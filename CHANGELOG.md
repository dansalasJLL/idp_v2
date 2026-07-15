# Changelog — Scalability Engineering Pass

## Pass 4 — Power BI integration (portfolio data layer)

Answers the stakeholder ask for an automated dashboard with minimum human
intervention. Architectural decision: build the tool **Power BI-ready** rather than
a dashboard inside the tool — Power BI already handles scheduled refresh, sharing,
row-level security, and portfolio/trend views, and "minimum intervention" means the
data should land somewhere Power BI pulls on a schedule with nobody clicking export.

### New — portfolio store (`portfolio.py`)
Persists every processed contract to a star schema built for Power BI:
`contracts.csv` (one row per contract run — the dimension) and `obligations.csv`
(one row per obligation — the fact), joined on `contract_run_id`, plus a
`manifest.json`. Append-only with **upsert by run_id**, so re-processing a contract
replaces its rows instead of duplicating — scheduled refreshes stay clean. Flat,
typed, scalar columns only (Power BI can't auto-model nested JSON); penalty strings
are parsed into a numeric `penalty_amount_usd` column for measures. Column order is a
fixed contract (new columns appended only) so a refresh never breaks on a rename.
**Verified**: `test_portfolio_store_upserts_and_builds_star_schema` + self-test.

### New — read-only API (`api.py`)
stdlib-only HTTP server (no new dependency) exposing `/contracts`, `/obligations`,
`/summary`, `/manifest`, `/health` as JSON for Power BI's Web connector — the path
for when the tool runs on a host. Read-only; never mutates the store. **Verified**:
`test_portfolio_api_serves_powerbi_endpoints` + self-test.

### Changed — `run_pipeline` persists automatically
Every live run now records to the portfolio store (non-fatal on failure — a store
write error can't lose the extraction). The UI gained a "📊 Power BI portfolio"
panel showing the running totals and the exact folder/API connection strings.

### New — setup docs
`POWERBI.md` (5-minute folder and Web/API setup, suggested risk + cost portfolio
dashboard) and a starter `powerbi/idp_portfolio.pbids` connection file.

### Test suite grew 16 -> 18 tests
All green; still no API key or network required.

---

## Pass 3 — risk mechanics & mitigation

Adds a stakeholder-requested risk layer: a separate risk dimension, "what to do about
it" guidance, and a dedicated high-risk view.

### New — separate risk dimension (`risk.py`)
Every obligation now carries `risk_level` (High/Medium/Low) and `risk_type` (Financial/
Legal/Regulatory/Operational/Reputational/Contractual), **independent of `priority`**.
Priority = how urgently to act (penalty + trigger); risk = kind and severity of
exposure. Derived deterministically in code so it's auditable — a reviewer can trace
why an item is High (monetary penalty, legal/regulatory nature, auto-renewal trap, or
low-confidence extraction on a firm-trigger item). **Verified**:
`test_risk_classification_is_deterministic_and_separate_from_priority`,
`test_obligations_carry_risk_and_mitigation`.

### New — mitigation library with model fallback (`risk.py`)
"What to do about it": a rules library keyed to category/risk type returns concrete,
auditable recommended actions (`source="rules"`). When no category-specific rule
applies, the caller can fall back to the model for tailored guidance
(`suggest_mitigation_via_model`, `source="model"`), which itself falls back to the rules
default if the model call fails — never fatal. **Verified**:
`test_mitigation_rules_then_model_fallback`, plus `risk.py`'s self-test.

### New — high-risk UI (both a tab and a banner count, per the stakeholder)
- A **🚨 High Risk tab** isolating High-`risk_level` items, grouped by risk type, each
  with its mitigation block and its own progress bar.
- A **high-risk metric** in the top row and a **top banner** with a by-risk-type
  breakdown.
- Risk badges + a green mitigation block on every obligation (shared render function,
  so the Checklist and High Risk tabs stay in sync).
- A **risk-level filter** in the sidebar.
- `risk_level` / `risk_type` / `mitigation_summary` / `mitigation_actions` /
  `mitigation_source` columns added to the Excel/CSV export, ordered up front.
- Demo data is enriched with risk fields on load, so this is visible with zero setup.
  **Verified**: `test_checklist_exposes_risk_grouping`, plus a headless Streamlit
  `AppTest` run confirming the metric reads "High risk 13", the tabs render, and the
  banner + mitigation text appear with no exceptions.

### Changed — `build_checklist` and the `Obligation` model
`Obligation` gained `risk_level`, `risk_type`, `mitigation`. `build_checklist` now
returns `by_risk` grouping and a `high_risk` count. Fixed a Streamlit
`use_container_width` deprecation while in the file.

### Test suite grew 12 -> 16 tests
All green; still no API key or network required.

### Tunable monetary severity threshold
Follow-up to the "everything is High" observation on the penalty-heavy Spanish lease:
a monetary penalty is now only High risk if its parsed dollar amount meets a
configurable threshold (default $10,000); below it, Medium. Penalties with no stated
amount stay High (unknown magnitude = treat as material). Non-monetary drivers (legal,
regulatory, auto-renewal) are unaffected. The threshold is a live slider in the sidebar,
so a reviewer can tune it per contract type. Effect on demo data: the CR lease dropped
from 18/20 High to 6/20 High — far more useful triage. **Verified**: the amount parser
and threshold behavior are covered in `risk.py`'s self-test (small fine → Medium, large
fine → High, unknown amount → High).

---

## Pass 2 — production hardening (cost, cache, Falcon)

Closes two of the three items that were explicitly left open at the end of pass 1,
and adds the cost infrastructure the savings-KPI ask depends on.

### New — cost & token telemetry (`telemetry.py`)
The savings KPIs need real numbers, and you can't report "$X per contract" without
capturing it. Added a model-agnostic telemetry layer that records per model call:
input/output tokens, estimated USD cost (configurable pricing table), latency, and
cache-hit status. A `RunStats` aggregates a whole document run into a summary
(total cost, tokens, cache-hit rate, wall-clock) that `run_pipeline` returns and the
UI shows in a new "Run cost & efficiency" panel. **Verified**:
`test_telemetry_captures_token_usage`, plus `telemetry.py`'s own self-test.

### New — content-addressed extraction cache (`cache.py`)
Keyed on `(clause text + prompt + model + schema)`. A cache hit returns the stored
result for $0 and ~0ms. Two effects that matter at BPO scale: re-running a contract
is free, and boilerplate shared across a portfolio is paid for once instead of once
per contract. Backends: `MemoryCache`, `DiskCache` (persists across restarts),
`NullCache` (benchmarking). `run_pipeline` uses `DiskCache` by default; the interface
is tiny so a Redis/shared-table backend drops in for multi-worker production.
**Verified**: `test_cache_eliminates_repeat_model_calls_and_cost` proves a warm run
makes **zero** model calls and costs **$0.00** while returning identical results.

### Fixed — FalconProvider is no longer a stub
Implemented as a real, configuration-driven HTTP adapter supporting both an
Anthropic/OpenAI-style tool-calling surface (`mode="tool"`) and a plain-JSON endpoint
(`mode="json"` + `response_path`), with retries, usage capture, and a clear error if
no endpoint is configured. The platform team supplies endpoint/key/model/mode; nothing
else in the pipeline changes. **Verified**:
`test_falcon_provider_builds_and_parses_without_network`, `test_falcon_requires_endpoint`.

### Changed — `ModelProvider` base class
Providers now implement *either* `extract()` or `extract_with_response()` (the latter
also returns the raw response so telemetry can read token usage). The base supplies
sensible defaults for the other and a clear error if neither is implemented. Existing
providers and mocks are unaffected.

### Test suite grew 8 -> 12 tests
All green (`pytest tests/ -q`), still no API key or network required.

### Still open after pass 2
- The KPI *dashboard / persistence* (storing `run_stats` per contract over time,
  charting analyst-hours-saved) — the data is now captured per run; aggregating it
  across runs into a dashboard is the remaining piece.
- PyMuPDF AGPL/commercial licensing sign-off (handover section 12.2).
- A real Falcon endpoint to point the (now-ready) adapter at.

---

# Pass 1 — core fixes & Lease support

This pass turns the hackathon prototype into a pipeline that actually runs
end-to-end, adds automated regression tests, and adds the two pieces of
stakeholder feedback (measurable KPIs infrastructure + Lease Administration
support) into the code itself rather than leaving them as roadmap items.

Every item below is verified: either an automated test in `tests/` or a
self-test in the module itself. Nothing here is "should work" — it's "does work,
confirmed by running it."

## Fixed — the live pipeline (was section 5.1 in the handover)

The core wiring bug is fixed. `run_pipeline()` in `app.py` now actually works
end-to-end on a real PDF upload:

- `parse_chunk.parse_and_chunk()` now accepts **raw bytes** (Streamlit's
  `file_uploader.read()` output), not just a file path — added a bytes-aware
  `_open_fitz_doc()` helper.
- Added the missing `parse_chunk.count_pages()` function.
- `reduce_obligations.py` now exposes `reduce_obligations` as an alias for
  `reduce` (both names work — nothing imports a name that doesn't exist).
- `build_checklist()` now accepts `document_name` and `page_count` and returns
  them in the checklist dict, along with an `obligations` key (same list as
  `items`) — matching exactly what `app.py`'s UI reads (`data["obligations"]`,
  `data.get("document_name")`, `data.get("page_count")`).
- `run_pipeline` no longer converts `Obligation` objects to dicts before
  calling `reduce()` — it stays as `List[Obligation]` all the way through,
  which is what `reduce()` and `build_checklist()` actually expect.
- **Verified**: `tests/test_pipeline.py::test_full_pipeline_msa_profile` runs
  the complete pipeline against a real synthetic PDF and checks the output
  shape matches what the UI needs.

## Fixed — a second, previously undocumented bug

- `reduce_obligations.py`'s `_word_set()` (used by dedup) called `re.sub(...)`,
  but `re` was only imported *locally inside* `reduce()`, not at module scope.
  This worked when the file was run directly (`python reduce_obligations.py`,
  where the `if __name__ == "__main__":` block's own `import re` happened to
  live at module scope) but raised `NameError` the moment the module was
  *imported* by something else — exactly what `app.py`'s `run_pipeline` does.
  Every self-test passed while the actual integration path was broken.
  Fixed by moving `import re` to the top of the module.
- **Verified**: `tests/test_pipeline.py::test_dedup_does_not_crash_on_import`
  reproduces the exact import pattern that used to fail.

## Fixed — missing dependencies (was section 5.2)

`requirements.txt` now includes `pymupdf` and `tiktoken`, which `parse_chunk.py`
actually imports. Added `requirements-dev.txt` for `pytest`.

## Fixed — the governance guard was not automatic (was section 5.3)

`run_pipeline` used to hardcode `contains_real_client_data=False` — so the
safety boundary was procedural (a banner + trusting the operator), not a real
technical control. Now:

- The live-upload sidebar has an explicit checkbox: *"I confirm this is
  synthetic / sample data"* — **unchecked by default**.
- `contains_real_client_data` is passed through as `not is_synthetic`, so an
  upload is treated as real client data (and blocked on the sandbox endpoint)
  unless the user actively confirms otherwise.
- **Verified**: `tests/test_pipeline.py::test_governance_guard_blocks_real_data_on_sandbox`.

## Fixed — UI bugs (was section 5.4)

- Removed the duplicate `<p class="idp-title">` line that rendered the page
  title twice.
- The **Table** tab was nested inside the checklist's `for o in filtered:`
  loop, so it silently rebuilt once per obligation on every render. Dedented
  it to sit beside the Checklist tab, as originally intended.

## Fixed — throughput (was section 5.5)

`extract_all()` now runs clause extraction across a bounded thread pool
(`max_workers=5` by default) instead of one clause at a time. Each clause is
an independent map step with no shared state, so this is a safe wall-clock
speedup with no change in correctness. `max_workers=1` preserves the original
strict-sequential behavior for deterministic debugging. A `progress_cb`
callback lets the UI progress bar advance smoothly per clause instead of
jumping in three big steps.

## Fixed — schema drift across demo datasets (was section 4)

`demo_obligations_us.json`, `demo_obligations_cr.json`, and
`demo_obligations_planetorg.json` used a different field naming convention
(`source_clause` / `page_number` / `due_date` / `penalty_if_missed`, and no
`verbatim_snippet`) than the canonical schema in `demo_obligations.json`
(`source_section` / `source_page` / `deadline` / `frequency` / `penalty`).

- Added `normalize_demo_data.py`, a one-time migration script that converts
  the legacy files to the canonical schema — splitting the combined
  `source_clause` field into a proper `section_id` + `verbatim_snippet`,
  inferring `trigger_type` from the due-date text, and mapping the cadence
  into `frequency` vs `deadline` correctly.
- All four demo datasets now share **exactly one schema** (verified
  programmatically — same key set on every item, across all 100 obligations).
- Removed the `.get(a, b)` dual-key fallback logic that had accumulated in
  `app.py` to paper over the drift (e.g. `o.get("source_section",
  o.get("source_clause", ...))`) — the code is simpler and the schema is the
  single source of truth again.

## New — Lease Administration support (stakeholder request)

This was flagged as a roadmap item in the earlier handover (section 10.2). It's
now implemented, not just planned:

- `idp_extraction.py` adds a `DocumentProfile` abstraction: a taxonomy
  (`categories`) + a tailored prompt focus (`prompt_focus`) per document type.
  Two profiles ship: `msa` and `lease`.
- The `Category` enum gained `Payment` and `Maintenance` (lease-specific,
  distinct from the general `Financial` category).
- The `Party` enum gained `Landlord` / `Tenant`, with automatic normalization
  of Spanish lease terms (`Arrendatario` → `Tenant`, `Arrendador` → `Landlord`,
  `Ambas partes` → `Both`) via `idp_extraction.normalize_party` — contracts in
  Spanish extract cleanly into the same English-language schema.
- `app.py`'s live-upload sidebar now has a **document type selector** (Master
  Service Agreement / Lease Administration) that drives which taxonomy and
  prompt the pipeline uses.
