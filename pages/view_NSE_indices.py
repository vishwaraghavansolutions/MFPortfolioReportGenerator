"""
manage_NSE_indices.py — NSE Index Dashboard
Loads *_Index_Master.csv files from gs://winrich_shared/Master/ via GCSStorageAgent.
Year dropdown → select year → display index daily values.
"""
from __future__ import annotations
import re

import pandas as pd
import streamlit as st

from utils.auth import require_login
from utils.navbar import navbar
from agents.gcs_storage_agent import GCSStorageAgent
from agents.base import AgentStatus

# ─── Page config + CSS ────────────────────────────────────────────────────────
st.set_page_config(page_title="NSE Index Dashboard", page_icon="📈",
                   layout="wide", initial_sidebar_state="collapsed")

require_login()
navbar()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif}
.stApp{background:#0d1520;color:#e2e8f0}
.app-header{display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #1e3a5f;padding-bottom:1.2rem;margin-bottom:1.8rem}
.app-eyebrow{font-size:.65rem;font-family:'JetBrains Mono',monospace;color:#3b82f6;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.4rem}
.app-title{font-size:1.85rem;font-weight:700;letter-spacing:-.03em;color:#f1f5f9;line-height:1}
.sec-head{font-size:.62rem;font-family:'JetBrains Mono',monospace;color:#475569;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid #1e3a5f;padding-bottom:.4rem;margin:1.4rem 0 .8rem}
.kpi-strip{display:flex;gap:1px;background:#1e3a5f;border-radius:10px;overflow:hidden;margin-bottom:1.4rem;border:1px solid #1e3a5f}
.kpi-cell{flex:1;background:#131f2e;padding:1rem;text-align:center}
.kpi-cell.hi{background:#0f2744}
.kpi-num{font-size:1.6rem;font-weight:700;line-height:1;color:#f1f5f9;font-family:'JetBrains Mono',monospace}
.kpi-lbl{font-size:.58rem;font-family:'JetBrains Mono',monospace;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-top:.25rem}
.stSelectbox>div>div,.stMultiSelect>div>div{background:#131f2e!important;border-color:#1e3a5f!important;color:#e2e8f0!important}
.stTextInput>div>div>input{background:#131f2e!important;border-color:#1e3a5f!important;color:#e2e8f0!important}
.stButton>button{font-family:'Inter',sans-serif;font-weight:600;font-size:.78rem;background:#1e3a5f;color:#93c5fd;border:1px solid #2d5a8e;border-radius:7px;padding:.42rem 1rem}
.stButton>button:hover{background:#2d5a8e;color:#bfdbfe;border-color:#3b82f6}
[data-testid="stDataFrame"]{border:1px solid #1e3a5f;border-radius:8px;overflow:hidden}
hr{border-color:#1e3a5f;margin:1rem 0}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-thumb{background:#1e3a5f;border-radius:4px}
</style>
""", unsafe_allow_html=True)

# ─── GCS helpers ──────────────────────────────────────────────────────────────
_BUCKET = "winrich_shared"
_PREFIX = "Master"
_AGENT  = GCSStorageAgent()


@st.cache_data(ttl=300, show_spinner=False)
def _list_index_files() -> list[dict]:
    """Return list of {year, filename} dicts for *_Index_Master.csv in GCS."""
    resp = _AGENT.run("list_ranking_files", {
        "bucket_name": _BUCKET,
        "prefix":      _PREFIX,
    })
    if resp.status != AgentStatus.SUCCESS:
        st.error(f"Cannot list GCS files: {resp.error}")
        st.stop()

    result = []
    for f in resp.output.get("files", []):
        fname = f["filename"]
        m = re.match(r"(\d{4})_Index_Master\.csv", fname, re.IGNORECASE)
        if m:
            result.append({"year": int(m.group(1)), "filename": fname})
    return sorted(result, key=lambda x: x["year"], reverse=True)


@st.cache_data(ttl=300, show_spinner=False)
def _load_year(filename: str) -> pd.DataFrame:
    resp = _AGENT.run("load_ranking_csv", {
        "bucket_name": _BUCKET,
        "prefix":      _PREFIX,
        "filename":    filename,
    })
    if resp.status != AgentStatus.SUCCESS:
        st.error(f"Cannot load {filename}: {resp.error}")
        st.stop()
    df = resp.output["dataframe"].copy()
    df.columns = df.columns.str.strip()
    return df


# ─── Detect columns ───────────────────────────────────────────────────────────
def _detect_cols(df: pd.DataFrame):
    """Return (index_col, date_col, value_cols) by inspecting column names."""
    cols_lower = {c.lower(): c for c in df.columns}

    index_col = next(
        (cols_lower[k] for k in cols_lower
         if "index" in k and "name" in k), None
    ) or next(
        (cols_lower[k] for k in cols_lower if "index" in k), None
    ) or df.columns[0]

    date_col = next(
        (cols_lower[k] for k in cols_lower
         if k in ("date", "trading date", "trade date", "tradedate")), None
    ) or next(
        (cols_lower[k] for k in cols_lower if "date" in k), None
    )

    skip = {index_col, date_col}
    value_cols = [c for c in df.columns if c not in skip]
    return index_col, date_col, value_cols


# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="app-header">
  <div>
    <div class="app-eyebrow">NSE Indices · Index Master</div>
    <div class="app-title">Index Daily Values</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ─── Load file list ───────────────────────────────────────────────────────────
index_files = _list_index_files()
if not index_files:
    st.warning(f"No `*_Index_Master.csv` files found in `gs://{_BUCKET}/{_PREFIX}/`.")
    st.stop()

years      = [f["year"] for f in index_files]
file_by_yr = {f["year"]: f["filename"] for f in index_files}

# ─── Controls row ─────────────────────────────────────────────────────────────
ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([1, 2, 2, 1])

with ctrl1:
    sel_year = st.selectbox("Year", years, key="sel_year")

with ctrl4:
    if st.button("🔄 Refresh", use_container_width=True):
        _list_index_files.clear()
        _load_year.clear()
        st.rerun()

# ─── Load data ────────────────────────────────────────────────────────────────
with st.spinner(f"Loading {file_by_yr[sel_year]} from GCS…"):
    df_raw = _load_year(file_by_yr[sel_year])

index_col, date_col, value_cols = _detect_cols(df_raw)

# ─── Parse date if found ──────────────────────────────────────────────────────
if date_col:
    df_raw[date_col] = pd.to_datetime(df_raw[date_col], errors="coerce")

# ─── Parse numeric value cols ─────────────────────────────────────────────────
for c in value_cols:
    df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce")

# ─── KPI strip ────────────────────────────────────────────────────────────────
n_indices = df_raw[index_col].nunique() if index_col in df_raw.columns else 0
n_rows    = len(df_raw)
date_min  = df_raw[date_col].min().strftime("%d %b %Y") if date_col and pd.notna(df_raw[date_col].min()) else "—"
date_max  = df_raw[date_col].max().strftime("%d %b %Y") if date_col and pd.notna(df_raw[date_col].max()) else "—"

st.markdown(f"""
<div class="kpi-strip">
  <div class="kpi-cell hi"><div class="kpi-num">{sel_year}</div><div class="kpi-lbl">Year</div></div>
  <div class="kpi-cell"><div class="kpi-num">{n_indices}</div><div class="kpi-lbl">Indices</div></div>
  <div class="kpi-cell"><div class="kpi-num">{n_rows:,}</div><div class="kpi-lbl">Rows</div></div>
  <div class="kpi-cell"><div class="kpi-num" style="font-size:1rem">{date_min}</div><div class="kpi-lbl">From</div></div>
  <div class="kpi-cell"><div class="kpi-num" style="font-size:1rem">{date_max}</div><div class="kpi-lbl">To</div></div>
</div>
""", unsafe_allow_html=True)

# ─── Filters ──────────────────────────────────────────────────────────────────
all_indices = sorted(df_raw[index_col].dropna().unique().tolist())

with ctrl2:
    search = st.text_input("🔍 Search index", placeholder="e.g. NIFTY 50", key="idx_search")

with ctrl3:
    sel_indices = st.multiselect("Filter indices", all_indices,
                                  default=[], placeholder="All indices", key="idx_filter")

# Apply filters
df = df_raw.copy()
if sel_indices:
    df = df[df[index_col].isin(sel_indices)]
elif search:
    df = df[df[index_col].str.contains(search, case=False, na=False)]

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab_table, tab_chart, tab_pivot = st.tabs(["📋 Daily Values", "📈 Chart", "🔢 Pivot by Index"])

with tab_table:
    st.markdown(f'<div class="sec-head">{len(df):,} rows · {df[index_col].nunique()} indices</div>',
                unsafe_allow_html=True)
    srch2 = st.text_input("🔎 Quick search", key="tbl_srch", label_visibility="collapsed",
                           placeholder="Search within results…")
    view = df if not srch2 else df[df[index_col].str.contains(srch2, case=False, na=False)]
    st.dataframe(view.reset_index(drop=True), use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download CSV",
                       data=view.to_csv(index=False).encode(),
                       file_name=f"NSE_Index_{sel_year}_filtered.csv",
                       mime="text/csv")

with tab_chart:
    # Need a single index + a numeric column to chart
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        chart_index = st.selectbox("Index", all_indices, key="chart_idx")
    numeric_value_cols = [c for c in value_cols
                          if pd.api.types.is_numeric_dtype(df_raw[c])]
    with chart_col2:
        chart_val = st.selectbox("Value column", numeric_value_cols, key="chart_val") \
                    if numeric_value_cols else None

    if chart_val and date_col:
        cdf = df_raw[df_raw[index_col] == chart_index][[date_col, chart_val]].dropna()
        cdf = cdf.sort_values(date_col).set_index(date_col)
        if not cdf.empty:
            st.line_chart(cdf[chart_val], use_container_width=True)
        else:
            st.info("No data for the selected index.")
    elif not date_col:
        st.info("No date column detected in this CSV — cannot draw a time-series chart.")
    else:
        st.info("No numeric value columns found.")

with tab_pivot:
    # Latest value per index — show as a summary table
    if date_col and numeric_value_cols:
        piv_val = st.selectbox("Value column", numeric_value_cols, key="piv_val")
        latest = (df_raw.sort_values(date_col)
                        .groupby(index_col)[piv_val].last()
                        .reset_index()
                        .rename(columns={index_col: "Index", piv_val: f"Latest {piv_val}"}))
        if search:
            latest = latest[latest["Index"].str.contains(search, case=False, na=False)]
        if sel_indices:
            latest = latest[latest["Index"].isin(sel_indices)]
        latest = latest.sort_values(f"Latest {piv_val}", ascending=False).reset_index(drop=True)
        st.markdown(f'<div class="sec-head">Latest {piv_val} per index ({len(latest)} shown)</div>',
                    unsafe_allow_html=True)
        st.dataframe(latest, use_container_width=True, hide_index=True)
    else:
        st.info("Pivot requires a date column and at least one numeric value column.")

st.markdown("""<hr>
<div style="text-align:center;color:#334155;font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.1em">
NSE INDICES LIMITED · DATA FOR REFERENCE PURPOSES ONLY · NOT INVESTMENT ADVICE</div>
""", unsafe_allow_html=True)
