"""
risk.py — risk classification + mitigation guidance.

Two responsibilities, both kept OUT of idp_extraction so the extraction schema
stays clean and this logic is independently auditable/testable:

1. classify_risk(obligation-like) -> (RiskLevel, RiskType)
   Deterministic, in-code (not model-guessed) so the same clause always yields
   the same risk classification and a reviewer can trace *why*. This is the
   separate risk dimension the stakeholder asked for — independent of priority.

2. mitigation_for(category, risk_type, obligation-like) -> Mitigation
   "What to do about it." A rules library keyed to (category / risk type)
   returns concrete, auditable recommended actions. When no rule matches, the
   caller can fall back to the model (see suggest_mitigation_via_model) — the
   "both: rules library with model fallback" design.

Everything here operates on a light structural view of an obligation (anything
with .category, .penalty, .trigger_type, .deadline, .responsible_party,
.confidence), so it works on both the Pydantic Obligation object and a plain
dict from the demo JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from idp_extraction import RiskLevel, RiskType, Category, TriggerType


# --------------------------------------------------------------------------- #
# Light accessor so this works on Obligation objects AND plain dicts
# --------------------------------------------------------------------------- #
def _get(ob, name, default=None):
    if isinstance(ob, dict):
        return ob.get(name, default)
    return getattr(ob, name, default)


def _as_str(value) -> str:
    """Enums -> their .value; everything else -> str. Safe for dict or model input."""
    if value is None:
        return ""
    return getattr(value, "value", value) if not isinstance(value, str) else value


_MONEY_HINTS = ("$", "usd", "fee", "penalt", "credit", "interest", "%", "per day",
                "per diem", "liquidated", "indemn", "damages")

# Which obligation category maps to which kind of risk, by default.
_CATEGORY_RISK_TYPE = {
    "Financial": RiskType.FINANCIAL,
    "Payment": RiskType.FINANCIAL,
    "Insurance": RiskType.FINANCIAL,
    "Indemnity & Liability": RiskType.LEGAL,
    "Compliance & Regulatory": RiskType.REGULATORY,
    "Service Level (SLA)": RiskType.OPERATIONAL,
    "Maintenance": RiskType.OPERATIONAL,
    "Reporting": RiskType.OPERATIONAL,
    "Confidentiality & Data": RiskType.REPUTATIONAL,
    "Notice": RiskType.CONTRACTUAL,
    "Term & Renewal": RiskType.CONTRACTUAL,
    "Termination": RiskType.CONTRACTUAL,
}


def classify_risk_type(ob) -> RiskType:
    cat = _as_str(_get(ob, "category"))
    return _CATEGORY_RISK_TYPE.get(cat, RiskType.OPERATIONAL if cat else RiskType.NONE)


# Monetary severity: a penalty is only "High" on financial grounds if its dollar
# amount meets this threshold. Tunable per deployment — a penalty-heavy lease
# where every clause carries a small fine shouldn't flag every clause as High.
# Penalties with no parseable amount stay High (unknown magnitude = treat as
# material until a human confirms).
import re as _re

HIGH_RISK_MONETARY_THRESHOLD = 10_000  # USD

_AMOUNT_RE = _re.compile(r"(?:usd|\$)\s*([\d][\d,]*(?:\.\d+)?)", _re.IGNORECASE)


def parse_penalty_amount(penalty: str):
    """Largest USD figure mentioned in a penalty string, or None if none parseable.
    Uses the max because clauses often list an escalating scale (e.g. '$500/day up
    to $5,000/month') — the ceiling is the true exposure."""
    if not penalty:
        return None
    amounts = []
    for m in _AMOUNT_RE.finditer(penalty):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return max(amounts) if amounts else None


def classify_risk_level(ob, monetary_threshold: float = HIGH_RISK_MONETARY_THRESHOLD) -> RiskLevel:
    """Derive risk level from exposure signals, independent of priority.

    High:   monetary exposure AT/ABOVE the threshold (or monetary with no
            parseable amount), OR legal (indemnity/liability), OR regulatory,
            OR an auto-renew/lapse contractual trap.
    Medium: a monetary penalty BELOW the threshold, OR a penalty that isn't
            clearly monetary, OR an operational obligation with a firm
            recurring/dated trigger, OR low model confidence on a firm-trigger item.
    Low:    everything else.

    `monetary_threshold` is tunable so a penalty-heavy contract (e.g. a lease
    where every clause carries a small fine) doesn't flag every clause as High.
    """
    penalty = _as_str(_get(ob, "penalty")).lower()
    has_penalty = bool(penalty.strip())
    monetary = has_penalty and any(h in penalty for h in _MONEY_HINTS)
    amount = parse_penalty_amount(penalty)
    # "Severe" money = amount meets threshold, OR monetary but amount unknown.
    severe_money = monetary and (amount is None or amount >= monetary_threshold)
    risk_type = classify_risk_type(ob)
    trigger = _as_str(_get(ob, "trigger_type"))
    firm_trigger = trigger in (TriggerType.SPECIFIC_DATE.value,
                               TriggerType.RECURRING.value,
                               TriggerType.EVENT_DRIVEN.value)
    desc = (_as_str(_get(ob, "description")) + " " + penalty).lower()
    auto_renew_trap = any(k in desc for k in ("auto-renew", "automatically renew",
                                              "auto renew", "lapse", "forfeit", "deemed accepted"))

    if severe_money or risk_type in (RiskType.LEGAL, RiskType.REGULATORY) or auto_renew_trap:
        return RiskLevel.HIGH
    if has_penalty or (risk_type == RiskType.OPERATIONAL and firm_trigger):
        return RiskLevel.MEDIUM

    # Uncertain extractions on anything with a real trigger are a review risk.
    try:
        conf = float(_get(ob, "confidence", 1.0) or 1.0)
    except (TypeError, ValueError):
        conf = 1.0
    if conf < 0.70 and firm_trigger:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def classify_risk(ob, monetary_threshold: float = HIGH_RISK_MONETARY_THRESHOLD) -> tuple:
    """Return (RiskLevel, RiskType) for an obligation-like object/dict."""
    return classify_risk_level(ob, monetary_threshold), classify_risk_type(ob)


# --------------------------------------------------------------------------- #
# Mitigation library
# --------------------------------------------------------------------------- #
@dataclass
class Mitigation:
    """What-to-do guidance for an obligation."""
    summary: str                                   # one-line "what this exposes you to"
    actions: List[str] = field(default_factory=list)  # concrete recommended steps
    source: str = "rules"                          # "rules" or "model" (provenance/auditability)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "actions": list(self.actions), "source": self.source}


# Keyed by risk type; category-specific overrides layered on top where useful.
# These are deliberately concrete and operational — a BPO analyst can act on
# them directly, and they're auditable (no model in the loop for the common case).
_MITIGATION_BY_RISK_TYPE = {
    RiskType.FINANCIAL: Mitigation(
        summary="Direct monetary exposure if this is missed.",
        actions=[
            "Add the amount and due date/cadence to the obligations tracker with an owner.",
            "Set a reminder ahead of each due date (lead time >= the notice/cure period).",
            "Reconcile against invoices/statements each cycle; flag variances immediately.",
        ],
    ),
    RiskType.LEGAL: Mitigation(
        summary="Legal/liability exposure (indemnity, damages, litigation).",
        actions=[
            "Route to Legal for review of scope and caps on liability.",
            "Confirm insurance coverage aligns with the indemnity obligation.",
            "Document the trigger conditions and required response timeline.",
        ],
    ),
    RiskType.REGULATORY: Mitigation(
        summary="Regulatory / compliance exposure (fines, enforcement).",
        actions=[
            "Assign to the compliance owner for the relevant regime (GDPR, fire code, etc.).",
            "Schedule the recurring certification/audit before each statutory deadline.",
            "Retain evidence of compliance (certificates, reports) for audit.",
        ],
    ),
    RiskType.OPERATIONAL: Mitigation(
        summary="Operational/service-delivery risk (SLA breach, missed report/maintenance).",
        actions=[
            "Put the cadence into the delivery team's workflow with a named owner.",
            "Track performance against the threshold; escalate before a breach becomes chargeable.",
        ],
    ),
    RiskType.REPUTATIONAL: Mitigation(
        summary="Confidentiality/reputational exposure on disclosure.",
        actions=[
            "Confirm data-handling controls and access restrictions are in place.",
            "Note the survival period so obligations aren't dropped after termination.",
        ],
    ),
    RiskType.CONTRACTUAL: Mitigation(
        summary="Contractual mechanics risk (auto-renewal, lapse, missed notice window).",
        actions=[
            "Calendar the notice window with a reminder set BEFORE it opens.",
            "Record the decision (renew / renegotiate / exit) ahead of the deadline.",
        ],
    ),
    RiskType.NONE: Mitigation(
        summary="No material risk identified.",
        actions=["Acknowledge and file; no active mitigation required."],
    ),
}

# Category-specific action to prepend when it adds real value beyond the type default.
_CATEGORY_EXTRA_ACTION = {
    "Insurance": "Verify the certificate of insurance is on file and renews before expiry.",
    "Term & Renewal": "Diary the renewal/non-renewal notice date with a buffer for approvals.",
    "Termination": "Pre-stage the termination checklist (data return, transition, final invoicing).",
    "Confidentiality & Data": "Confirm the confidentiality survival clause is tracked post-termination.",
    "Payment": "Automate the payment/reminder so a missed cycle can't incur late charges.",
}


def mitigation_for(ob, risk_type: Optional[RiskType] = None) -> Mitigation:
    """Rules-based mitigation for an obligation. Deterministic and auditable.
    Returns a Mitigation with source='rules'. Never raises."""
    rtype = risk_type or classify_risk_type(ob)
    base = _MITIGATION_BY_RISK_TYPE.get(rtype, _MITIGATION_BY_RISK_TYPE[RiskType.NONE])
    actions = list(base.actions)

    cat = _as_str(_get(ob, "category"))
    extra = _CATEGORY_EXTRA_ACTION.get(cat)
    if extra and extra not in actions:
        actions = [extra] + actions

    return Mitigation(summary=base.summary, actions=actions, source="rules")


def has_specific_rule(ob) -> bool:
    """True when the rules library has category-specific guidance for this
    obligation (i.e. more than the generic risk-type default). Used to decide
    whether the model fallback is worth calling."""
    return _as_str(_get(ob, "category")) in _CATEGORY_EXTRA_ACTION


# --------------------------------------------------------------------------- #
# Model fallback (the "both" in "rules library with model fallback")
# --------------------------------------------------------------------------- #
MITIGATION_SYSTEM_PROMPT = (
    "You are a JLL contract risk advisor. Given one contractual obligation, provide "
    "brief, concrete risk-mitigation guidance: a one-line risk summary and 2-4 specific, "
    "actionable steps an operations analyst can take. Be practical and specific to the "
    "obligation. Return ONLY JSON: {\"summary\": \"...\", \"actions\": [\"...\", \"...\"]}."
)


def suggest_mitigation_via_model(ob, provider) -> Mitigation:
    """Ask the model for mitigation guidance when the rules library has no
    category-specific rule. Falls back to the rules default if the model call
    fails or returns nothing — this is advisory, never fatal.

    `provider` is any ModelProvider (see providers.py)."""
    import json
    rules_default = mitigation_for(ob)
    desc = _as_str(_get(ob, "description"))
    penalty = _as_str(_get(ob, "penalty"))
    cat = _as_str(_get(ob, "category"))
    user = (f"Obligation: {desc}\nCategory: {cat}\nPenalty if missed: {penalty or 'none stated'}\n"
            f"Provide mitigation guidance.")
    try:
        raw = provider.extract(MITIGATION_SYSTEM_PROMPT, user, {"type": "object"})
        # Providers return a list of dicts (obligations tool) OR we accept a dict;
        # normalize both shapes.
        payload = raw[0] if isinstance(raw, list) and raw else raw
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict) and payload.get("summary"):
            actions = payload.get("actions") or rules_default.actions
            return Mitigation(summary=payload["summary"], actions=list(actions), source="model")
    except Exception:
        pass
    return rules_default


