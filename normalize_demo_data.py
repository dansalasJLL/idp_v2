"""
normalize_demo_data.py — one-time migration script.

Converts demo_obligations_us.json, demo_obligations_cr.json, and
demo_obligations_planetorg.json (legacy schema: source_clause / page_number /
due_date / penalty_if_missed) to the canonical Obligation schema used by
demo_obligations.json and by the live pipeline (source_section / source_page /
deadline / frequency / trigger_type / penalty / verbatim_snippet).

This directly resolves the schema-drift item flagged in the project handover
(section 4): one schema, everywhere, so app.py no longer needs dual-key
fallbacks and any new demo dataset "just works" without special-casing.

Run once: python normalize_demo_data.py
"""
import json
import re

RECURRING_HINTS = [
    "cada mes", "mensual", "cada año", "anual", "semestral", "cada trimestre",
    "trimestral", "each month", "monthly", "each year", "annual", "quarterly",
    "semi-annual", "every month", "every year",
]
SPECIFIC_DATE_HINTS = [
    "firma", "signing", "commencement", "vencimiento", "expiration", "entrega",
]

# Matches a leading clause/section label at the start of source_clause, e.g.
# "CLAUSULA CUARTA:", "SECTION 3.1:", "ARTICLE 7 —"
LABEL_RE = re.compile(
    r"^\s*((?:CLAUSULA|CL[ÁA]USULA|SECTION|ARTICLE|ARTICULO|ART[ÍI]CULO)\s+[A-Z0-9ÁÉÍÓÚñÑ]+(?:\.\d+)*)",
    re.IGNORECASE,
)


def split_source_clause(source_clause: str):
    """Return (section_id, verbatim_snippet) from a combined 'CLAUSULA X: text...' string."""
    m = LABEL_RE.match(source_clause or "")
    if m:
        section_id = m.group(1).strip().rstrip(":")
        return section_id, source_clause.strip()
    # Fallback: no recognizable label — use the first few words as a pseudo-id
    words = (source_clause or "").split()
    section_id = " ".join(words[:3]) if words else "N/A"
    return section_id, source_clause.strip()


def infer_trigger_type(due_date: str) -> str:
    text = (due_date or "").lower()
    if any(h in text for h in RECURRING_HINTS):
        return "Recurring"
    if any(h in text for h in SPECIFIC_DATE_HINTS):
        return "Specific date"
    return "Event-driven"


def normalize_item(item: dict) -> dict:
    section_id, verbatim = split_source_clause(item.get("source_clause", ""))
    due_date = item.get("due_date")
    trigger_type = infer_trigger_type(due_date)

    out = {
        "obligation_id": item["obligation_id"],
        "source_section": section_id,
        "source_page": str(item.get("page_number", "N/A")),
        "description": item["description"],
        "category": item["category"],
        "responsible_party": item["responsible_party"],
        "trigger_type": trigger_type,
        # Recurring obligations: the due_date text IS the cadence -> frequency.
        # Everything else: it's a one-off/conditional deadline description.
        "deadline": None if trigger_type == "Recurring" else due_date,
        "frequency": due_date if trigger_type == "Recurring" else None,
        "penalty": item.get("penalty_if_missed"),
        "verbatim_snippet": verbatim,
        "confidence": item["confidence"],
        "priority": item["priority"],
        "needs_review": item.get("needs_review", False),
    }
    return out


def normalize_file(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("obligations", data)
    normalized = [normalize_item(it) for it in items]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    print(f"[normalize] {path}: {len(normalized)} items -> canonical schema")


if __name__ == "__main__":
    for fname in ("demo_obligations_us.json", "demo_obligations_cr.json", "demo_obligations_planetorg.json"):
        normalize_file(fname)
    print("\nDone. All demo datasets now share the same schema as demo_obligations.json.")
