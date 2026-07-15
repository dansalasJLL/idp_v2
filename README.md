# Intelligent Document Processing Agent

Turns a contract (PDF) into a categorized, source-linked compliance checklist. Built
for the JLL hackathon, designed to live entirely within JLL-sanctioned environments.

Supports two document types out of the box — **Master Service Agreements** and
**Lease Administration** documents (leases, in English or Spanish) — via the same
pipeline. See "Document profiles" below.

`parse → chunk → extract → reduce → checklist → UI / export`

---

## System requirements

- **Python 3.10–3.12** (3.11 recommended)
- **pip** and ~1 GB free disk for dependencies
- **Anthropic API key** (hackathon sponsorship) — only needed for live mode
- **No Node.js needed** to run the app (Node was only used to generate the docs/deck)
- Any OS (macOS / Linux / Windows)

## Files

| File | Role |
|------|------|
| `app.py` | Streamlit UI — upload, checklist, click-to-source, export |
| `providers.py` | Model adapters: Claude (sandbox) / Falcon (prod) / JLL GPT + governance guard |
| `idp_extraction.py` | Obligation schema, document profiles (MSA/Lease), prompt, per-clause extraction + priority |
| `parse_chunk.py` | PDF (path or bytes) → structure-aware `ClauseChunk` list |
| `reduce_obligations.py` | Dedup + build the final checklist |
| `risk.py` | Risk classification (level + type) + mitigation library with model fallback |
| `telemetry.py` | Per-call token/cost/latency capture → run cost & the savings-KPI data source |
| `portfolio.py` | Persists every run to a Power BI-ready star schema (contracts + obligations CSVs) |
| `api.py` | Read-only HTTP API over the portfolio store, for Power BI's Web connector |
| `cache.py` | Content-addressed extraction cache (memory / disk) — re-runs & shared boilerplate cost $0 |
| `normalize_demo_data.py` | One-time migration script — unifies demo JSON to the canonical schema |
| `demo_obligations*.json` | Cached **synthetic** demo data — 4 sample contracts (2 MSA, 2 Lease) |
| `tests/` | End-to-end pipeline test suite (`pytest tests/ -v`) — no API key needed |
| `requirements.txt` | Python dependencies |
| `requirements-dev.txt` | Test-only dependencies (pytest) |

---

## First steps

### 1. Put all files in one folder, then set up a virtual environment
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Verify the pieces — no API key required
Each module has a self-test. Run them top to bottom; all should pass.
```bash
python parse_chunk.py          # parse/chunk self-tests
python reduce_obligations.py   # dedup demo (16 → 14 records)
python providers.py            # provider + governance-guard self-tests
python idp_extraction.py       # offline schema/validation check
```

### 2b. Run the automated test suite (recommended)
A real end-to-end integration suite lives in `tests/` — it exercises the full
`parse → extract → reduce → checklist` pipeline (both the MSA and Lease profiles)
against a small synthetic sample PDF, using a mock model provider so it needs no
API key and no network access. This is what actually catches integration bugs
that per-module self-tests miss (e.g. a renamed function breaking an import
elsewhere).
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

### 3. Run the app in demo mode — still no API key
```bash
streamlit run app.py
```
Opens `localhost:8501` with the **Demo dataset** selected. Fully clickable — this is
your judging-safe path and proves the whole UI end to end.

### 4. Enable live mode (sandbox — synthetic contracts only)
```bash
export ANTHROPIC_API_KEY=sk-ant-...   # Windows: set ANTHROPIC_API_KEY=...
streamlit run app.py
```
Choose **Upload MSA (live)** and upload a **clean, text-based sample** PDF.

---

## ⚠️ The sandbox rule (non-negotiable)

The sponsored Claude access is **cleared for synthetic / sample contracts only**.
**Never upload real client contracts** (MSAs or leases) in this app. The code enforces
this with a governance guard (`assert_data_allowed`) that blocks real-flagged data
from any endpoint not cleared for it. The live-upload sidebar requires an explicit
"this is synthetic data" confirmation before every run — uploads are treated as real
data by default (safe-by-default), not the other way around. Production swaps the
adapter to **JLL Falcon**, which is sanctioned and data-cleared, so real contracts
stay inside JLL's governed envelope.

---

## Architecture & run order

```
parse_chunk.parse_and_chunk(pdf)               → List[ClauseChunk]   (path OR raw bytes)
        ↓
idp_extraction.extract_all(chunks, provider,
    profile=get_profile("msa"|"lease"))        → List[Obligation]   (map, parallel — ThreadPoolExecutor)
        ↓
reduce_obligations.reduce_obligations(obs)     → deduped, sorted List[Obligation]   (reduce)
        ↓
reduce_obligations.build_checklist(obs,
    document_name=..., page_count=...)         → checklist dict (incl. "obligations" key for the UI)
        ↓
app.py                                          → UI + Excel/CSV export
```

The **only** endpoint-specific code is `providers.py`. Everything else is identical
between sandbox and production, and between document types.

## Document profiles — how this scales to new document types

Extraction is not hardcoded to MSAs. `idp_extraction.py` defines a `DocumentProfile`
(a taxonomy of categories + a tailored prompt focus) per document type. Two ship
today:

| Profile key | Label | Example categories |
|---|---|---|
| `msa` | Master Service Agreement | Financial, Insurance, SLA, Indemnity & Liability, ... |
| `lease` | Lease Administration | Payment, Maintenance, Insurance, Term & Renewal, ... |

`app.py`'s live-upload sidebar lets the user pick the profile before running the
pipeline; the demo dataset picker shows a badge (MSA / Lease Administration) per
sample contract — two of the four demo datasets are in fact lease contracts (one in
Spanish), proving the same engine handles both today.