def enrich_dict(ob: dict) -> dict:
    """Add risk_level, risk_type, and mitigation to a plain obligation dict
    (e.g. loaded from demo JSON) if not already present. Idempotent — returns
    the same dict, mutated. This lets demo data and live-pipeline output carry
    the exact same risk fields without regenerating the JSON files."""
    if "risk_level" not in ob or "risk_type" not in ob:
        lvl, typ = classify_risk(ob)
        ob["risk_level"] = lvl.value
        ob["risk_type"] = typ.value
    if not ob.get("mitigation"):
        rtype = RiskType(ob["risk_type"]) if ob.get("risk_type") else classify_risk_type(ob)
        ob["mitigation"] = mitigation_for(ob, rtype).to_dict()
    return ob


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("=== risk.py self-test ===\n")

    # Financial penalty below threshold -> Medium; large penalty -> High.
    ob_fin = {"category": "Payment", "penalty": "USD 200/day late", "trigger_type": "Recurring",
              "description": "Pay monthly rent", "confidence": 0.95}
    lvl, typ = classify_risk(ob_fin)
    assert lvl == RiskLevel.MEDIUM and typ == RiskType.FINANCIAL, (lvl, typ)
    m = mitigation_for(ob_fin)
    assert m.source == "rules" and any("payment" in a.lower() or "reminder" in a.lower() for a in m.actions)
    print(f"Payment w/ small fine -> {lvl.value}/{typ.value}; {len(m.actions)} actions: OK")

    ob_fin_big = {"category": "Payment", "penalty": "Liquidated damages of USD $324,000.00",
                  "trigger_type": "Event-driven", "description": "Sublease breach", "confidence": 0.95}
    assert classify_risk(ob_fin_big)[0] == RiskLevel.HIGH
    print("Payment w/ large penalty -> High: OK")

    # Indemnity -> High / Legal
    ob_leg = {"category": "Indemnity & Liability", "penalty": "Full indemnification",
              "trigger_type": "Event-driven", "description": "Indemnify client", "confidence": 0.96}
    lvl, typ = classify_risk(ob_leg)
    assert lvl == RiskLevel.HIGH and typ == RiskType.LEGAL, (lvl, typ)
    print(f"Indemnity -> {lvl.value}/{typ.value}: OK")

    # Auto-renew trap with no penalty -> still High / Contractual
    ob_renew = {"category": "Term & Renewal", "penalty": None, "trigger_type": "Specific date",
                "description": "Agreement automatically renews unless 90 days notice given",
                "confidence": 0.9}
    lvl, typ = classify_risk(ob_renew)
    assert lvl == RiskLevel.HIGH and typ == RiskType.CONTRACTUAL, (lvl, typ)
    print(f"Auto-renew trap -> {lvl.value}/{typ.value}: OK")

    # Low-stakes boilerplate -> Low
    ob_low = {"category": "Notice", "penalty": None, "trigger_type": "Conditional",
              "description": "Notices delivered by certified mail", "confidence": 0.9}
    lvl, typ = classify_risk(ob_low)
    assert lvl == RiskLevel.LOW, (lvl, typ)
    print(f"Boilerplate notice -> {lvl.value}/{typ.value}: OK")

    # Monetary severity threshold: a small fine is Medium, a large one is High.
    assert parse_penalty_amount("Multa de USD $500.00 por mes") == 500.0
    assert parse_penalty_amount("$500/day up to $5,000 per month") == 5000.0
    assert parse_penalty_amount("injunctive relief") is None
    ob_small = {"category": "Reporting", "penalty": "Multa de USD $500.00 por reporte",
                "trigger_type": "Recurring", "description": "Monthly occupancy report",
                "confidence": 0.95}
    ob_big = {"category": "Reporting", "penalty": "Penalty of USD $50,000.00",
              "trigger_type": "Recurring", "description": "Monthly occupancy report",
              "confidence": 0.95}
    assert classify_risk_level(ob_small) == RiskLevel.MEDIUM, "small fine should be Medium"
    assert classify_risk_level(ob_big) == RiskLevel.HIGH, "large fine should be High"
    # Monetary but no parseable amount -> stays High (unknown magnitude)
    ob_unknown = {"category": "Financial", "penalty": "interest accrues on overdue amounts",
                  "trigger_type": "Recurring", "description": "Late payment interest",
                  "confidence": 0.95}
    assert classify_risk_level(ob_unknown) == RiskLevel.HIGH, "unknown-amount money stays High"
    print("Monetary severity threshold + amount parsing: OK")

    # Model fallback: category with no specific rule, mock provider returns guidance
    class MockProvider:
        def extract(self, sp, up, schema):
            return [{"summary": "Model-suggested risk.", "actions": ["Do X", "Do Y"]}]
    ob_generic = {"category": "Reporting", "penalty": None, "trigger_type": "Recurring",
                  "description": "Submit occupancy report", "confidence": 0.9}
    m2 = suggest_mitigation_via_model(ob_generic, MockProvider())
    assert m2.source == "model" and m2.actions == ["Do X", "Do Y"], m2
    print(f"Model fallback -> source={m2.source}, {len(m2.actions)} actions: OK")

    # Model fallback failure -> graceful rules default
    class BrokenProvider:
        def extract(self, sp, up, schema):
            raise RuntimeError("model down")
    m3 = suggest_mitigation_via_model(ob_generic, BrokenProvider())
    assert m3.source == "rules", m3
    print("Model fallback failure -> rules default: OK")

    print("\nAll risk self-tests passed.")
