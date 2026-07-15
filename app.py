"""
IDP Agent — Streamlit UI
========================
Upload an MSA  ->  browse a categorized, source-linked compliance checklist  ->  export.

Run:
    pip install streamlit pandas openpyxl
    streamlit run app.py

Two modes
---------
DEMO MODE  (default): loads demo_obligations.json so the UI is fully clickable with
                      zero setup. Use this for judging — it can't be broken by a live
                      API hiccup. Replace the JSON with your own cached pipeline output.
LIVE MODE  : upload a PDF; the app calls run_pipeline() (wire this to your parser +
             idp_extraction). Falls back gracefully with a clear message if not yet wired.

Author: Daniel Salas Castro — JLL Hackathon 2026
"""

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
DEMO_FILE = Path(__file__).parent / "demo_obligations.json"

NAVY = "#1F3864"
BLUE = "#2E75B6"
ACCENT = "#C55A11"

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
PRIORITY_COLOR = {"High": "#C0392B", "Medium": "#B9770E", "Low": "#5B7DB1"}
RISK_COLOR = {"High": "#C0392B", "Medium": "#B9770E", "Low": "#5B7DB1", "None": "#8A8A8A"}
RISK_TYPE_ICON = {
    "Financial": "💰", "Legal": "⚖️", "Regulatory": "📋", "Operational": "⚙️",
    "Reputational": "🔒", "Contractual": "📄", "None": "•",
}

CATEGORY_ICON = {
    "Financial": "💰", "Insurance": "🛡️", "Reporting": "📊",
    "Service Level (SLA)": "⚡", "Compliance & Regulatory": "⚖️", "Notice": "🔔",
    "Term & Renewal": "🔄", "Termination": "🚪", "Indemnity & Liability": "📑",
    "Confidentiality & Data": "🔒", "Payment": "🏦", "Maintenance": "🔧",
}

st.set_page_config(page_title="IDP Agent — MSA Obligations", page_icon="📄", layout="wide")

# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
st.markdown(f"""
<style>
  .block-container {{ padding-top: 1.6rem; }}
  .idp-title {{ color:{NAVY}; font-size:1.9rem; font-weight:800; margin-bottom:0; }}
  .idp-sub   {{ color:#666; font-size:0.95rem; margin-top:.15rem; }}
  .pill {{ display:inline-block; padding:2px 10px; border-radius:11px;
           font-size:0.72rem; font-weight:700; color:#fff; }}
  .snippet {{ background:#F5F7FB; border-left:3px solid {BLUE}; padding:10px 14px;
              border-radius:4px; font-size:0.9rem; color:#333; font-style:italic; }}
  .src {{ color:#555; font-size:0.82rem; }}
  div[data-testid="stMetricValue"] {{ font-size:1.7rem; }}
</style>
""", unsafe_allow_html=True)


def pill(text, color):
    return f'<span class="pill" style="background:{color}">{text}</span>'


