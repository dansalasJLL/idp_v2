# Power BI integration — 5-minute setup

The IDP Agent writes a **Power BI-ready data layer** on every processed contract.
You don't export anything by hand: each run appends to a persistent store, and
Power BI refreshes from it on its own schedule. This is the "minimum human
intervention" path — process contracts in the tool, open Power BI, see the
portfolio update.

## What the tool produces

A portfolio store (default folder `portfolio_store/`, override with the
`IDP_PORTFOLIO_DIR` env var) containing two flat, typed CSVs in a star schema:

| File | Grain | Role in Power BI |
|------|-------|------------------|
| `contracts.csv` | one row per processed contract | **dimension** table |
| `obligations.csv` | one row per obligation | **fact** table |

They join on `contract_run_id`. A `manifest.json` documents the schema and row
counts. Re-processing the same contract on the same day **upserts** (replaces
its rows) rather than duplicating, so refreshes stay clean.

Key numeric columns ready for measures: `penalty_amount_usd`, `compute_cost_usd`,
`total_penalty_exposure_usd`, `high_risk` / `medium_risk` / `low_risk`,
`input_tokens` / `output_tokens`, `confidence`.

## Option A — Folder connection (recommended, zero infrastructure)

Best when the store folder is on a shared drive / OneDrive / SharePoint the
Power BI service can reach.

1. Power BI Desktop → **Get Data → Folder** (or **Text/CSV** for a single file).
2. Point it at the `portfolio_store/` folder (or directly at `obligations.csv`).
3. Load both `contracts.csv` and `obligations.csv`.
4. **Model view** → drag `contracts[contract_run_id]` onto
   `obligations[contract_run_id]` → a one-to-many relationship.
5. **Publish** to the Power BI service → **Dataset settings → Scheduled refresh**
   → set the cadence (e.g. hourly/daily). Done — it now updates itself.

If the folder is on OneDrive/SharePoint, use a personal/enterprise gateway or
the built-in cloud connector so the service can refresh without your machine on.

## Option B — Web/API connection (when the tool runs on a server)

Best once the tool is hosted and you want Power BI to pull from a URL.

1. On the host, run the read-only API:
   ```bash
   python api.py --port 8600 --store portfolio_store
   ```
2. Power BI Desktop → **Get Data → Web** →
   `http://<host>:8600/obligations` (and again for `/contracts`).
3. Build the same relationship + scheduled refresh as Option A.

Endpoints: `/contracts`, `/obligations`, `/summary`, `/manifest`, `/health`.
The API is **read-only** and never mutates the store. Put auth / a reverse proxy
in front of it before exposing it beyond the internal network (it ships with no
auth by design — that's a deployment decision, not a code one).

## Suggested dashboard (both risk + cost in one portfolio model)

Pages:
1. **Portfolio overview** — cards: # contracts, # obligations, # high-risk,
   total penalty exposure, total compute cost. Trend line of contracts processed
   over time (`processed_at`).
2. **Risk** — stacked bar of `risk_level` by `document_type`; matrix of
   `risk_type` × count; table of high-risk obligations with `mitigation_summary`.
3. **Cost & savings** — `compute_cost_usd` per contract, cache-hit-rate trend,
   and a savings measure: `[analyst_hours_saved] × [loaded_rate] − compute_cost`
   (add the rate/hours assumptions as what-if parameters).

## Starter connection file

`powerbi/idp_portfolio.pbids` (in this repo) is a Power BI data-source file — a
colleague double-clicks it and Power BI opens straight to the folder connection.
Edit the path inside it to your store location first.

## Notes

- **Schema stability**: column order in the CSVs is a fixed contract; new
  columns are only ever added at the end, so a scheduled refresh never breaks on
  a reorder/rename.
- **`mitigation_actions`** is a single ` | `-delimited string (Power BI-friendly).
  Split it in Power Query if you want one row per action.
- **First run**: the store and CSVs are created on the first live extraction.
  Until then, Power BI has nothing to connect to — process one contract first.
