"""
IDP Agent — Obligation Extraction Core
======================================
Pydantic schema + extraction prompt + per-clause extraction (via a ModelProvider).

The model call is delegated to a provider (see providers.py), so this module is
endpoint-agnostic: sandbox Claude for the hackathon, a JLL-sanctioned endpoint for
production — same schema, same validation, same priority logic.

Pipeline position
-----------------
    parse -> chunk -> [THIS MODULE per chunk] -> reduce/dedup -> checklist -> export

Quick start
-----------
    pip install anthropic pydantic
    export ANTHROPIC_API_KEY=sk-ant-...        # sandbox / synthetic contracts only
    python idp_extraction.py

Author: Daniel Salas Castro — JLL Hackathon 2026
"""

from __future__ import annotations

import json
import os
from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

REVIEW_CONFIDENCE_THRESHOLD = 0.70  # items below this get flagged for human review


# ---------------------------------------------------------------------------
# Enums  (the shared vocabulary — keep in sync with the design doc taxonomy)
# ---------------------------------------------------------------------------
class Category(str, Enum):
    # Shared / MSA-oriented
    FINANCIAL = "Financial"
    INSURANCE = "Insurance"
    REPORTING = "Reporting"
    SLA = "Service Level (SLA)"
    COMPLIANCE = "Compliance & Regulatory"
    NOTICE = "Notice"
    TERM_RENEWAL = "Term & Renewal"
    TERMINATION = "Termination"
    INDEMNITY = "Indemnity & Liability"
    CONFIDENTIALITY = "Confidentiality & Data"
    # Lease Administration additions (see DOCUMENT_PROFILES below)
    PAYMENT = "Payment"                # rent / canon — distinct from general Financial
    MAINTENANCE = "Maintenance"        # HVAC, electrical, fire-system upkeep, etc.


class Party(str, Enum):
    JLL = "JLL"
    CLIENT = "Client"
    VENDOR = "Vendor"
    BOTH = "Both"
    # Lease Administration additions
    LANDLORD = "Landlord"
    TENANT = "Tenant"


# Spanish-language party terms seen in real lease contracts (e.g. Costa Rica
# lease demo data) normalize to the Party enum so the schema stays in English
# regardless of the source contract's language.
PARTY_SYNONYMS = {
    "arrendatario": Party.TENANT,
    "arrendador": Party.LANDLORD,
    "ambas partes": Party.BOTH,
    "both parties": Party.BOTH,
    "tenant": Party.TENANT,
    "landlord": Party.LANDLORD,
}


def normalize_party(value: str) -> str:
    """Map any known synonym (incl. Spanish lease terms) to the canonical Party value.
    Unknown values pass through unchanged — the pydantic Party field will then raise
    a clear validation error rather than silently coercing something unexpected."""
    key = (value or "").strip().lower()
    return PARTY_SYNONYMS.get(key, value)


class TriggerType(str, Enum):
    SPECIFIC_DATE = "Specific date"
    RECURRING = "Recurring"
    EVENT_DRIVEN = "Event-driven"
    CONDITIONAL = "Conditional"