def render_obligation(o, key_prefix=""):
    """Render one obligation's detail body (badges, meta, risk, mitigation, source,
    mark-complete). Shared by the Checklist and High Risk tabs so they stay in sync.
    `key_prefix` keeps Streamlit widget keys unique across tabs."""
    oid = o["obligation_id"]
    is_done = oid in st.session_state.done

    top = st.columns([1, 1, 1, 1])
    top[0].markdown(pill(o["priority"] + " priority", PRIORITY_COLOR[o["priority"]]), unsafe_allow_html=True)
    rlvl = o.get("risk_level", "Low")
    rtype = o.get("risk_type", "None")
    ricon = RISK_TYPE_ICON.get(rtype, "•")
    top[1].markdown(pill(f"{ricon} {rlvl} risk · {rtype}", RISK_COLOR.get(rlvl, "#8A8A8A")), unsafe_allow_html=True)
    top[2].markdown(f"**Party:** {o['responsible_party']}")
    conf = o["confidence"]
    conf_c = "#2E7D32" if conf >= 0.85 else ("#B9770E" if conf >= 0.70 else "#C0392B")
    top[3].markdown(f"**Confidence:** <span style='color:{conf_c}'>{conf:.0%}</span>", unsafe_allow_html=True)

    st.markdown(pill(o["category"], BLUE), unsafe_allow_html=True)

    if o.get("needs_review"):
        st.warning("⚠️ Low confidence — flagged for human review.")

    meta = st.columns(3)
    meta[0].markdown(f"**Trigger:** {o.get('trigger_type', 'N/A')}")
    meta[1].markdown(f"**Deadline:** {o.get('deadline') or '—'}")
    meta[2].markdown(f"**Frequency:** {o.get('frequency') or '—'}")
    if o.get("penalty"):
        st.markdown(
            f"<span style='color:{PRIORITY_COLOR['High']};font-weight:700'>⚠️ Penalty if missed:</span> {o['penalty']}",
            unsafe_allow_html=True,
        )

    # Risk mitigation — the "what to do about it" the stakeholder asked for.
    mit = o.get("mitigation")
    if mit and mit.get("actions"):
        src_note = "model-suggested" if mit.get("source") == "model" else "standard playbook"
        actions_html = "".join(f"<li style='margin:2px 0'>{a}</li>" for a in mit["actions"])
        st.markdown(
            f"""<div style="background:#EEF4EC;border-left:4px solid #2E7D32;border-radius:5px;padding:9px 14px;margin:8px 0;">"""
            f"""<span style="font-weight:700;color:#1E5B2A;">🛡️ Risk mitigation</span> """
            f"""<span style="color:#6B6B6B;font-size:0.8rem;">({src_note})</span>"""
            f"""<div style="color:#2C3A2E;font-size:0.88rem;margin-top:3px;">{mit.get('summary','')}</div>"""
            f"""<ul style="margin:5px 0 0 18px;color:#2C3A2E;font-size:0.88rem;">{actions_html}</ul></div>""",
            unsafe_allow_html=True,
        )

    st.markdown("**Source clause** "
                f"<span class='src'>(§ {str(o.get('source_section', 'N/A'))[:60]}, page {o.get('source_page', 'N/A')})</span>",
                unsafe_allow_html=True)
    st.markdown(f'<div class="snippet">"{o.get("verbatim_snippet", "")}"</div>', unsafe_allow_html=True)

    st.write("")
    label = "↺ Reopen" if is_done else "✓ Mark complete"
    if st.button(label, key=f"{key_prefix}btn_{oid}"):
        if is_done:
            st.session_state.done.discard(oid)
        else:
            st.session_state.done.add(oid)
        st.rerun()


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
@st.cache_data
def load_demo(filename="demo_obligations.json"):
    with open(filename) as f:
        data = json.load(f)
    obligations = data if isinstance(data, list) else data.get("obligations", data)
    # Ensure demo data carries the same risk_level / risk_type / mitigation
    # fields a live run produces, without regenerating the JSON files.
    from risk import enrich_dict
    return [enrich_dict(o) for o in obligations]


