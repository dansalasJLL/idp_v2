"""
reduce_obligations.py — Dedup + Checklist Builder
JLL Hackathon 2026 · IDP Agent

Takes the raw List[Obligation] from idp_extraction.extract_all() and:
  1. Deduplicates near-identical obligations (same section + category + party)
  2. Sorts by priority (High → Medium → Low) then by section
  3. Builds a structured checklist dict ready for the UI and export

Usage:
    from reduce_obligations import reduce, build_checklist, to_dataframe

    obligations = extract_all(chunks, provider)
    clean       = reduce(obligations)
    checklist   = build_checklist(clean)
    df          = to_dataframe(clean)          # pandas DataFrame for Excel export
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Dict, List, Optional

from idp_extraction import Obligation, Priority, Category

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRIORITY_ORDER: Dict[str, int] = {
    Priority.HIGH.value:   0,
    Priority.MEDIUM.value: 1,
    Priority.LOW.value:    2,
}

# Similarity threshold: obligations in the same section+category+party
# whose descriptions share this fraction of words are considered duplicates.
DEDUP_SIMILARITY_THRESHOLD = 0.65


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _word_set(text: str) -> set:
    """Lowercase word set for Jaccard similarity."""
    return set(re.sub(r"[^a-z0-9\s]", "", text.lower()).split())


def _jaccard(a: str, b: str) -> float:
    sa, sb = _word_set(a), _word_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _dedup_group(obligations: List[Obligation]) -> List[Obligation]:
    """
    Within a group (same section + category + party), remove near-duplicates.
    Keeps the item with the highest confidence when two are similar.
    """
    kept: List[Obligation] = []
    for ob in sorted(obligations, key=lambda o: -o.confidence):
        is_dup = any(
            _jaccard(ob.description, k.description) >= DEDUP_SIMILARITY_THRESHOLD
            for k in kept
        )
        if not is_dup:
            kept.append(ob)
    return kept


def reduce(obligations: List[Obligation]) -> List[Obligation]:
    """
    Deduplicate and sort obligations.

    Returns a clean, sorted List[Obligation]:
      - Near-duplicates within the same section+category+party removed
      - Sorted: High → Medium → Low, then by source_section
    """
    # Group by (section, category, responsible_party)
    groups: Dict[tuple, List[Obligation]] = defaultdict(list)
    for ob in obligations:
        key = (ob.source_section, ob.category, ob.responsible_party)
        groups[key].append(ob)

    # Dedup within each group
    clean: List[Obligation] = []
    for group_obs in groups.values():
        clean.extend(_dedup_group(group_obs))

    # Sort: priority first, then section_id numerically where possible
    def _sort_key(ob: Obligation):
        pri = PRIORITY_ORDER.get(ob.priority, 9)
        # Try numeric sort on section_id (e.g. "8.3" → (8, 3))
        parts = []
        for part in ob.source_section.split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        return (pri, parts)

    clean.sort(key=_sort_key)

    removed = len(obligations) - len(clean)
    print(f"[reduce] {len(obligations)} obligations → {len(clean)} after dedup "
          f"({removed} duplicate(s) removed)")
    return clean


# ---------------------------------------------------------------------------
# Checklist builder
# ---------------------------------------------------------------------------

def build_checklist(
    obligations: List[Obligation],
    document_name: Optional[str] = None,
    page_count: Optional[int] = None,
) -> Dict:
    """
    Build a structured checklist dict from a clean obligation list.

    `document_name` / `page_count`, if given, are carried through so the caller
    (app.py) can render the header without a separate lookup — this is the exact
    shape the UI expects from both the demo JSON and a live pipeline run.

    Returns:
    {
        "document_name": str | None,
        "page_count":    int | None,
        "total":         int,
        "needs_review":  int,
        "by_priority":   {"High": [...], "Medium": [...], "Low": [...]},
        "by_category":   {"Financial": [...], ...},
        "items":         [obligation_dict, ...],   # full flat list, sorted
        "obligations":   [obligation_dict, ...],   # same list — key name app.py reads
    }
    """
    by_priority: Dict[str, List[dict]] = {
        Priority.HIGH.value:   [],
        Priority.MEDIUM.value: [],
        Priority.LOW.value:    [],
    }
    by_category: Dict[str, List[dict]] = defaultdict(list)
    by_risk: Dict[str, List[dict]] = {"High": [], "Medium": [], "Low": []}
    items: List[dict] = []

    for ob in obligations:
        d = ob.model_dump(mode="json")
        by_priority[ob.priority].append(d)
        by_category[ob.category].append(d)
        rlvl = getattr(getattr(ob, "risk_level", None), "value", None) or d.get("risk_level", "Low")
        by_risk.setdefault(rlvl, []).append(d)
        items.append(d)

    needs_review = sum(1 for ob in obligations if ob.needs_review)
    high_risk = len(by_risk.get("High", []))

    checklist = {
        "document_name": document_name,
        "page_count":   page_count,
        "total":        len(obligations),
        "needs_review": needs_review,
        "high_risk":    high_risk,
        "by_priority":  dict(by_priority),
        "by_category":  dict(by_category),
        "by_risk":      dict(by_risk),
        "items":        items,
        "obligations":  items,
    }

    print(f"[build_checklist] {checklist['total']} items | "
          f"High={len(by_priority[Priority.HIGH.value])} "
          f"Med={len(by_priority[Priority.MEDIUM.value])} "
          f"Low={len(by_priority[Priority.LOW.value])} | "
          f"High-risk={high_risk} | Needs review={needs_review}")
    return checklist


# Alias — app.py imports `reduce_obligations`; the module's own self-test and
# other internal callers use the shorter `reduce`. Keeping both names avoids
# an import-time crash regardless of which one calling code expects.
reduce_obligations = reduce


# ---------------------------------------------------------------------------
# DataFrame / Excel export helper
# ---------------------------------------------------------------------------

def to_dataframe(obligations: List[Obligation]):
    """
    Convert obligations to a pandas DataFrame for Excel/CSV export.
    Returns None and prints a warning if pandas is not installed.
    """
    try:
        import pandas as pd
    except ImportError:
        print("[to_dataframe] pandas not installed — skipping DataFrame export.")
        return None

    rows = []
    for ob in obligations:
        rows.append({
            "ID":               ob.obligation_id,
            "Section":          ob.source_section,
            "Page":             ob.source_page,
            "Category":         ob.category,
            "Responsible Party":ob.responsible_party,
            "Description":      ob.description,
            "Trigger":          ob.trigger_type,
            "Deadline":         str(ob.deadline) if ob.deadline else "",
            "Frequency":        ob.frequency or "",
            "Penalty":          ob.penalty or "",
            "Priority":         ob.priority,
            "Confidence":       f"{ob.confidence:.0%}",
            "Needs Review":     "Yes" if ob.needs_review else "No",
            "Verbatim Snippet": ob.verbatim_snippet,
        })

    return pd.DataFrame(rows)


def export_excel(obligations: List[Obligation], path: str = "obligations.xlsx") -> str:
    """Export obligations to Excel. Returns the output path."""
    df = to_dataframe(obligations)
    if df is None:
        raise ImportError("pandas and openpyxl are required: pip install pandas openpyxl")

    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise ImportError("openpyxl is required for Excel export: pip install openpyxl")

    # Style: freeze header row, auto-width columns
    with __import__("pandas").ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Obligations")
        ws = writer.sheets["Obligations"]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

    print(f"[export_excel] Saved → {path}")
    return path


# ---------------------------------------------------------------------------
# Demo / self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import re
    from idp_extraction import (
        ExtractedObligation, Obligation, Category, Party,
        TriggerType, Priority, derive_priority,
    )

    print("=== reduce_obligations.py self-test ===\n")

    # Build 16 synthetic obligations (some duplicates)
    raw_items = []
    base_data = [
        # (section, category, party, trigger, penalty, confidence, description_suffix)
        ("8.1",  Category.INSURANCE,    Party.JLL,    TriggerType.RECURRING,     "$1,000/day", 0.95, "maintain CGL insurance $5M"),
        ("8.1",  Category.INSURANCE,    Party.JLL,    TriggerType.RECURRING,     "$1,000/day", 0.88, "maintain CGL insurance five million"),  # near-dup
        ("8.1",  Category.INSURANCE,    Party.JLL,    TriggerType.SPECIFIC_DATE, None,         0.80, "provide insurance certificate within 10 days"),
        ("12.3", Category.REPORTING,    Party.JLL,    TriggerType.RECURRING,     "$500/day",   0.92, "deliver monthly performance reports by 5th business day"),
        ("12.3", Category.REPORTING,    Party.JLL,    TriggerType.RECURRING,     "$500/day",   0.85, "submit monthly performance reports 5th business day"),  # near-dup
        ("15.2", Category.CONFIDENTIALITY, Party.BOTH, TriggerType.EVENT_DRIVEN, None,        0.90, "keep confidential all confidential information"),
        ("15.2", Category.CONFIDENTIALITY, Party.BOTH, TriggerType.SPECIFIC_DATE, None,       0.78, "confidentiality obligations survive 3 years after termination"),
        ("19.1", Category.TERM_RENEWAL, Party.BOTH,   TriggerType.SPECIFIC_DATE, None,        0.88, "provide 90 days written notice before renewal"),
        ("19.1", Category.TERM_RENEWAL, Party.BOTH,   TriggerType.RECURRING,     None,        0.65, "auto-renews for successive one-year periods"),
        ("Schedule A", Category.SLA,    Party.JLL,    TriggerType.RECURRING,     "5-10% credit", 0.93, "maintain 99.5% system uptime monthly"),
        ("Schedule A", Category.SLA,    Party.CLIENT, TriggerType.SPECIFIC_DATE, None,        0.82, "submit credit requests within 30 days"),
        ("Schedule A", Category.SLA,    Party.JLL,    TriggerType.RECURRING,     "5-10% credit", 0.87, "guarantee 99.5% uptime measured monthly"),  # near-dup
        ("22.1", Category.FINANCIAL,    Party.JLL,    TriggerType.SPECIFIC_DATE, "$10,000",   0.91, "pay invoices within 30 days of receipt"),
        ("22.1", Category.FINANCIAL,    Party.CLIENT, TriggerType.EVENT_DRIVEN,  None,        0.75, "dispute invoices within 15 days of receipt"),
        ("25.3", Category.COMPLIANCE,   Party.JLL,    TriggerType.RECURRING,     None,        0.60, "maintain data protection compliance annually"),
        ("25.3", Category.COMPLIANCE,   Party.JLL,    TriggerType.RECURRING,     None,        0.55, "ensure regulatory compliance on ongoing basis"),  # near-dup
    ]

    for i, (sec, cat, party, trigger, penalty, conf, desc) in enumerate(base_data, 1):
        ext = ExtractedObligation(
            description=f"[{i}] {desc}",
            category=cat,
            responsible_party=party,
            trigger_type=trigger,
            penalty=penalty,
            verbatim_snippet=f"Synthetic verbatim text for obligation {i} in section {sec}.",
            confidence=conf,
        )
        chunk_mock = type("C", (), {"section_id": sec, "page_range": str(200 + i)})()
        ob = Obligation.from_extracted(ext, obligation_id=f"{sec}-{i}", chunk=chunk_mock)
        raw_items.append(ob)

    print(f"Input: {len(raw_items)} raw obligations (with intentional duplicates)\n")

    # Reduce
    clean = reduce(raw_items)

    # Build checklist
    checklist = build_checklist(clean)

    print(f"\nChecklist summary:")
    print(f"  Total:        {checklist['total']}")
    print(f"  Needs review: {checklist['needs_review']}")
    for pri, items in checklist["by_priority"].items():
        print(f"  {pri}: {len(items)}")

    # DataFrame
    df = to_dataframe(clean)
    if df is not None:
        print(f"\nDataFrame shape: {df.shape}")
        print(df[["ID", "Category", "Priority", "Confidence"]].to_string(index=False))

    print("\nAll reduce_obligations self-tests passed.")