class Priority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# ---------------------------------------------------------------------------
# Risk dimension — SEPARATE from priority.
#
# Priority answers "how urgently must someone act on this obligation?"
# (penalty severity + how firm the trigger is). Risk answers "what kind of
# exposure does this create for JLL, and how bad if it goes wrong?" The two
# correlate but are not the same: a low-dollar recurring report can be low
# priority yet, if missed repeatedly, a compliance/regulatory risk; a one-off
# indemnity with no deadline can be low priority but a severe liability risk.
#
# Keeping risk as its own dimension lets the tool surface a dedicated high-risk
# view without disturbing the existing priority-driven checklist ordering.
# ---------------------------------------------------------------------------
class RiskLevel(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class RiskType(str, Enum):
    FINANCIAL = "Financial"          # direct monetary exposure (penalties, fees, credits)
    LEGAL = "Legal"                  # indemnities, liability, litigation exposure
    REGULATORY = "Regulatory"        # compliance with laws/regs (GDPR, fire code, etc.)
    OPERATIONAL = "Operational"      # SLAs, maintenance, service-delivery failures
    REPUTATIONAL = "Reputational"    # confidentiality breaches, disclosure
    CONTRACTUAL = "Contractual"      # renewal/termination/notice mechanics (auto-renew, lapse)
    NONE = "None"                    # no material risk identified


# ---------------------------------------------------------------------------
# Document profiles — the "scalable to new document types" seam.
#
# The pipeline (parse -> chunk -> extract -> reduce -> checklist -> export) is
# identical for every document type. What differs per type is: which
# obligation categories are relevant, and how the extraction prompt should be
# worded. A DocumentProfile captures exactly that, and nothing else — adding a
# new document type (e.g. NDAs, SOWs) means adding one profile here, not
# touching the rest of the pipeline.
# ---------------------------------------------------------------------------
class DocumentProfile(BaseModel):
    key: str
    label: str
    description: str
    categories: List[Category]
    prompt_focus: str  # inserted into SYSTEM_PROMPT to steer extraction toward this doc type


MSA_PROFILE = DocumentProfile(
    key="msa",
    label="Master Service Agreement",
    description="Vendor/services contracts — SLAs, service fees, indemnities, data handling.",
    categories=[
        Category.FINANCIAL, Category.INSURANCE, Category.REPORTING, Category.SLA,
        Category.COMPLIANCE, Category.NOTICE, Category.TERM_RENEWAL, Category.TERMINATION,
        Category.INDEMNITY, Category.CONFIDENTIALITY,
    ],
    prompt_focus=(
        "This is a Master Service Agreement between a service provider and a client. "
        "Focus on service-delivery obligations: SLAs and service credits, invoicing and "
        "payment terms, insurance coverage, reporting cadence, indemnities, confidentiality, "
        "and term/renewal/termination mechanics."
    ),
)

LEASE_PROFILE = DocumentProfile(
    key="lease",
    label="Lease Administration",
    description="Commercial leases — rent, escalations, CAM, maintenance, renewal/break options.",
    categories=[
        Category.PAYMENT, Category.FINANCIAL, Category.INSURANCE, Category.REPORTING,
        Category.COMPLIANCE, Category.MAINTENANCE, Category.NOTICE, Category.TERM_RENEWAL,
        Category.TERMINATION, Category.CONFIDENTIALITY, Category.INDEMNITY,
    ],
    prompt_focus=(
        "This is a commercial LEASE agreement between a landlord and a tenant. Focus on "
        "lease-specific obligations: base rent and any escalations, security deposits, "
        "operating expenses / CAM charges, insurance coverage the tenant must carry, "
        "maintenance duties (HVAC, electrical, fire-safety inspections/certifications), "
        "utilities and financial reporting to the landlord, renewal/extension/break options, "
        "assignment and subleasing restrictions, and notice/critical-date requirements. "
        "Contracts may be in Spanish or English — extract in English, but keep "
        "verbatim_snippet in the ORIGINAL language exactly as written in the clause."
    ),
)

DOCUMENT_PROFILES: dict = {p.key: p for p in (MSA_PROFILE, LEASE_PROFILE)}


def get_profile(key: str = "msa") -> DocumentProfile:
    key = (key or "msa").lower().strip()
    if key not in DOCUMENT_PROFILES:
        raise ValueError(f"Unknown document profile '{key}'. Options: {list(DOCUMENT_PROFILES)}")
    return DOCUMENT_PROFILES[key]


# ---------------------------------------------------------------------------
# Input model — one clause chunk produced by the parser/chunker
# ---------------------------------------------------------------------------
class ClauseChunk(BaseModel):
    """A structure-aware unit of the MSA. The metadata becomes the citation."""
    section_id: str = Field(..., description="e.g. '8.3', 'Schedule C', 'Exhibit 2'")
    heading: Optional[str] = Field(None, description="Clause/section heading if available")
    page_range: str = Field(..., description="e.g. '142' or '142-144'")
    text: str = Field(..., description="Raw text of this clause")


# ---------------------------------------------------------------------------
# Output model — what the model returns PER obligation
# (obligation_id and priority are derived later, NOT extracted by the model)
# ---------------------------------------------------------------------------
class ExtractedObligation(BaseModel):
    description: str = Field(..., description="Plain-language statement of what must be done.")
    category: Category
    responsible_party: Party
    trigger_type: TriggerType
    deadline: Optional[date] = Field(None, description="Absolute due date (YYYY-MM-DD) if stated.")
    frequency: Optional[str] = Field(None, description="Cadence for recurring obligations.")
    penalty: Optional[str] = Field(None, description="Consequence of non-compliance, incl. amount.")
    verbatim_snippet: str = Field(
        ...,
        description="EXACT text copied from the clause that supports this obligation. "
                    "Required — an obligation with no supporting text must not be returned.",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Model confidence this is a genuine, correctly-parsed obligation.",
    )

    @field_validator("verbatim_snippet")
    @classmethod
    def snippet_must_be_substantive(cls, v: str) -> str:
        if not v or len(v.strip()) < 8:
            raise ValueError("verbatim_snippet too short to be a real citation")
        return v.strip()

    @field_validator("responsible_party", mode="before")
    @classmethod
    def normalize_party_synonyms(cls, v):
        """Accept Spanish lease-contract party terms (Arrendatario/Arrendador/etc.)
        and map them to the canonical Party enum before validation."""
        if isinstance(v, str):
            return normalize_party(v)
        return v


# ---------------------------------------------------------------------------
# Finalized record — enriched after extraction (id + source + priority)
# ---------------------------------------------------------------------------
class Obligation(ExtractedObligation):
    obligation_id: str
    source_section: str
    source_page: str
    priority: Priority
    needs_review: bool
    # Separate risk dimension (independent of priority) + mitigation guidance.
    risk_level: RiskLevel = RiskLevel.LOW
    risk_type: RiskType = RiskType.NONE
    mitigation: Optional[dict] = None      # {"summary", "actions", "source"} — see risk.py

    @classmethod
    def from_extracted(cls, ext: ExtractedObligation, *, obligation_id: str, chunk: ClauseChunk) -> "Obligation":
        ob = cls(
            obligation_id=obligation_id,
            source_section=chunk.section_id,
            source_page=chunk.page_range,
            priority=derive_priority(ext),
            needs_review=ext.confidence < REVIEW_CONFIDENCE_THRESHOLD,
            **ext.model_dump(),
        )
        # Classify risk + attach rules-based mitigation. Imported lazily to avoid
        # a circular import (risk.py imports the enums from this module).
        from risk import classify_risk, mitigation_for
        ob.risk_level, ob.risk_type = classify_risk(ob)
        ob.mitigation = mitigation_for(ob, ob.risk_type).to_dict()
        return ob


# ---------------------------------------------------------------------------
# Priority derivation  (in code, not by the model — keeps it consistent)
# ---------------------------------------------------------------------------
_MONEY_HINTS = ("$", "usd", "fee", "penalt", "credit", "interest", "%", "per day", "per diem")


def derive_priority(ext: ExtractedObligation) -> Priority:
    has_penalty = bool(ext.penalty and ext.penalty.strip())
    monetary = has_penalty and any(h in ext.penalty.lower() for h in _MONEY_HINTS)
    firm_trigger = ext.trigger_type in (
        TriggerType.SPECIFIC_DATE, TriggerType.RECURRING, TriggerType.EVENT_DRIVEN
    )
    if monetary and firm_trigger:
        return Priority.HIGH
    if has_penalty or (firm_trigger and ext.deadline is not None):
        return Priority.MEDIUM
    return Priority.LOW


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = """You are a contract analyst for JLL's Commercial Real Estate team. \
You extract obligations from one clause of a {doc_label}.

{profile_focus}

An OBLIGATION is any binding requirement a party must perform or satisfy: payments, \
insurance coverage, reports, service levels, notices, renewals, terminations, \
indemnities, confidentiality duties, and similar commitments.

Rules:
1. Extract EVERY obligation in the clause. A single clause may contain several, or none.
2. For each obligation, copy the EXACT supporting text into verbatim_snippet. \
Never paraphrase the snippet. If you cannot point to supporting text, do not return the item.
3. Do NOT invent obligations. If the clause is purely definitional, recital, or \
boilerplate with no binding requirement, return an empty list.
4. Capture penalties and amounts whenever the clause states a consequence of non-compliance.
5. Assign responsible_party from the clause's perspective ({party_options}).
6. Set confidence honestly — lower it when the clause is ambiguous or you are inferring.
7. Use ONLY these categories: {category_options}.
8. Return results ONLY by calling the record_obligations tool."""


def build_system_prompt(profile: "DocumentProfile") -> str:
    """Assemble the extraction system prompt for a given document profile (MSA, Lease, ...).
    This is the ONLY thing that changes to support a new document type — the schema,
    validation, dedup, checklist, and export are all identical across profiles."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        doc_label=profile.label,
        profile_focus=profile.prompt_focus,
        party_options=", ".join(p.value for p in Party),
        category_options=", ".join(c.value for c in profile.categories),
    )


# Backwards-compatible default (MSA) — existing callers that import SYSTEM_PROMPT
# directly keep working unchanged.
SYSTEM_PROMPT = build_system_prompt(MSA_PROFILE)


def build_user_prompt(chunk: ClauseChunk) -> str:
    heading = f" — {chunk.heading}" if chunk.heading else ""
    return (
        f"Clause {chunk.section_id}{heading} (page {chunk.page_range}):\n\n"
        f'"""\n{chunk.text}\n"""\n\n'
        f"Extract all obligations from this clause."
    )


# ---------------------------------------------------------------------------
# Extraction (delegates the model call to a ModelProvider)
# ---------------------------------------------------------------------------
def _provider_model(provider) -> str:
    """Best-effort model id for pricing/cache keys. Providers may expose `.model`
    (ClaudeProvider does); otherwise fall back to the provider name."""
    return getattr(provider, "model", None) or getattr(provider, "name", "unknown")


def extract_obligations_from_chunk(
    chunk: ClauseChunk,
    provider,
    profile: "DocumentProfile" = MSA_PROFILE,
    cache=None,
    run_stats=None,
) -> List["Obligation"]:
    """Map step: one clause -> validated obligations, via the given provider.

    `profile` selects the taxonomy + prompt focus (MSA, Lease, ...).
    `cache`, if given, short-circuits the model call when this exact clause +
    prompt + model + schema has been seen before (content-addressed) — a cache
    hit costs $0 and ~0ms.
    `run_stats`, if given, records tokens / cost / latency / cache-hit per call
    so the UI and KPI layer can report real numbers.
    Malformed items are skipped, not allowed to poison the run."""
    import time as _time
    from cache import make_key
    from telemetry import CallRecord, extract_usage

    system_prompt = build_system_prompt(profile)
    user_prompt = build_user_prompt(chunk)
    schema = ExtractedObligation.model_json_schema()
    model = _provider_model(provider)

    # ---- cache lookup ----
    key = None
    if cache is not None:
        key = make_key(chunk.text, system_prompt, model, schema)
        hit = cache.get(key)
        if hit is not None:
            if run_stats is not None:
                run_stats.record(CallRecord(chunk.section_id, model, cached=True))
            return _validate(hit, chunk)

    # ---- model call (with timing + usage capture) ----
    started = _time.time()
    error = None
    raw_items: list = []
    try:
        # If the provider exposes extract_with_response (returns (items, response)),
        # use it so we can read token usage; otherwise fall back to plain extract().
        if hasattr(provider, "extract_with_response"):
            raw_items, response = provider.extract_with_response(system_prompt, user_prompt, schema)
            in_tok, out_tok = extract_usage(response)
        else:
            raw_items = provider.extract(system_prompt, user_prompt, schema)
            in_tok, out_tok = extract_usage(getattr(provider, "last_response", None))
    except Exception as e:
        error = str(e)
        in_tok = out_tok = 0
        raise
    finally:
        if run_stats is not None:
            run_stats.record(CallRecord(
                chunk.section_id, model,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_s=_time.time() - started, error=error,
            ))

    if cache is not None and key is not None:
        cache.set(key, raw_items)

    return _validate(raw_items, chunk)


def _validate(raw_items: list, chunk: ClauseChunk) -> List["Obligation"]:
    out: List[Obligation] = []
    for i, item in enumerate(raw_items):
        try:
            ext = ExtractedObligation.model_validate(item)
        except ValidationError as e:
            print(f"  [skip] clause {chunk.section_id} item {i}: {e.error_count()} error(s)")
            continue
        out.append(Obligation.from_extracted(ext, obligation_id=f"{chunk.section_id}-{i + 1}", chunk=chunk))
    return out


def extract_all(
    chunks: List[ClauseChunk],
    provider,
    profile: "DocumentProfile" = MSA_PROFILE,
    max_workers: int = 5,
    progress_cb=None,
    cache=None,
    run_stats=None,
) -> List["Obligation"]:
    """Run extraction across many chunks, in parallel.

    A 2,000-page MSA can be a few hundred clause chunks; running them sequentially
    (one network round-trip at a time) is the throughput bottleneck the design doc
    flags for production. Each clause is an independent map step with no shared
    state, so a bounded thread pool gives a large wall-clock speedup with no change
    to correctness — set max_workers=1 to fall back to the original sequential
    behavior (e.g. for deterministic test runs).

    `cache` (see cache.py) short-circuits already-seen clauses for $0.
    `run_stats` (see telemetry.py) accumulates tokens / cost / latency per call;
    it is mutated in place and also returned via extract_all's caller when needed.

    progress_cb(done, total), if given, is called after each chunk completes —
    lets a UI progress bar advance smoothly instead of jumping in three big steps.
    """
    def _one(chunk):
        return extract_obligations_from_chunk(chunk, provider, profile,
                                              cache=cache, run_stats=run_stats)

    if max_workers <= 1:
        results: List[Obligation] = []
        for n, chunk in enumerate(chunks, 1):
            found = _one(chunk)
            results.extend(found)
            print(f"[{n}/{len(chunks)}] {chunk.section_id}: {len(found)} obligation(s)")
            if progress_cb:
                progress_cb(n, len(chunks))
        results.sort(key=lambda o: o.obligation_id)
        return results

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: List[Obligation] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            try:
                found = future.result()
            except Exception as e:
                print(f"  [error] clause {chunk.section_id} failed: {e}")
                found = []
            results.extend(found)
            done += 1
            print(f"[{done}/{len(chunks)}] {chunk.section_id}: {len(found)} obligation(s)")
            if progress_cb:
                progress_cb(done, len(chunks))

    # Thread completion order is nondeterministic; restore a stable, readable
    # order (by section, then by the per-chunk obligation index) before reduce/sort.
    results.sort(key=lambda o: o.obligation_id)
    return results


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
SAMPLE_CHUNK = ClauseChunk(
    section_id="8.3",
    heading="Insurance Requirements",
    page_range="142",
    text=(
        "Service Provider shall maintain Commercial General Liability insurance with "
        "limits of not less than $5,000,000 per occurrence throughout the Term, and "
        "shall furnish Client with a certificate of insurance evidencing such coverage "
        "within ten (10) business days of the Effective Date and upon each renewal. "
        "Failure to maintain the required coverage shall entitle Client to assess a "
        "penalty of $1,000 for each day the coverage lapses."
    ),
)


def _demo_offline():
    print("No ANTHROPIC_API_KEY found — running OFFLINE schema/validation check.\n")
    sample = {
        "description": "Maintain Commercial General Liability insurance of at least "
                       "$5,000,000 per occurrence for the full Term.",
        "category": "Insurance", "responsible_party": "Vendor", "trigger_type": "Recurring",
        "deadline": None, "frequency": "continuous / each renewal",
        "penalty": "$1,000 per day the coverage lapses",
        "verbatim_snippet": "shall maintain Commercial General Liability insurance with "
                            "limits of not less than $5,000,000 per occurrence",
        "confidence": 0.95,
    }
    ext = ExtractedObligation.model_validate(sample)
    ob = Obligation.from_extracted(ext, obligation_id="8.3-1", chunk=SAMPLE_CHUNK)
    print(json.dumps(ob.model_dump(mode="json"), indent=2))
    print(f"\nDerived priority: {ob.priority.value}   needs_review: {ob.needs_review}")


def _demo_live():
    from providers import ClaudeProvider, assert_data_allowed
    provider = ClaudeProvider()
    # SANDBOX guardrail: this sample is synthetic, so real-data flag is False.
    assert_data_allowed(provider, contains_real_client_data=False)
    print(f"Calling provider '{provider.name}' on the sample clause...\n")
    for ob in extract_obligations_from_chunk(SAMPLE_CHUNK, provider):
        print(json.dumps(ob.model_dump(mode="json"), indent=2)); print("-" * 60)


if __name__ == "__main__":
    if os.getenv("ANTHROPIC_API_KEY"):
        _demo_live()
    else:
        _demo_offline()
