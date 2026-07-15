# Deploy — clickable demo on Streamlit Community Cloud

Goal: a public `*.streamlit.app` link judges can open with no setup — running the real
UI on **synthetic demo data**.

## ⚠️ Read first — deploy in DEMO MODE only
- **Do NOT add the Anthropic API key** as a Streamlit secret.
- Without the key, the app stays in demo mode; the "Upload MSA (live)" path fails
  safely and falls back to the demo dataset with a message.
- Why: the sponsored Claude access is sandbox / synthetic-only, and a public live
  endpoint would also burn the shared quota.
- Never commit a real MSA or any API key to the repo. It holds only synthetic data + code.

## Files the repo needs (in the root)
```
app.py
providers.py
idp_extraction.py
parse_chunk.py
reduce_obligations.py
demo_obligations.json
requirements.txt
README.md            (optional)
```
The heavy imports (anthropic, pymupdf) are lazy — loaded only inside `run_pipeline` —
so demo mode runs even though those packages are only used by live mode.

## Steps
1. **GitHub repo** — create a new repo (public is simplest; private also works). Push
   the files above to the `main` branch, in the repo root.
2. **Sign in** — go to `share.streamlit.io` and sign in with GitHub; authorize access.
3. **Create app** — in your workspace, click **"Create app"** (upper-right) → choose
   "Deploy a public app from GitHub."
4. **Point it at the app** — set:
   - Repository: `your-username/your-repo`
   - Branch: `main`
   - Main file path: `app.py`
5. **(Optional) Advanced settings** — pick Python `3.11` or `3.12`; set a custom
   subdomain (e.g. `idp-agent` → `https://idp-agent.streamlit.app`).
6. **Deploy** — build takes ~2–5 minutes. You get the public URL.
7. **Leave secrets empty** — confirms demo-only mode.

## Verify the live link
Open the URL and confirm:
- Demo dataset loads automatically.
- Filters (priority / category / party) work.
- Expanding an obligation shows the source clause + verbatim snippet.
- Mark-complete updates the progress bar.
- Export downloads Excel / CSV.

## Optional: restrict access
If you don't want it fully public, enable a per-app **viewer allow-list** in the app
settings to limit who can open it (e.g. just the judging panel).

## Troubleshooting
- **Build fails on a package** → in Advanced settings set Python `3.11`, redeploy.
- **`requirements.txt` not found** → it must be in the repo root.
- **App sleeps after inactivity** → free apps idle out; open the link a few minutes
  before you present so it's warm.
- **Changes not showing** → push to `main`; Community Cloud redeploys automatically.

## Demo-day note
Present the walkthrough from this public link (demo data, can't break). If you want to
show a *live* extraction, run that locally with your API key on a synthetic PDF — keep
it off the public deployment.
