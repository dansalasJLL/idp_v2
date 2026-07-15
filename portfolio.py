"""
portfolio.py — persistence + Power BI-ready data layer.

Why this exists
---------------
A dashboard needs history, and the tool itself is stateless between runs. This
module persists every processed contract to a small on-disk store and projects
it into the flat, typed shapes Power BI ingests with zero human intervention:

  - obligations.csv : one row per obligation across ALL processed contracts
  - contracts.csv   : one row per contract run (cost, counts, risk rollup)
  - a manifest      : schema + refresh metadata

Power BI connects to the OUTPUT FOLDER (Get Data -> Folder, or a single CSV),
and the Power BI service auto-refreshes it on a schedule — nobody has to open
the tool or click export. When the tool later runs as a server, the same rows
are served by api.py (see that module) so Power BI can pull from a URL instead
of a folder, with no change to the model.

Design choices for "minimum human intervention":
  - Append-only, keyed by a deterministic run_id, so re-processing a contract
    UPSERTS (replaces its rows) rather than duplicating — refreshes stay clean.
  - Star-schema-friendly: contracts (dimension) + obligations (fact), joined on
    contract_run_id. This is exactly what Power BI's model view expects.
  - Flat scalar columns only (no nested dicts) — Power BI can't model nested
    JSON without manual transforms, which would defeat the automation goal.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional


DEFAULT_STORE = os.environ.get("IDP_PORTFOLIO_DIR", "portfolio_store")

# Column order is the contract with Power BI — stable so scheduled refreshes
# never break on a reordered/renamed field. Add new columns at the END only.
OBLIGATION_COLUMNS = [
    "contract_run_id", "document_name", "document_type", "processed_at",
    "obligation_id", "risk_level", "risk_type", "priority", "category",
    "responsible_party", "description", "penalty", "penalty_amount_usd",
    "deadline", "frequency", "trigger_type", "source_section", "source_page",
    "confidence", "needs_review", "mitigation_summary", "mitigation_actions",
    "mitigation_source", "status",
]

CONTRACT_COLUMNS = [
    "contract_run_id", "document_name", "document_type", "processed_at",
    "page_count", "total_obligations", "high_risk", "medium_risk", "low_risk",
    "needs_review", "with_penalty", "total_penalty_exposure_usd",
    "compute_cost_usd", "input_tokens", "output_tokens", "cache_hit_rate",
    "wall_clock_s", "model",
]


def make_run_id(document_name: str, processed_at: Optional[str] = None) -> str:
    """Deterministic per (document, day) so re-processing the same contract the
    same day upserts instead of duplicating. Include processed_at date only, not
    time, so intra-day re-runs collapse to one logical run."""
    day = (processed_at or datetime.now(timezone.utc).isoformat())[:10]
    h = hashlib.sha256(f"{document_name}|{day}".encode("utf-8")).hexdigest()[:12]
    return f"run_{day}_{h}"


def _to_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _flatten_obligation(ob: dict, ctx: dict) -> dict:
    """One obligation dict + run context -> one flat CSV row."""
    from risk import parse_penalty_amount
    mit = ob.get("mitigation") or {}
    return {
        "contract_run_id": ctx["contract_run_id"],
        "document_name": ctx["document_name"],
        "document_type": ctx["document_type"],
        "processed_at": ctx["processed_at"],
        "obligation_id": ob.get("obligation_id", ""),
        "risk_level": ob.get("risk_level", ""),
        "risk_type": ob.get("risk_type", ""),
        "priority": ob.get("priority", ""),
        "category": ob.get("category", ""),
        "responsible_party": ob.get("responsible_party", ""),
        "description": ob.get("description", ""),
        "penalty": ob.get("penalty") or "",
        "penalty_amount_usd": parse_penalty_amount((ob.get("penalty") or "").lower()) or "",
        "deadline": ob.get("deadline") or "",
        "frequency": ob.get("frequency") or "",
        "trigger_type": ob.get("trigger_type", ""),
        "source_section": ob.get("source_section", ""),
        "source_page": ob.get("source_page", ""),
        "confidence": ob.get("confidence", ""),
        "needs_review": bool(ob.get("needs_review", False)),
        "mitigation_summary": mit.get("summary", ""),
        "mitigation_actions": " | ".join(mit.get("actions", [])),
        "mitigation_source": mit.get("source", ""),
        "status": ob.get("status", "Open"),
    }


def _contract_row(checklist: dict, ctx: dict) -> dict:
    from risk import parse_penalty_amount
    obs = checklist.get("obligations", checklist.get("items", []))
    by_risk = checklist.get("by_risk", {})
    rs = checklist.get("run_stats", {}) or {}
    exposure = sum(
        parse_penalty_amount((o.get("penalty") or "").lower()) or 0 for o in obs
    )
    return {
        "contract_run_id": ctx["contract_run_id"],
        "document_name": ctx["document_name"],
        "document_type": ctx["document_type"],
        "processed_at": ctx["processed_at"],
        "page_count": checklist.get("page_count") or "",
        "total_obligations": checklist.get("total", len(obs)),
        "high_risk": len(by_risk.get("High", [])),
        "medium_risk": len(by_risk.get("Medium", [])),
        "low_risk": len(by_risk.get("Low", [])),
        "needs_review": checklist.get("needs_review", 0),
        "with_penalty": sum(1 for o in obs if o.get("penalty")),
        "total_penalty_exposure_usd": round(exposure, 2),
        "compute_cost_usd": rs.get("total_cost_usd", 0.0),
        "input_tokens": rs.get("input_tokens", 0),
        "output_tokens": rs.get("output_tokens", 0),
        "cache_hit_rate": rs.get("cache_hit_rate", 0.0),
        "wall_clock_s": rs.get("wall_clock_s", 0.0),
        "model": rs.get("model", ""),
    }


class PortfolioStore:
    """Append-only, upsert-by-run_id store backed by two JSONL files, projected
    to Power BI-ready CSVs on write."""

    def __init__(self, root: str = DEFAULT_STORE):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.obligations_jsonl = os.path.join(root, "obligations.jsonl")
        self.contracts_jsonl = os.path.join(root, "contracts.jsonl")
        self.obligations_csv = os.path.join(root, "obligations.csv")
        self.contracts_csv = os.path.join(root, "contracts.csv")
        self.manifest_path = os.path.join(root, "manifest.json")

    # --- write path ---------------------------------------------------------
    def record_run(self, checklist: dict, document_type: str = "MSA") -> str:
        """Persist one processed contract. Upserts by run_id (re-processing the
        same contract the same day replaces its rows). Returns the run_id."""
        document_name = checklist.get("document_name") or "unknown"
        processed_at = datetime.now(timezone.utc).isoformat()
        run_id = make_run_id(document_name, processed_at)
        ctx = {
            "contract_run_id": run_id,
            "document_name": document_name,
            "document_type": document_type,
            "processed_at": processed_at,
        }
        obs = checklist.get("obligations", checklist.get("items", []))
        ob_rows = [_flatten_obligation(o, ctx) for o in obs]
        contract_row = _contract_row(checklist, ctx)

        self._upsert(self.obligations_jsonl, ob_rows, run_id)
        self._upsert(self.contracts_jsonl, [contract_row], run_id)
        self._rebuild_csvs()
        self._write_manifest()
        return run_id

    def _upsert(self, path: str, new_rows: List[dict], run_id: str) -> None:
        existing = self._read_jsonl(path)
        kept = [r for r in existing if r.get("contract_run_id") != run_id]
        with open(path, "w", encoding="utf-8") as f:
            for r in kept + new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_jsonl(path: str) -> List[dict]:
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _rebuild_csvs(self) -> None:
        self._write_csv(self.obligations_csv, self._read_jsonl(self.obligations_jsonl), OBLIGATION_COLUMNS)
        self._write_csv(self.contracts_csv, self._read_jsonl(self.contracts_jsonl), CONTRACT_COLUMNS)

    @staticmethod
    def _write_csv(path: str, rows: List[dict], columns: List[str]) -> None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def _write_manifest(self) -> None:
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": {
                "obligations": {"path": "obligations.csv", "columns": OBLIGATION_COLUMNS,
                                "grain": "one row per obligation per contract run"},
                "contracts": {"path": "contracts.csv", "columns": CONTRACT_COLUMNS,
                              "grain": "one row per contract run"},
            },
            "join_key": "contract_run_id",
            "star_schema": {"fact": "obligations", "dimension": "contracts"},
            "row_counts": {
                "obligations": len(self._read_jsonl(self.obligations_jsonl)),
                "contracts": len(self._read_jsonl(self.contracts_jsonl)),
            },
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    # --- read path (used by api.py / tests) --------------------------------
    def obligations(self) -> List[dict]:
        return self._read_jsonl(self.obligations_jsonl)

    def contracts(self) -> List[dict]:
        return self._read_jsonl(self.contracts_jsonl)

    def summary(self) -> Dict:
        contracts = self.contracts()
        obs = self.obligations()
        return {
            "contracts": len(contracts),
            "obligations": len(obs),
            "high_risk": sum(1 for o in obs if o.get("risk_level") == "High"),
            "total_compute_cost_usd": round(sum(_to_float(c.get("compute_cost_usd")) or 0 for c in contracts), 4),
            "total_penalty_exposure_usd": round(sum(_to_float(c.get("total_penalty_exposure_usd")) or 0 for c in contracts), 2),
        }


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import tempfile
    import shutil

    print("=== portfolio.py self-test ===\n")
    tmp = tempfile.mkdtemp()
    store = PortfolioStore(root=tmp)

    checklist = {
        "document_name": "Test MSA.pdf",
        "page_count": 100,
        "total": 2,
        "needs_review": 0,
        "by_risk": {"High": [1], "Medium": [], "Low": [1]},
        "run_stats": {"total_cost_usd": 0.05, "input_tokens": 1000, "output_tokens": 300,
                      "cache_hit_rate": 0.0, "wall_clock_s": 3.2, "model": "claude-sonnet-4-6"},
        "obligations": [
            {"obligation_id": "8.1-1", "risk_level": "High", "risk_type": "Financial",
             "priority": "High", "category": "Insurance", "responsible_party": "JLL",
             "description": "Maintain insurance", "penalty": "USD $50,000 per lapse",
             "trigger_type": "Recurring", "confidence": 0.95, "needs_review": False,
             "mitigation": {"summary": "Money risk", "actions": ["Do A", "Do B"], "source": "rules"}},
            {"obligation_id": "9.1-1", "risk_level": "Low", "risk_type": "Contractual",
             "priority": "Low", "category": "Notice", "responsible_party": "Both",
             "description": "Notice by mail", "penalty": "", "trigger_type": "Conditional",
             "confidence": 0.9, "needs_review": False,
             "mitigation": {"summary": "Low", "actions": ["File"], "source": "rules"}},
        ],
    }

    rid = store.record_run(checklist, document_type="MSA")
    print(f"recorded run: {rid}")
    assert len(store.obligations()) == 2
    assert len(store.contracts()) == 1

    # Upsert: re-record same contract same day -> still 1 contract, 2 obligations
    store.record_run(checklist, document_type="MSA")
    assert len(store.contracts()) == 1, "re-processing must upsert, not duplicate"
    assert len(store.obligations()) == 2
    print("upsert (no duplication on re-run): OK")

    # Add a second, different contract
    checklist2 = dict(checklist, document_name="Lease CR.pdf")
    checklist2["obligations"] = checklist["obligations"][:1]
    checklist2["total"] = 1
    checklist2["by_risk"] = {"High": [1], "Medium": [], "Low": []}
    store.record_run(checklist2, document_type="Lease")
    assert len(store.contracts()) == 2
    assert len(store.obligations()) == 3
    print("second contract appended: OK")

    # CSV projection
    assert os.path.exists(store.obligations_csv) and os.path.exists(store.contracts_csv)
    with open(store.obligations_csv, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    assert header == OBLIGATION_COLUMNS, "CSV header must match the stable contract"
    print("Power BI CSV projection + stable schema: OK")

    # penalty amount parsed into its own numeric column
    obs = store.obligations()
    hi = next(o for o in obs if o["obligation_id"] == "8.1-1")
    assert str(hi["penalty_amount_usd"]) in ("50000.0", "50000"), hi["penalty_amount_usd"]
    print("penalty amount parsed to numeric column: OK")

    print("summary:", json.dumps(store.summary()))
    shutil.rmtree(tmp)
    print("\nAll portfolio self-tests passed.")