Party names are normalized too: Spanish lease terms (`Arrendatario`, `Arrendador`)
map to the canonical `Party` enum (`Tenant`, `Landlord`) via
`idp_extraction.normalize_party`, so the schema stays consistent regardless of the
source contract's language.

**Adding a third document type** (e.g. NDAs, SOWs) means adding one
`DocumentProfile` entry in `idp_extraction.py` — the parser, schema, dedup,
checklist, UI, and export are all unchanged.

## Throughput — parallel extraction

`extract_all(chunks, provider, max_workers=5)` runs clause extraction across a
bounded thread pool instead of one clause at a time. Each clause is an independent
map step with no shared state, so this is a safe, large wall-clock speedup on
multi-hundred-clause contracts. Set `max_workers=1` to fall back to strict
sequential order (useful for deterministic debugging).

## Risk mechanics & mitigation

Every obligation carries a **separate risk dimension**, independent of `priority`:

- `risk_level` — High / Medium / Low, derived in code (auditable, deterministic) from
  monetary exposure, legal/regulatory nature, auto-renewal traps, and extraction
  confidence. Priority answers "how urgently must someone act?"; risk answers "what
  kind of exposure, and how bad if it goes wrong?" — related but not the same.
- `risk_type` — Financial / Legal / Regulatory / Operational / Reputational /
  Contractual, mapped from the obligation category.
- `mitigation` — a what-to-do block (`summary` + `actions` + `source`). Guidance comes
  from a **rules library** keyed to category/risk type (deterministic, auditable); when
  no category-specific rule applies, the caller can **fall back to the model**
  (`risk.suggest_mitigation_via_model`) for tailored guidance. Provenance is recorded
  in `source` ("rules" or "model").

All of this lives in `risk.py`, separate from extraction, so it's independently
testable and a reviewer can trace exactly why an item was rated High. The UI surfaces
it as a dedicated **🚨 High Risk tab**, a **high-risk count** in the metrics row and a
top banner (broken down by risk type), risk badges + mitigation blocks on every
obligation, a **risk-level filter**, and `risk_level`/`risk_type`/`mitigation_*` columns
in the Excel/CSV export. Demo data is enriched with these fields on load, so the
classification is visible without a live run.

## Power BI / BI dashboards

Every processed contract is appended to a **Power BI-ready data layer** — no manual
export. `portfolio.py` maintains a star schema (`contracts.csv` = dimension,
`obligations.csv` = fact, joined on `contract_run_id`) under `portfolio_store/`.
Power BI connects two ways:

- **Folder** (zero infra): Get Data → Folder → the store folder → scheduled refresh.
- **Web/API** (hosted): run `python api.py --port 8600`, then Get Data → Web →
  `http://<host>:8600/obligations`.

Both refresh on Power BI's own schedule, so the dashboard updates itself as contracts
are processed. Full walkthrough (with a suggested risk + cost portfolio dashboard and a
starter `.pbids` file) is in `POWERBI.md`.

## Cost, caching & telemetry

Every live run captures per-clause token usage, latency, and estimated USD cost
(`telemetry.py`) and returns a `run_stats` summary the UI shows in the "Run cost &
efficiency" panel. This is the raw material for the savings KPIs (real $/contract,
tokens, cache savings, wall-clock).

Extraction is wrapped in a **content-addressed cache** (`cache.py`) keyed on
`(clause text + prompt + model + schema)`. A cache hit returns the stored result for
$0 and ~0ms, so:

- Re-running a contract (reviewer reloads, job retries, portfolio re-processing)
  costs nothing the second time.
- Boilerplate that recurs across a portfolio (standard insurance / confidentiality /
  notice clauses) is paid for once, not once per contract.

`run_pipeline` uses `DiskCache` by default (survives restarts). For multi-worker
production, implement the tiny `ExtractionCache` interface against Redis or a shared
table — the pipeline is agnostic to the backend.

## Production path (post-hackathon)

1. Configure `FalconProvider` against JLL Falcon's inference endpoint. It's a real,
   working HTTP adapter now (not a stub) — set `endpoint`, `api_key`, `model`, and
   `mode` (`"tool"` for Anthropic/OpenAI-style tool calling, or `"json"` + a
   `response_path` for a plain-JSON endpoint):
   ```python
   provider = get_provider("falcon",
       endpoint="https://falcon.jll.com/v1/messages",
       api_key=os.environ["FALCON_API_KEY"],
       model="falcon-<model-id>", mode="tool")
   ```
2. In `app.py`, swap `ClaudeProvider()` for that in `run_pipeline`. Set the real
   Falcon token pricing in `telemetry.PRICING_PER_MTOK["jll-falcon"]` so cost
   reporting stays accurate.
3. Nothing downstream changes — parse, schema, reduce, checklist, UI, export, the
   MSA/Lease profiles, caching, and telemetry all stand as-is.

---

## Troubleshooting

- **`import fitz` fails** → `pip install pymupdf` (the package imports as `fitz`). Make
  sure no unrelated package literally named `fitz` is installed.
- **Model name mismatch** → `providers.py` sets `DEFAULT_MODEL = "claude-sonnet-4-6"`.
  If the sponsorship exposes a different model id, change it there.
- **Port in use** → `streamlit run app.py --server.port 8502`.
- **`tiktoken` not installed** → optional; token counts fall back to a safe heuristic.
- **Live run errors** → the app auto-falls back to the demo dataset and shows the
  reason, so a failed live call never breaks a presentation. Check the key and model name.

## Demo-day playbook

- Present from the **Demo dataset** — it can't be broken by a network hiccup.
- Show the live upload **once** with a synthetic PDF as the "it runs on real PDFs" beat.
- Land on the **expanded obligation view** (source clause + verbatim snippet) — that's
  the trust moment that answers "why would legal believe an AI?"
