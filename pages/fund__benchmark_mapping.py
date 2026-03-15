"""
category_mapping.py — Fund Benchmark Mapping
══════════════════════════════════════════════
Tab 1 — Edit Mapping  (fund-TYPE level)
Tab 2 — View Funds    (read-only)
Tab 3 — SEBI Reference (benchmark methodology)

Run:  streamlit run category_mapping.py
"""

from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from agents.mf_benchmark_agent import MutualFundBenchmarkAgent, _BENCHMARK_MAP

MAPPING_FILE = "data/mf_benchmark_map.parquet"

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Fund Benchmark Mapping",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Design system ─────────────────────────────────────────────────────────────
# Palette: deep navy background, slate cards, blue accent, muted status colours
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Global background ── */
.stApp { background: #0d1520; color: #e2e8f0; }
[data-testid="stSidebar"] { display: none; }

/* ── App header ── */
.app-header {
    display: flex; align-items: flex-end; justify-content: space-between;
    border-bottom: 1px solid #1e3a5f;
    padding-bottom: 1.2rem; margin-bottom: 1.8rem;
}
.app-eyebrow {
    font-size: 0.65rem; font-family: 'JetBrains Mono', monospace;
    color: #3b82f6; letter-spacing: 0.12em; text-transform: uppercase;
    margin-bottom: 0.4rem;
}
.app-title {
    font-size: 1.85rem; font-weight: 700; letter-spacing: -0.03em;
    color: #f1f5f9; line-height: 1;
}
.app-meta {
    font-family: 'JetBrains Mono', monospace; font-size: 0.62rem;
    color: #475569; text-align: right; line-height: 1.6;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: #131f2e; border: 1px solid #1e3a5f;
    border-radius: 8px; padding: 3px; gap: 2px; margin-bottom: 1rem;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Inter', sans-serif; font-size: 0.82rem; font-weight: 500;
    color: #64748b !important; background: transparent;
    border-radius: 6px; padding: 7px 20px;
}
.stTabs [aria-selected="true"] {
    background: #1e3a5f !important; color: #93c5fd !important;
}

/* ── Section heading ── */
.sec-head {
    font-size: 0.62rem; font-family: 'JetBrains Mono', monospace;
    color: #475569; letter-spacing: 0.1em; text-transform: uppercase;
    border-bottom: 1px solid #1e3a5f; padding-bottom: 0.4rem;
    margin: 1.4rem 0 0.8rem;
}

/* ── Stats strip ── */
.stats-strip {
    display: flex; gap: 1px; background: #1e3a5f;
    border-radius: 10px; overflow: hidden; margin-bottom: 1.4rem;
    border: 1px solid #1e3a5f;
}
.stat-cell {
    flex: 1; background: #131f2e; padding: 1rem 1rem; text-align: center;
}
.stat-cell.hi { background: #0f2744; }
.stat-num {
    font-size: 1.6rem; font-weight: 700; line-height: 1;
    color: #f1f5f9; font-family: 'JetBrains Mono', monospace;
}
.stat-cell.hi .stat-num { color: #60a5fa; }
.stat-num.warn  { color: #f59e0b !important; }
.stat-num.alert { color: #f87171 !important; }
.stat-lbl {
    font-size: 0.58rem; font-family: 'JetBrains Mono', monospace;
    color: #475569; text-transform: uppercase; letter-spacing: 0.08em;
    margin-top: 0.25rem;
}

/* ── Cards / rows ── */
.ft-row {
    background: #131f2e; border: 1px solid #1e3a5f; border-radius: 8px;
    padding: 0.7rem 1rem; margin-bottom: 0.35rem;
    transition: border-color 0.15s;
}
.ft-row:hover  { border-color: #2d5a8e; }
.ft-row.pending { border-color: #d97706; background: #1a1500; }
.ft-row.saved   { border-color: #0d9488; background: #051412; }

.ft-name  { font-weight: 600; font-size: 0.86rem; color: #e2e8f0; }
.ft-count {
    font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; color: #475569;
}
.ft-mixed {
    font-family: 'JetBrains Mono', monospace; font-size: 0.6rem;
    color: #f59e0b; background: #1c1400; border: 1px solid #78350f;
    border-radius: 3px; padding: 1px 6px; display: inline-block; margin-top: 3px;
}

/* ── Column header for ft table ── */
.ft-header {
    display: grid;
    grid-template-columns: 2fr 0.5fr 3fr 1.2fr 0.7fr;
    gap: 0.8rem; padding: 0 1rem;
    font-size: 0.58rem; font-family: 'JetBrains Mono', monospace;
    color: #334155; text-transform: uppercase; letter-spacing: 0.08em;
    margin-bottom: 0.25rem;
}

/* ── Source badges ── */
.src-sebi {
    display: inline-block; background: #042f2e; color: #2dd4bf;
    border: 1px solid #134e4a; font-size: 0.58rem;
    font-family: 'JetBrains Mono', monospace; padding: 2px 7px; border-radius: 3px;
}
.src-override {
    display: inline-block; background: #1c1400; color: #fbbf24;
    border: 1px solid #78350f; font-size: 0.58rem;
    font-family: 'JetBrains Mono', monospace; padding: 2px 7px; border-radius: 3px;
}
.src-none {
    display: inline-block; background: #1a0a0a; color: #f87171;
    border: 1px solid #7f1d1d; font-size: 0.58rem;
    font-family: 'JetBrains Mono', monospace; padding: 2px 7px; border-radius: 3px;
}
.badge-pending {
    display: inline-block; background: #1c1400; color: #f59e0b;
    border: 1px solid #d97706; font-size: 0.58rem;
    font-family: 'JetBrains Mono', monospace; padding: 2px 7px; border-radius: 3px;
}

/* ── Toasts ── */
.toast-ok {
    background: #042f2e; border-left: 3px solid #0d9488; border-radius: 6px;
    padding: 0.65rem 1rem; font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem; color: #5eead4; margin: 0.5rem 0;
}
.toast-err {
    background: #1a0a0a; border-left: 3px solid #dc2626; border-radius: 6px;
    padding: 0.65rem 1rem; font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem; color: #fca5a5; margin: 0.5rem 0;
}

/* ── Legend strip ── */
.legend-strip {
    display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap;
    font-family: 'JetBrains Mono', monospace; font-size: 0.63rem;
    color: #475569; padding: 0.5rem 0;
}
.legend-sep { color: #1e3a5f; }

/* ── SEBI reference table ── */
.sebi-section {
    background: #131f2e; border: 1px solid #1e3a5f; border-radius: 10px;
    padding: 1.4rem 1.6rem; margin-bottom: 1.2rem;
}
.sebi-section h4 {
    font-size: 0.7rem; font-family: 'JetBrains Mono', monospace;
    color: #3b82f6; text-transform: uppercase; letter-spacing: 0.1em;
    margin: 0 0 0.6rem; font-weight: 500;
}
.sebi-section p {
    font-size: 0.82rem; color: #94a3b8; line-height: 1.7; margin: 0 0 0.8rem;
}
.sebi-section a { color: #60a5fa; text-decoration: none; }
.sebi-section a:hover { text-decoration: underline; }

.bm-group-head {
    font-size: 0.62rem; font-family: 'JetBrains Mono', monospace;
    color: #3b82f6; text-transform: uppercase; letter-spacing: 0.1em;
    border-bottom: 1px solid #1e3a5f; padding-bottom: 0.3rem;
    margin: 1rem 0 0.4rem;
}
.bm-row {
    display: grid; grid-template-columns: 1.4fr 2fr;
    gap: 1rem; padding: 0.45rem 0.5rem;
    font-size: 0.8rem; border-radius: 4px;
}
.bm-row:nth-child(even) { background: #0d1520; }
.bm-cat  { color: #cbd5e1; font-weight: 500; }
.bm-idx  { color: #64748b; font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; }

/* ── Callout box ── */
.callout {
    background: #0f2744; border: 1px solid #1e3a5f; border-left: 3px solid #3b82f6;
    border-radius: 6px; padding: 0.9rem 1.1rem; margin: 0.8rem 0;
    font-size: 0.82rem; color: #94a3b8; line-height: 1.65;
}
.callout strong { color: #93c5fd; }

/* ── Streamlit widget overrides ── */
.stSelectbox > div > div {
    background: #131f2e !important; border-color: #1e3a5f !important;
    color: #e2e8f0 !important;
}
.stTextInput > div > div > input {
    background: #131f2e !important; border-color: #1e3a5f !important;
    color: #e2e8f0 !important;
}
.stButton > button {
    font-family: 'Inter', sans-serif; font-weight: 600;
    font-size: 0.78rem; background: #1e3a5f; color: #93c5fd;
    border: 1px solid #2d5a8e; border-radius: 7px; padding: 0.42rem 1rem;
}
.stButton > button:hover { background: #2d5a8e; color: #bfdbfe; border-color: #3b82f6; }
[data-testid="stDataFrame"] {
    border: 1px solid #1e3a5f; border-radius: 8px; overflow: hidden;
}
hr { border-color: #1e3a5f; margin: 0.8rem 0; }
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-thumb { background: #1e3a5f; border-radius: 4px; }

/* ── Expander ── */
.streamlit-expanderHeader {
    background: #131f2e !important; border: 1px solid #1e3a5f !important;
    border-radius: 8px !important; color: #93c5fd !important;
    font-size: 0.82rem !important;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Agent + helpers
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def get_agent() -> MutualFundBenchmarkAgent:
    return MutualFundBenchmarkAgent()

agent = get_agent()

_SS = {
    "t1_ac"      : None,
    "t1_pending" : {},
    "t1_saved"   : set(),
    "t2_ac"      : None,
    "t2_ft"      : None,
    "t2_search"  : "",
    "t2_amc"     : "",
    "t2_bm"      : "All",
    "t2_src"     : "All",
    "toast"      : None,
}
for k, v in _SS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _run(skill: str, stop_on_fail=True, **kwargs):
    r = agent.run(skill, {**kwargs, "mapping_file": MAPPING_FILE})
    if r.status.value != "success":
        if stop_on_fail:
            st.error(r.error); st.stop()
        return None
    return r.output


def _ac_options(ac_out: dict) -> tuple[list, list]:
    vals   = ac_out["asset_classes"]
    counts = ac_out["counts"]
    labels = ["— select —"] + [f"{a}  ({counts[a]:,})" for a in vals]
    return vals, labels


def _ft_options(ft_out: dict) -> tuple[list, list]:
    vals   = ft_out["fund_types"]
    counts = ft_out["counts"]
    labels = ["— select —"] + [f"{f}  ({counts[f]:,})" for f in vals]
    return vals, labels


def _src_badge(src: str) -> str:
    cls = {"SEBI Circular": "src-sebi", "User Override": "src-override"}.get(src, "src-none")
    return f'<span class="{cls}">{src}</span>'


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.toast:
    kind, msg = st.session_state.toast
    cls = "toast-ok" if kind == "ok" else "toast-err"
    st.markdown(f'<div class="{cls}">{msg}</div>', unsafe_allow_html=True)
    st.session_state.toast = None

st.markdown(f"""
<div class="app-header">
  <div>
    <div class="app-eyebrow">Fund · Benchmark Mapping Tool</div>
    <div class="app-title">Benchmark Mapping</div>
  </div>
  <div class="app-meta">
    MutualFundBenchmarkAgent · 10 skills<br>
    SEBI Oct-2021 Circular · {MAPPING_FILE}
  </div>
</div>
""", unsafe_allow_html=True)

ac_out = _run("get_asset_classes")
ac_vals, ac_labels = _ac_options(ac_out)


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

tab_edit, tab_view, tab_ref = st.tabs([
    "✏️  Edit Mapping",
    "📋  View Funds",
    "📖  SEBI Reference",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — EDIT MAPPING
# ══════════════════════════════════════════════════════════════════════════════

with tab_edit:

    fb1, fb2 = st.columns([3, 5])
    with fb1:
        prev_ac1 = st.session_state.t1_ac
        idx_ac1  = (ac_vals.index(prev_ac1) + 1) if prev_ac1 in ac_vals else 0
        sel_ac1  = st.selectbox("Asset Class", ac_labels, index=idx_ac1, key="t1_sb_ac")
        if sel_ac1 == "— select —":
            if st.session_state.t1_ac is not None:
                st.session_state.update(t1_ac=None, t1_pending={}, t1_saved=set())
                st.rerun()
        else:
            chosen1 = ac_vals[ac_labels.index(sel_ac1) - 1]
            if chosen1 != st.session_state.t1_ac:
                st.session_state.update(t1_ac=chosen1, t1_pending={}, t1_saved=set())
                st.rerun()

    # ── Landing ───────────────────────────────────────────────────────────────
    if not st.session_state.t1_ac:
        st.markdown('<div class="sec-head">Asset class overview — select one above</div>',
                    unsafe_allow_html=True)
        ac_df = pd.DataFrame({
            "Asset Class": ac_vals,
            "Funds"      : [ac_out["counts"][a] for a in ac_vals],
        })
        c_tbl, c_bar = st.columns([1, 2])
        with c_tbl: st.dataframe(ac_df, use_container_width=True, hide_index=True)
        with c_bar: st.bar_chart(ac_df.set_index("Asset Class"), color="#3b82f6")
        st.stop()

    # ── Load summary ──────────────────────────────────────────────────────────
    fts_out     = _run("get_fund_type_summary", asset_class=st.session_state.t1_ac)
    ft_rows     = fts_out["fund_types"]
    all_known_bms = sorted({r["benchmark"] for r in ft_rows if r["benchmark"]})

    n_uniform   = sum(1 for r in ft_rows if r["is_uniform"])
    n_mixed     = len(ft_rows) - n_uniform
    n_pending   = len(st.session_state.t1_pending)
    total_funds = sum(r["fund_count"] for r in ft_rows)

    st.markdown(f"""
    <div class="stats-strip">
      <div class="stat-cell hi">
        <div class="stat-num">{len(ft_rows)}</div>
        <div class="stat-lbl">Fund Types</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num">{total_funds:,}</div>
        <div class="stat-lbl">Total Funds</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num">{n_uniform}</div>
        <div class="stat-lbl">Uniform</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num {'warn' if n_mixed else ''}">{n_mixed}</div>
        <div class="stat-lbl">Mixed</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num {'warn' if n_pending else ''}">{n_pending}</div>
        <div class="stat-lbl">Pending</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="ft-header">
      <span>Fund Type</span><span>Funds</span>
      <span>Benchmark</span><span>Source</span><span></span>
    </div>""", unsafe_allow_html=True)

    for row in ft_rows:
        ft         = row["fund_type"]
        cur_bm     = row["benchmark"]
        src        = row["benchmark_source"]
        fund_count = row["fund_count"]
        is_uniform = row["is_uniform"]
        pending_bm = st.session_state.t1_pending.get(ft)
        is_pending = pending_bm is not None and pending_bm != cur_bm
        is_saved   = ft in st.session_state.t1_saved

        src_cls  = {"SEBI Circular":"src-sebi","User Override":"src-override"}.get(src,"src-none")
        src_html = f'<span class="{src_cls}">{src}</span>'
        mixed_html = "" if is_uniform else '<span class="ft-mixed">mixed</span>'

        c_name, c_count, c_sel, c_src, c_btn = st.columns([2, 0.5, 3, 1.2, 0.7])

        with c_name:
            st.markdown(
                f'<div style="padding:0.55rem 0">'
                f'<div class="ft-name">{ft}</div>{mixed_html}</div>',
                unsafe_allow_html=True)

        with c_count:
            st.markdown(
                f'<div class="ft-count" style="padding-top:0.75rem">{fund_count:,}</div>',
                unsafe_allow_html=True)

        with c_sel:
            display_bm = pending_bm if is_pending else cur_bm
            other_bms  = [b for b in all_known_bms if b != display_bm]
            opts       = ([display_bm] + other_bms) if display_bm else all_known_bms
            CUSTOM_OPT = "✎  Type custom benchmark…"

            custom_key = f"t1_custom_{ft}"
            if custom_key not in st.session_state:
                st.session_state[custom_key] = ""

            sel_bm = st.selectbox(
                f"_bm_{ft}", opts + [CUSTOM_OPT], index=0,
                key=f"t1_sel_{ft}", label_visibility="collapsed")
            if sel_bm == CUSTOM_OPT:
                sel_bm = st.text_input(
                    f"_txt_{ft}", st.session_state[custom_key],
                    placeholder="Enter benchmark name…",
                    key=f"t1_txt_{ft}", label_visibility="collapsed")
                st.session_state[custom_key] = sel_bm

            if sel_bm and sel_bm != cur_bm:
                st.session_state.t1_pending[ft] = sel_bm
            elif sel_bm == cur_bm and ft in st.session_state.t1_pending:
                del st.session_state.t1_pending[ft]

        with c_src:
            badge = ('<span class="badge-pending">pending</span>'
                     if is_pending else src_html)
            st.markdown(f'<div style="padding-top:0.6rem">{badge}</div>',
                        unsafe_allow_html=True)

        with c_btn:
            if is_pending:
                if st.button("Apply", key=f"t1_apply_{ft}",
                             help=f"Update all {fund_count:,} {ft} funds"):
                    r = agent.run("update_benchmark_by_fund_type", {
                        "asset_class"  : st.session_state.t1_ac,
                        "fund_type"    : ft,
                        "new_benchmark": pending_bm,
                        "mapping_file" : MAPPING_FILE,
                    })
                    if r.status.value == "success":
                        st.session_state.t1_pending.pop(ft, None)
                        st.session_state.t1_saved.add(ft)
                        st.session_state.toast = (
                            "ok", f"✓ {ft} — {r.output['updated_count']:,} fund(s) → {pending_bm}")
                        st.rerun()
                    else:
                        st.session_state.toast = ("err", r.error); st.rerun()

    if st.session_state.t1_pending:
        st.markdown("---")
        ca, cb, _ = st.columns([1.6, 1, 5])
        with ca:
            if st.button(f"💾  Apply all  ({n_pending} pending)", key="t1_apply_all"):
                errors, saved_n = [], 0
                for ft, bm in list(st.session_state.t1_pending.items()):
                    r = agent.run("update_benchmark_by_fund_type", {
                        "asset_class"  : st.session_state.t1_ac,
                        "fund_type"    : ft,
                        "new_benchmark": bm,
                        "mapping_file" : MAPPING_FILE,
                    })
                    if r.status.value == "success":
                        saved_n += r.output["updated_count"]
                        st.session_state.t1_pending.pop(ft, None)
                        st.session_state.t1_saved.add(ft)
                    else:
                        errors.append(f"{ft}: {r.error}")
                if errors:
                    st.session_state.toast = ("err", "Errors: " + "; ".join(errors))
                else:
                    st.session_state.toast = (
                        "ok", f"✓ {saved_n:,} funds updated across all pending types.")
                st.rerun()
        with cb:
            if st.button("✕  Discard all", key="t1_discard_all"):
                st.session_state.t1_pending = {}; st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — VIEW FUNDS
# ══════════════════════════════════════════════════════════════════════════════

with tab_view:

    vb1, vb2, vb3, vb4 = st.columns([2, 2, 2, 2])

    with vb1:
        prev_ac2 = st.session_state.t2_ac
        idx_ac2  = (ac_vals.index(prev_ac2) + 1) if prev_ac2 in ac_vals else 0
        sel_ac2  = st.selectbox("Asset Class", ac_labels, index=idx_ac2, key="t2_sb_ac")
        if sel_ac2 == "— select —":
            if st.session_state.t2_ac is not None:
                st.session_state.update(t2_ac=None, t2_ft=None); st.rerun()
        else:
            chosen_ac2 = ac_vals[ac_labels.index(sel_ac2) - 1]
            if chosen_ac2 != st.session_state.t2_ac:
                st.session_state.update(t2_ac=chosen_ac2, t2_ft=None); st.rerun()

    with vb2:
        ft2_out = (_run("get_fund_types", stop_on_fail=False,
                        asset_class=st.session_state.t2_ac)
                   if st.session_state.t2_ac else None)
        ft2_vals, ft2_labels = (_ft_options(ft2_out) if ft2_out
                                else ([], ["— select asset class first —"]))
        prev_ft2 = st.session_state.t2_ft
        idx_ft2  = (ft2_vals.index(prev_ft2) + 1) if prev_ft2 in ft2_vals else 0
        sel_ft2  = st.selectbox("Fund Type", ft2_labels, index=idx_ft2, key="t2_sb_ft",
                                disabled=not st.session_state.t2_ac)
        if sel_ft2 in ("— select —", "— select asset class first —"):
            if st.session_state.t2_ft is not None:
                st.session_state.t2_ft = None; st.rerun()
        elif ft2_vals:
            chosen_ft2 = ft2_vals[ft2_labels.index(sel_ft2) - 1]
            if chosen_ft2 != st.session_state.t2_ft:
                st.session_state.t2_ft = chosen_ft2; st.rerun()

    with vb3:
        st.session_state.t2_search = st.text_input(
            "Fund name", st.session_state.t2_search,
            placeholder="e.g. Direct Plan", key="t2_search_inp",
            disabled=not st.session_state.t2_ft)

    with vb4:
        st.session_state.t2_amc = st.text_input(
            "AMC", st.session_state.t2_amc,
            placeholder="e.g. SBI", key="t2_amc_inp",
            disabled=not st.session_state.t2_ft)

    if not st.session_state.t2_ac:
        st.markdown('<div class="sec-head">Select an asset class to begin</div>',
                    unsafe_allow_html=True)
        ac_df2 = pd.DataFrame({
            "Asset Class": ac_vals,
            "Funds"      : [ac_out["counts"][a] for a in ac_vals],
        })
        st.bar_chart(ac_df2.set_index("Asset Class"), color="#3b82f6")
        st.stop()

    if not st.session_state.t2_ft:
        if ft2_out:
            ft_df2 = pd.DataFrame({
                "Fund Type": ft2_vals,
                "Funds"    : [ft2_out["counts"][f] for f in ft2_vals],
            }).sort_values("Funds", ascending=False)
            st.markdown(
                f'<div class="sec-head">{st.session_state.t2_ac} — select a fund type</div>',
                unsafe_allow_html=True)
            c1, c2 = st.columns([1, 2])
            with c1: st.dataframe(ft_df2, use_container_width=True, hide_index=True)
            with c2: st.bar_chart(ft_df2.set_index("Fund Type"), color="#3b82f6")
        st.stop()

    vo = _run("get_funds_by_filter",
              asset_class=st.session_state.t2_ac,
              fund_type  =st.session_state.t2_ft,
              amc        =st.session_state.t2_amc,
              search     =st.session_state.t2_search,
              page=1, page_size=0)

    all_funds    = vo["funds"]
    v_bm_counts  = vo.get("benchmarks", {})
    v_src_counts = vo.get("sources", {})
    v_total      = vo["total"]

    st.markdown(f"""
    <div class="stats-strip">
      <div class="stat-cell hi">
        <div class="stat-num">{v_total:,}</div>
        <div class="stat-lbl">Funds</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num">{len(v_bm_counts)}</div>
        <div class="stat-lbl">Benchmarks</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num">{v_src_counts.get("SEBI Circular", 0):,}</div>
        <div class="stat-lbl">SEBI Circular</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num warn">{v_src_counts.get("User Override", 0):,}</div>
        <div class="stat-lbl">User Override</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num alert">{v_src_counts.get("Not Mapped", 0):,}</div>
        <div class="stat-lbl">Not Mapped</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="legend-strip">
      <span>Benchmark source:</span>
      <span class="src-sebi">SEBI Circular</span>
      <span class="legend-sep">·</span>
      <span>Derived from SEBI Oct-2021 circular, applied by fund category</span>
      <span class="legend-sep">·</span>
      <span class="src-override">User Override</span>
      <span class="legend-sep">·</span>
      <span>Manually set — differs from SEBI default</span>
      <span class="legend-sep">·</span>
      <span class="src-none">Not Mapped</span>
      <span class="legend-sep">·</span>
      <span>No SEBI rule and no manual mapping</span>
    </div>
    """, unsafe_allow_html=True)

    sf1, sf2 = st.columns([3, 2])
    with sf1:
        bm_opts = ["All"] + sorted(v_bm_counts)
        prev_bm = st.session_state.t2_bm
        st.session_state.t2_bm = st.selectbox(
            "Filter by benchmark", bm_opts,
            index=bm_opts.index(prev_bm) if prev_bm in bm_opts else 0,
            key="t2_bm_sel")
    with sf2:
        src_opts = ["All", "SEBI Circular", "User Override", "Not Mapped"]
        prev_src = st.session_state.t2_src
        st.session_state.t2_src = st.selectbox(
            "Filter by source", src_opts,
            index=src_opts.index(prev_src) if prev_src in src_opts else 0,
            key="t2_src_sel")

    view_df = pd.DataFrame(all_funds)
    if st.session_state.t2_bm != "All":
        view_df = view_df[view_df["benchmark"] == st.session_state.t2_bm]
    if st.session_state.t2_src != "All":
        view_df = view_df[view_df["benchmark_source"] == st.session_state.t2_src]

    st.markdown('<div class="sec-head">Benchmark distribution</div>', unsafe_allow_html=True)
    bm_dist = (
        pd.DataFrame(all_funds)
        .groupby("benchmark")["scheme_name"].count()
        .reset_index()
        .rename(columns={"scheme_name": "Fund Count"})
        .sort_values("Fund Count", ascending=False)
    )
    bc1, bc2 = st.columns([2, 3])
    with bc1: st.dataframe(bm_dist, use_container_width=True, hide_index=True)
    with bc2: st.bar_chart(bm_dist.set_index("benchmark"), color="#3b82f6")

    is_filtered = (st.session_state.t2_bm != "All" or st.session_state.t2_src != "All")
    st.markdown(
        f'<div class="sec-head">Funds — {len(view_df):,} shown'
        f'{"  (filtered)" if is_filtered else ""}</div>',
        unsafe_allow_html=True)

    show_cols = [c for c in [
        "scheme_code", "scheme_name", "fund_house",
        "benchmark", "benchmark_source", "latest_nav", "nav_date",
    ] if c in view_df.columns]

    def _style_src(val):
        return {
            "SEBI Circular" : "background:#042f2e;color:#2dd4bf",
            "User Override" : "background:#1c1400;color:#fbbf24",
            "Not Mapped"    : "background:#1a0a0a;color:#f87171",
        }.get(val, "")

    st.dataframe(
        view_df[show_cols]
        .rename(columns={
            "scheme_code"     : "Code",
            "scheme_name"     : "Fund Name",
            "fund_house"      : "AMC",
            "benchmark"       : "Benchmark",
            "benchmark_source": "Source",
            "latest_nav"      : "NAV",
            "nav_date"        : "Date",
        })
        .style.map(_style_src, subset=["Source"]),
        use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SEBI REFERENCE
# ══════════════════════════════════════════════════════════════════════════════

with tab_ref:

    st.markdown("""
    <div class="sebi-section">
      <h4>About the Benchmark Map</h4>
      <p>
        In October 2021, SEBI issued a circular (<strong>SEBI/HO/IMD/IMD-I/DOF6/P/CIR/2021/626</strong>)
        mandating that every mutual fund scheme must declare a <strong>Tier-1 benchmark index</strong>
        appropriate for its scheme category, and a Tier-2 benchmark where applicable. The intent
        was to standardise performance comparison across AMCs and make it easier for investors
        to evaluate schemes against a consistent, category-level yardstick.
      </p>
      <p>
        The <code>_BENCHMARK_MAP</code> in this tool codifies those Tier-1 assignments. Each entry
        maps a SEBI scheme-category keyword to the index SEBI prescribed for that category.
        The mapping is applied via substring matching on the <code>scheme_category</code> field
        returned by mfapi.in (e.g. <em>"Equity Scheme - Large Cap Fund"</em> → keyword match on
        <em>"Large Cap Fund"</em> → <em>NIFTY 100 Total Return Index</em>).
      </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="callout">
      <strong>How benchmark_source is determined</strong><br>
      When the parquet file's stored benchmark matches the SEBI-derived value for that category,
      the fund is classified as <span class="src-sebi">SEBI Circular</span>.
      If it differs, it is a <span class="src-override">User Override</span> — meaning
      someone has manually assigned a different index (e.g. a more precise sector index for
      a thematic fund). If SEBI has no mapping for the category and no manual value is stored,
      the fund is <span class="src-none">Not Mapped</span>.
    </div>
    """, unsafe_allow_html=True)

    # ── Render _BENCHMARK_MAP as a grouped reference table ────────────────────
    # Group entries by asset class bucket
    _GROUPS = {
        "Equity": [
            "Large Cap Fund","Large & Mid Cap Fund","Mid Cap Fund","Small Cap Fund",
            "Multi Cap Fund","Flexi Cap Fund","Focused Fund","Value Fund","Contra Fund",
            "Dividend Yield Fund","Sectoral / Thematic","Sectoral/Thematic","ELSS",
        ],
        "Hybrid": [
            "Aggressive Hybrid Fund","Conservative Hybrid Fund","Balanced Hybrid Fund",
            "Dynamic Asset Allocation","Balanced Advantage Fund","Multi Asset Allocation",
            "Arbitrage Fund","Equity Savings Fund",
        ],
        "Debt": [
            "Overnight Fund","Liquid Fund","Ultra Short Duration Fund","Low Duration Fund",
            "Money Market Fund","Short Duration Fund","Medium Duration Fund",
            "Medium to Long Duration Fund","Long Duration Fund","Dynamic Bond Fund",
            "Corporate Bond Fund","Credit Risk Fund","Banking and PSU Fund",
            "Gilt Fund","Gilt Fund with 10 year constant duration","Floater Fund",
        ],
        "Passive / Index / FoF": [
            "Index Fund","ETF","Fund of Fund","FoF",
        ],
    }

    bm_lookup = dict(_BENCHMARK_MAP)

    col_a, col_b = st.columns(2)

    for col_idx, (group, keywords) in enumerate(_GROUPS.items()):
        col = col_a if col_idx % 2 == 0 else col_b
        with col:
            rows_html = ""
            for kw in keywords:
                idx = bm_lookup.get(kw, "—")
                rows_html += (
                    f'<div class="bm-row">'
                    f'<span class="bm-cat">{kw}</span>'
                    f'<span class="bm-idx">{idx}</span>'
                    f'</div>'
                )
            st.markdown(
                f'<div class="bm-group-head">{group}</div>'
                f'<div style="background:#131f2e;border:1px solid #1e3a5f;'
                f'border-radius:8px;overflow:hidden;padding:0 0.5rem;margin-bottom:1.2rem">'
                f'{rows_html}</div>',
                unsafe_allow_html=True)

    st.markdown("""
    <div class="callout" style="margin-top:0.5rem">
      <strong>Notes on the mapping</strong><br>
      • <em>Sectoral / Thematic</em> and <em>Sectoral/Thematic</em> are listed separately because
        mfapi.in returns both spellings depending on the AMC; both resolve to NIFTY 500 TRI.<br>
      • <em>Index Fund</em> and <em>ETF</em> map to <em>Underlying Index</em> — these are
        pass-through entries; the actual index is fund-specific and should be overridden in the
        parquet file after inspection.<br>
      • <em>Fund of Fund</em> maps to the Tier-1 benchmark of its underlying fund, which again
        requires a manual lookup.<br>
      • Source: <strong>SEBI Circular SEBI/HO/IMD/IMD-I/DOF6/P/CIR/2021/626</strong>,
        dated 4 October 2021.
    </div>
    """, unsafe_allow_html=True)

# ── Footer ─────────────────────────────────────────────────────────────────────
st.markdown("""
<hr>
<div style="text-align:center;color:#334155;font-family:'JetBrains Mono',monospace;
     font-size:0.58rem;letter-spacing:0.1em">
    FOR RESEARCH PURPOSES ONLY · NOT INVESTMENT ADVICE
</div>
""", unsafe_allow_html=True)