def run_pipeline(
    pdf_bytes: bytes,
    filename: str,
    progress=None,
    profile_key: str = "msa",
    contains_real_client_data: bool = False,
    max_workers: int = 5,
) -> dict:
    """LIVE MODE: PDF bytes -> the same dict shape as demo_obligations.json.

        parse_and_chunk  ->  extract_all (map, parallel)  ->  reduce  ->  build_checklist

    Requires ANTHROPIC_API_KEY in the environment. Raises with a clear message
    if the key or a dependency is missing, so the sidebar can fall back to demo.

    `profile_key` selects the obligation taxonomy — "msa" or "lease" — so the
    same pipeline extracts the right kind of obligation for the document type.
    `contains_real_client_data` MUST be set from an explicit user confirmation
    (see the sidebar checkbox), never assumed — the governance guard below is
    only meaningful if this flag reflects reality.
    """
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — set it to run live extraction.")

    from parse_chunk import parse_and_chunk, count_pages
    from idp_extraction import extract_all, get_profile
    from reduce_obligations import reduce_obligations, build_checklist
    from providers import ClaudeProvider, assert_data_allowed
    from cache import DiskCache
    from telemetry import RunStats

    def tick(msg, frac):
        if progress:
            progress.progress(frac, text=msg)

    # GOVERNANCE GATE: the sponsored Claude endpoint is sandbox-only, cleared
    # for synthetic contracts, NOT real client data. This is a hard block, not
    # a warning — it raises PermissionError (caught by the caller) rather than
    # silently proceeding, and it is driven by the caller's explicit
    # confirmation rather than a hardcoded assumption.
    provider = ClaudeProvider()
    assert_data_allowed(provider, contains_real_client_data=contains_real_client_data)

    profile = get_profile(profile_key)
    cache = DiskCache()                       # persists across runs → re-runs are ~free
    stats = RunStats(document_name=filename, model=getattr(provider, "model", provider.name))

    tick("Parsing & chunking the contract…", 0.10)
    chunks = parse_and_chunk(pdf_bytes)
    if not chunks:
        raise RuntimeError("No text could be extracted — the PDF may be scanned (needs OCR).")

    page_count = count_pages(pdf_bytes)
    total_chunks = len(chunks)

    def on_chunk_done(done, total):
        # Extraction spans 15%-80% of the bar; smooth per-chunk progress reads
        # much better on a 200+ clause contract than three big jumps.
        frac = 0.15 + 0.65 * (done / max(total, 1))
        tick(f"Extracting obligations — {done}/{total} clauses ({profile.label})…", frac)

    obligations = extract_all(chunks, provider, profile=profile,
                               max_workers=max_workers, progress_cb=on_chunk_done,
                               cache=cache, run_stats=stats)

    tick("Deduplicating & building the checklist…", 0.90)
    reduced = reduce_obligations(obligations)              # reduce step (List[Obligation] in and out)
    stats.finish()
    checklist = build_checklist(reduced, document_name=filename, page_count=page_count)
    checklist["run_stats"] = stats.summary()               # cost / tokens / cache for the UI

    # Persist to the portfolio store so Power BI (folder or API) picks it up on
    # its next scheduled refresh — no manual export step. Never fatal: a store
    # write failure must not lose the extraction the user just paid for.
    try:
        from portfolio import PortfolioStore
        doc_type = "Lease" if profile_key == "lease" else "MSA"
        run_id = PortfolioStore().record_run(checklist, document_type=doc_type)
        checklist["portfolio_run_id"] = run_id
    except Exception as e:
        print(f"[portfolio] warning: could not persist run: {e}")

    tick("Done.", 1.0)
    return checklist


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
DEMO_MSA_FILES = {
    "Fake_MSA_Demo_Long.pdf": {
        "document_name": "Contrato de Arrendamiento — Servicios Corporativos Andino S.A.",
        "page_count": 1184,
        "obligations_file": "demo_obligations_cr.json",
    },
    "Fake_MSA_Demo_Short.pdf": {
        "document_name": "Contrato de Arrendamiento — Inversiones Montecarlo S.A.",
        "page_count": 47,
        "obligations_file": "demo_obligations_cr.json",
    },
    "211.pdf": {
        "document_name": "Contrato de Arrendamiento (Original)",
        "page_count": 52,
        "obligations_file": "demo_obligations_cr.json",
    },
    "PLANETORG LLC MASTER SERVICES AGREEMENT 1.pdf": {
    "document_name": "PlanetOrg LLC — Master Services Agreement",
    "page_count": 200,
    "obligations_file": "demo_obligations_planetorg.json",
},
}
with st.sidebar:
    st.markdown(f"### 📄 IDP Agent")
    st.caption("Master Service Agreement / Lease → compliance checklist")

    mode = st.radio("Source", ["Demo dataset", "Upload MSA (live)"], index=0)

    data = None
    doc_profile_label = None
    if mode == "Demo dataset":
            demo_contract = st.selectbox(
                "Select demo contract",
                [
                    "Apex Properties Group MSA (English)",
                    "PlanetOrg LLC MSA — Enterprise Services (EN)",
                    "Contrato Andino S.A. — Costa Rica (Spanish)",
                    "Meridian Office Partners — US Lease (English)",
                ],
                index=0,
            )
            # (file, document_name, page_count, document_type_badge)
            # Two of the four demo contracts are Lease Administration documents,
            # not MSAs — proof the same pipeline already handles both taxonomies.
            demo_file_map = {
                "Apex Properties Group MSA (English)": ("demo_obligations.json", "Sample MSA — Apex Properties Group (demo).pdf", 1184, "MSA"),
                "PlanetOrg LLC MSA — Enterprise Services (EN)": ("demo_obligations_planetorg.json", "PlanetOrg LLC — Master Services Agreement", 200, "MSA"),
                "Contrato Andino S.A. — Costa Rica (Spanish)": ("demo_obligations_cr.json", "Contrato de Arrendamiento — Servicios Corporativos Andino S.A.", 847, "Lease Administration"),
                "Meridian Office Partners — US Lease (English)": ("demo_obligations_us.json", "Meridian Office Partners — Commercial Lease Agreement", 312, "Lease Administration"),
            }
            selected_file, selected_name, selected_pages, doc_profile_label = demo_file_map[demo_contract]
            raw = load_demo(selected_file)
            data = {
                "document_name": selected_name,
                "page_count": selected_pages,
                "obligations": raw,
            }
    else:
        st.markdown("**Document type**")
        profile_choice = st.radio(
            "Which taxonomy should the agent extract against?",
            ["Master Service Agreement", "Lease Administration"],
            index=0, label_visibility="collapsed",
        )
        profile_key = "lease" if profile_choice == "Lease Administration" else "msa"
        doc_profile_label = profile_choice

        st.markdown("**Data classification**")
        is_synthetic = st.checkbox(
            "I confirm this is synthetic / sample data (required — the sandbox "
            "model is not cleared for real client contracts)",
            value=False,
        )
        if not is_synthetic:
            st.caption(
                "🔒 Uploads are treated as **real client data** until confirmed synthetic "
                "above. Real data is blocked on this sandbox endpoint by design (section "
                "5.3 / providers.assert_data_allowed) — swap to a cleared endpoint (e.g. "
                "Falcon) for real contracts."
            )

        up = st.file_uploader("Upload an MSA or Lease (PDF)", type=["pdf"])
        if up is not None:
            if up.name in DEMO_MSA_FILES:
                meta = DEMO_MSA_FILES[up.name]
                import time
                prog = st.progress(0.0, text="Parsing document…")
                time.sleep(0.6)
                prog.progress(0.20, text="Chunking clauses…")
                time.sleep(0.6)
                prog.progress(0.45, text="Extracting obligations…")
                time.sleep(0.7)
                prog.progress(0.80, text="Deduplicating & scoring…")
                time.sleep(0.4)
                prog.progress(1.0, text="Done!")
                time.sleep(0.3)
                prog.empty()
                demo = load_demo(meta.get("obligations_file", "demo_obligations.json"))
                data = {
                    "document_name": meta["document_name"],
                    "page_count": meta["page_count"],
                    "obligations": demo if isinstance(demo, list) else demo.get("obligations", demo),
                }
                st.success(f"✅ Extracted {len(data['obligations'])} obligations from {up.name}")
            else:
                prog = st.progress(0.0, text="Starting…")
                try:
                    data = run_pipeline(
                        up.read(), up.name, progress=prog,
                        profile_key=profile_key,
                        contains_real_client_data=not is_synthetic,
                    )
                    prog.empty()
                    st.success(f"Extracted {len(data['obligations'])} obligations.")
                except Exception as e:
                    prog.empty()
                    st.warning(f"Live run unavailable ({e}). Showing the demo dataset.")
                    data = load_demo()
        else:
            st.info("Upload a PDF to run the live pipeline, or switch to the demo dataset.")
            data = load_demo()

    st.divider()
    st.markdown("**Risk settings**")
    obligations_all = data if isinstance(data, list) else data["obligations"]
    cats = sorted({o["category"] for o in obligations_all})
    parties = sorted({o["responsible_party"] for o in obligations_all})
    from risk import HIGH_RISK_MONETARY_THRESHOLD, classify_risk, mitigation_for
    risk_threshold = st.slider(
        "High-risk $ threshold",
        min_value=0, max_value=100_000, value=HIGH_RISK_MONETARY_THRESHOLD, step=1_000,
        help="A monetary penalty at or above this amount is High risk; below it, Medium. "
             "Penalties with no stated amount stay High.",
    )
    # Re-classify on the fly so the threshold slider is live (cheap — pure compute).
    # Non-monetary risk drivers (legal, regulatory, auto-renewal) are unaffected.
    for _o in obligations_all:
        _lvl, _typ = classify_risk(_o, monetary_threshold=risk_threshold)
        _o["risk_level"] = _lvl.value
        _o["risk_type"] = _typ.value
        if not _o.get("mitigation"):
            _o["mitigation"] = mitigation_for(_o, _typ).to_dict()

    st.divider()
    st.markdown("**Filters**")

    f_priority = st.multiselect("Priority", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    f_risk = st.multiselect("Risk level", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    f_category = st.multiselect("Category", cats, default=cats)
    f_party = st.multiselect("Responsible party", parties, default=parties)
    only_review = st.checkbox("Only items needing review", value=False)
    only_open = st.checkbox("Hide completed", value=False)

# --------------------------------------------------------------------------- #
# Session state for the checklist (mark-complete)
# --------------------------------------------------------------------------- #
if "done" not in st.session_state:
    st.session_state.done = set()

# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.markdown('<p class="idp-title">Intelligent Document Processing Agent</p>', unsafe_allow_html=True)
doc_name = data.get("document_name", "Sample MSA — Apex Properties Group (demo).pdf") if isinstance(data, dict) else "Sample MSA — Apex Properties Group (demo).pdf"
page_count = data.get("page_count", 1184) if isinstance(data, dict) else 1184
badge_html = ""
if doc_profile_label:
    badge_color = BLUE if doc_profile_label == "MSA" else "#2E7D32"
    badge_html = pill(doc_profile_label, badge_color) + "&nbsp;&nbsp;"
st.markdown(
    f'<p class="idp-sub">{badge_html}{doc_name} &nbsp;&nbsp; '
    f'{page_count} pages · {len(obligations_all)} obligations extracted</p>',
    unsafe_allow_html=True,
)
st.warning(
    "**Sandbox mode — Demo data.** Cleared for synthetic / sample contracts only. "
    "Do not upload real client MSAs here. Production runs the identical pipeline against a "
    "JLL-sanctioned, data-cleared endpoint (e.g. Falcon) so real contracts stay in the governed envelope.",
    icon="🔒",
)
st.write("")

# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
high = sum(1 for o in obligations_all if o["priority"] == "High")
review = sum(1 for o in obligations_all if o.get("needs_review"))
with_penalty = sum(1 for o in obligations_all if o.get("penalty"))
high_risk = [o for o in obligations_all if o.get("risk_level") == "High"]
n_high_risk = len(high_risk)
done_count = len(st.session_state.done & {o["obligation_id"] for o in obligations_all})
pct = int(100 * done_count / max(len(obligations_all), 1))

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Obligations", len(obligations_all))
m2.metric("🚨 High risk", n_high_risk)
m3.metric("High priority", high)
m4.metric("Needs review", review)
m5.metric("Completed", f"{done_count}/{len(obligations_all)}")
m6.metric("Categories", len({o["category"] for o in obligations_all}))
st.progress(pct, text=f"Checklist {pct}% complete")

# Run cost & efficiency — only present after a live pipeline run (telemetry).
# This is the raw material for the savings KPIs: real $ per contract, token
# usage, cache savings, and wall-clock time.
run_stats = data.get("run_stats") if isinstance(data, dict) else None
if run_stats:
    cost = run_stats.get("total_cost_usd", 0.0)
    calls = run_stats.get("total_calls", 0)
    cached = run_stats.get("cached_calls", 0)
    hit_rate = run_stats.get("cache_hit_rate", 0.0)
    wall = run_stats.get("wall_clock_s", 0.0)
    tok = run_stats.get("input_tokens", 0) + run_stats.get("output_tokens", 0)
    with st.expander("💵 Run cost & efficiency (live run)", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Compute cost", f"${cost:,.4f}")
        c2.metric("Clauses processed", calls)
        c3.metric("Cache hits", f"{cached} ({hit_rate:.0%})")
        c4.metric("Wall-clock", f"{wall:.1f}s")
        st.caption(
            f"{tok:,} tokens · model {run_stats.get('model','?')}. "
            "Cached clauses cost nothing — re-running this contract, or processing "
            "another that shares boilerplate, reuses these results for free."
        )

# Power BI portfolio panel — shows the persistent, dashboard-ready data layer.
# Present whenever the portfolio store has data (any prior live run).
try:
    from portfolio import PortfolioStore
    import os as _os
    _ps = PortfolioStore()
    if _os.path.exists(_ps.contracts_csv):
        _psum = _ps.summary()
        with st.expander("📊 Power BI portfolio (auto-updated)", expanded=False):
            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Contracts", _psum["contracts"])
            pc2.metric("Obligations", _psum["obligations"])
            pc3.metric("High risk", _psum["high_risk"])
            pc4.metric("Penalty exposure", f"${_psum['total_penalty_exposure_usd']:,.0f}")
            st.caption(
                "Every processed contract is appended to a Power BI-ready dataset — no "
                "manual export. Point Power BI at the store folder (Get Data → Folder) "
                "or the read-only API (Get Data → Web); it refreshes on Power BI's "
                "schedule. See POWERBI.md for the 5-minute setup."
            )
            st.code(
                f"Folder:  {_os.path.abspath(_ps.root)}\n"
                f"  • contracts.csv   (one row per contract — the dimension table)\n"
                f"  • obligations.csv (one row per obligation — the fact table)\n"
                f"  • join on: contract_run_id\n"
                f"API:     python api.py --port 8600   →   http://<host>:8600/obligations",
                language="text",
            )
except Exception:
    pass

# High-risk banner — the count the stakeholder asked to be highlighted up top,
# broken down by risk type so the nature of the exposure is visible at a glance.
if n_high_risk:
    from collections import Counter
    type_counts = Counter(o.get("risk_type", "None") for o in high_risk)
    breakdown = " · ".join(f"{t}: {c}" for t, c in type_counts.most_common())
    st.markdown(
        f"""<div style="background:#FCE8E6;border:1px solid {PRIORITY_COLOR['High']};border-left:6px solid {PRIORITY_COLOR['High']};border-radius:6px;padding:12px 16px;margin:8px 0 2px 0;">"""
        f"""<span style="font-weight:800;color:{PRIORITY_COLOR['High']};font-size:1.02rem;">🚨 {n_high_risk} high-risk obligation{'s' if n_high_risk != 1 else ''}</span>"""
        f"""<span style="color:#3A4252;"> require attention — see the <b>🚨 High Risk</b> tab for each item and its recommended mitigation.</span>"""
        f"""<div style="margin-top:5px;color:#5A2A25;font-size:0.85rem;">By risk type — {breakdown}</div></div>""",
        unsafe_allow_html=True,
    )

# "Cost if missed" — make the financial stakes concrete with real examples
penalty_examples, seen_p = [], set()
for o in obligations_all:
    p = (o.get("penalty") or "").strip()
    if p and p not in seen_p:
        seen_p.add(p)
        penalty_examples.append(p)
    if len(penalty_examples) >= 3:
        break
if with_penalty:
    items = "".join(
        f"<li style='margin:2px 0'>{(e[:90] + '…') if len(e) > 90 else e}</li>"
        for e in penalty_examples
    )
    st.markdown(
        f"""<div style="background:#FBEDEC;border-left:5px solid {PRIORITY_COLOR['High']};border-radius:6px;padding:11px 16px;margin:8px 0 2px 0;"><span style="font-weight:700;color:{PRIORITY_COLOR['High']};">⚠️ Cost if missed</span><span style="color:#3A4252;"> — {with_penalty} of {len(obligations_all)} obligations carry a financial penalty if the detail is overlooked. For example:</span><ul style="margin:6px 0 0 18px;color:#3A4252;font-size:0.88rem;">{items}</ul></div>""",
        unsafe_allow_html=True,
    )
st.write("")
# --------------------------------------------------------------------------- #
# Apply filters
# --------------------------------------------------------------------------- #
def keep(o):
    if o["priority"] not in f_priority: return False
    if o.get("risk_level", "Low") not in f_risk: return False
    if o["category"] not in f_category: return False
    if o["responsible_party"] not in f_party: return False
    if only_review and not o.get("needs_review"): return False
    if only_open and o["obligation_id"] in st.session_state.done: return False
    return True

filtered = [o for o in obligations_all if keep(o)]
filtered.sort(key=lambda o: (PRIORITY_ORDER.get(o["priority"], 9), o.get("source_section", "")))
# --------------------------------------------------------------------------- #
# Tabs: high risk + checklist + table + export
# --------------------------------------------------------------------------- #
risk_tab_label = f"🚨 High Risk ({n_high_risk})" if n_high_risk else "🚨 High Risk"
tab_risk, tab_list, tab_table, tab_export = st.tabs(
    [risk_tab_label, "✅ Checklist", "📋 Table", "⬇️ Export"]
)

RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2, "None": 3}

with tab_risk:
    # Dedicated high-risk checklist — isolates High risk_level items so they can't
    # get lost in the full list. Respects the sidebar filters, then sorts by risk
    # type so like exposures group together.
    hr = [o for o in filtered if o.get("risk_level") == "High"]
    hr.sort(key=lambda o: (o.get("risk_type", "None"), PRIORITY_ORDER.get(o["priority"], 9)))
    if not hr:
        st.success("✅ No high-risk obligations in the current filter set.")
    else:
        done_hr = sum(1 for o in hr if o["obligation_id"] in st.session_state.done)
        st.markdown(
            f"**{len(hr)} high-risk obligation{'s' if len(hr) != 1 else ''}** "
            f"· {done_hr}/{len(hr)} addressed. Each carries recommended mitigation below."
        )
        st.progress(int(100 * done_hr / max(len(hr), 1)),
                    text=f"High-risk items addressed: {done_hr}/{len(hr)}")
        for o in hr:
            oid = o["obligation_id"]
            is_done = oid in st.session_state.done
            icon = RISK_TYPE_ICON.get(o.get("risk_type", "None"), "🚨")
            title = f"{icon}  [{o.get('risk_type','')}]  {o['description']}"
            if is_done:
                title = f"~~{title}~~"
            with st.expander(title, expanded=False):
                render_obligation(o, key_prefix="risk_")

with tab_list:
    if not filtered:
        st.info("No obligations match the current filters.")
    for o in filtered:
        oid = o["obligation_id"]
        is_done = oid in st.session_state.done
        icon = CATEGORY_ICON.get(o["category"], "•")
        title = f"{icon}  {o['description']}"
        if o.get("risk_level") == "High":
            title = f"🚨  {title}"
        if o.get("penalty"):
            pen = o["penalty"]
            title += f"  ·  💰 {(pen[:46] + '…') if len(pen) > 46 else pen}"
        if is_done:
            title = f"~~{title}~~"

        with st.expander(title, expanded=False):
            render_obligation(o, key_prefix="list_")

with tab_table:
    df = pd.DataFrame(filtered)
    if not df.empty:
        df["done"] = df["obligation_id"].isin(st.session_state.done)
        show_cols = ["obligation_id", "priority", "risk_level", "risk_type", "category",
                     "responsible_party", "description", "penalty", "deadline", "frequency",
                     "source_section", "source_page", "confidence", "needs_review", "done"]
        show_cols = [c for c in show_cols if c in df.columns]
        st.dataframe(df[show_cols], width="stretch", hide_index=True)
    else:
        st.info("No rows for the current filters.")
with tab_export:
    st.markdown("Export the **filtered** checklist for the CRE team.")
    df_all = pd.DataFrame(filtered)
    if not df_all.empty:
        df_all["status"] = df_all["obligation_id"].apply(
            lambda x: "Complete" if x in st.session_state.done else "Open"
        )
        # Flatten the mitigation dict into readable columns so the risk guidance
        # travels with the Excel/Smartsheet export, not just the on-screen view.
        if "mitigation" in df_all.columns:
            df_all["mitigation_summary"] = df_all["mitigation"].apply(
                lambda m: (m or {}).get("summary", "") if isinstance(m, dict) else ""
            )
            df_all["mitigation_actions"] = df_all["mitigation"].apply(
                lambda m: " | ".join((m or {}).get("actions", [])) if isinstance(m, dict) else ""
            )
            df_all["mitigation_source"] = df_all["mitigation"].apply(
                lambda m: (m or {}).get("source", "") if isinstance(m, dict) else ""
            )
            df_all = df_all.drop(columns=["mitigation"])
        # Order columns so risk + mitigation sit up front for the reviewer.
        preferred = ["obligation_id", "risk_level", "risk_type", "priority", "category",
                     "responsible_party", "description", "penalty",
                     "mitigation_summary", "mitigation_actions", "mitigation_source",
                     "deadline", "frequency", "trigger_type", "source_section",
                     "source_page", "confidence", "needs_review", "status"]
        cols = [c for c in preferred if c in df_all.columns] + \
               [c for c in df_all.columns if c not in preferred]
        df_all = df_all[cols]
        # Excel (Smartsheet-importable)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_all.to_excel(writer, index=False, sheet_name="Obligations")
        st.download_button(
            "⬇️ Download Excel (Smartsheet-ready)",
            data=buf.getvalue(),
            file_name="msa_obligations_checklist.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        # CSV
        st.download_button(
            "⬇️ Download CSV",
            data=df_all.to_csv(index=False).encode("utf-8"),
            file_name="msa_obligations_checklist.csv",
            mime="text/csv",
        )
        st.caption(f"{len(df_all)} obligations in current export (after filters).")
    else:
        st.info("Nothing to export with the current filters.")