- The demo dataset picker now shows an **MSA / Lease Administration badge** —
  two of the four existing demo contracts (`demo_obligations_cr.json`, a
  Spanish-language lease, and `demo_obligations_us.json`) turn out to already
  be lease contracts, so this is proof the same engine already handles both
  document types, not just a design plan.
- **Verified**:
  `tests/test_pipeline.py::test_full_pipeline_lease_profile_normalizes_spanish_parties`
  confirms the Lease profile activates the right prompt and the Spanish party
  term normalizes correctly.

## New — automated regression test suite

Added `tests/test_pipeline.py` (8 tests, run via `pytest tests/ -v` or directly
with `python tests/test_pipeline.py` if pytest isn't installed) plus a small
synthetic sample PDF (`tests/sample_synthetic_msa.pdf`). Uses a mock model
provider, so it runs with no API key and no network access — this is what
should run in CI on every change from here on, and it's exactly the kind of
test that would have caught the section 5.1 pipeline bug before it shipped.

## Not changed in this pass (still open)

- `FalconProvider` is still a stub — production still needs it implemented
  against the real Falcon endpoint (see the handover's section 11/integration
  path).
- The KPI *measurement* infrastructure (dashboards, actual pilot numbers) is
  still a roadmap item — this pass makes the pipeline itself trustworthy and
  scalable, which is the prerequisite for collecting real KPI data, but does
  not build a KPI dashboard.
- PyMuPDF's AGPL/commercial licensing question (handover section 12.2) is
  still open and still needs legal/OSS-compliance sign-off before production.
