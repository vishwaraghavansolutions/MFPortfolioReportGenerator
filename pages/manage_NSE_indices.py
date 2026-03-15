"""
manage_NSE_indices.py — NSE Index Dashboard
Data loading  : st.cache_data reads parquets directly — no singleton issues.
Agent usage   : IndexAgent used only for Benchmark Lookup tab.
Run           : streamlit run manage_NSE_indices.py
"""
from __future__ import annotations
import re, sys
from pathlib import Path
import pandas as pd
import streamlit as st
import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from agents import IndexAgent
from agents._store import (
    INDEX_RETURN_COLS, INDEX_RISK_COLS, INDEX_VAL_COLS, INDEX_ALL_NUM_COLS,
)

# ─── Dirs ──────────────────────────────────────────────────────────────────────
PDF_DIR     = Path("data/pdfs")
PARQUET_DIR = Path("data")
PDF_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_DIR.mkdir(parents=True, exist_ok=True)

# ─── Constants ─────────────────────────────────────────────────────────────────
MONTH_ABBR = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
               "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
MONTH_NAME = {1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",
              7:"July",8:"August",9:"September",10:"October",11:"November",12:"December"}
SECTION_ICONS = {"Broad Market Indices":"\U0001f3db","Sectoral Indices":"\U0001f3ed",
                 "Strategy Indices":"\u265f","Thematic Indices":"\U0001f3af","Unknown":"\u2753"}
RETURN_LABELS = {"return_1m":"1M","return_3m":"3M","return_1yr":"1 Yr",
                 "return_3yr":"3 Yr","return_5yr":"5 Yr"}
_SKIP_NAMES  = {"mf_benchmark_map","category_index_mapping"}
NUMERIC_COLS = list(INDEX_ALL_NUM_COLS)
SECTION_LABELS = {"Broad Market Indices","Sectoral Indices","Strategy Indices","Thematic Indices"}
SKIP_PAT = re.compile(
    r"^(index name|returns|volatility|beta|correlation|r\^?2|p/e|p/b|dividend"
    r"|1m|3m|1 yr|3 yr|5 yr|based on|returns for|p/e,|index returns"
    r"|-\s*returns|- index|-\s*p/e)", re.IGNORECASE)


# ─── PDF helpers ───────────────────────────────────────────────────────────────
def _infer_date(fname):
    stem = Path(fname).stem.upper()
    m = re.search(
        r"(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?"
        r"|JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
        r"(\d{4})", stem)
    if m:
        return int(m.group(2)), MONTH_ABBR.get(m.group(1)[:3])
    m = re.search(r"(\d{4})[_\-]?(\d{2})", stem)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 2000 <= y <= 2099 and 1 <= mo <= 12:
            return y, mo
    return None, None

def _pdf_label(p):
    y, m = _infer_date(p.name)
    return f"{MONTH_NAME.get(m,'?')} {y}  \u00b7  {p.name}" if y else p.stem

def _clean(v):
    return "" if v is None else str(v).strip().replace(",", "")

def _is_data_row(row):
    if not row or len(row) < 13: return False
    first = _clean(row[0])
    if not first or SKIP_PAT.match(first): return False
    return _clean(row[1]).replace("-","").replace(".","").isdigit()

def _detect_section(row):
    if not row: return None
    first = _clean(row[0])
    return next((l for l in SECTION_LABELS if first.lower() == l.lower()), None)

def _parse_row(row):
    cells = [_clean(c) for c in row]
    while len(cells) < 13: cells.append("")
    ret  = [cells[1],cells[3],cells[5],cells[7],cells[9]]
    risk = (cells[11].split()+["","","",""])[:4]
    val  = (cells[12].split()+["","",""])[:3]
    return {"index_name":cells[0],
            "return_1m":ret[0],"return_3m":ret[1],"return_1yr":ret[2],
            "return_3yr":ret[3],"return_5yr":ret[4],
            "volatility_1yr":risk[0],"beta_1yr":risk[1],
            "correlation_1yr":risk[2],"r2_1yr":risk[3],
            "pe":val[0],"pb":val[1],"dividend_yield":val[2]}

def _parse_pdf(pdf_path):
    records, section = [], "Unknown"
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    if not row: continue
                    sec = _detect_section(row)
                    if sec: section = sec; continue
                    if _is_data_row(row):
                        p = _parse_row(row)
                        p["section"] = section
                        p["source_file"] = pdf_path.name
                        records.append(p)
    if not records: return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].replace({"":None,"-":None}), errors="coerce")
    y, m = _infer_date(pdf_path.name)
    df["year"], df["month"] = y, m
    return df

def pdf_to_parquet(pdf_path):
    out = PARQUET_DIR / (pdf_path.stem + ".parquet")
    df  = _parse_pdf(pdf_path)
    if not df.empty:
        df.to_parquet(out, index=False, engine="pyarrow")
    return out, len(df)


# ─── Data loading — st.cache_data, mtime-keyed ────────────────────────────────
def _dir_mtime(path):
    try:
        mtimes = [p.stat().st_mtime for p in path.glob("*.parquet")
                  if not any(n in p.name for n in _SKIP_NAMES)]
        return max(mtimes) if mtimes else 0.0
    except Exception:
        return 0.0

@st.cache_data(show_spinner=False)
def _load_parquets(parquet_dir: str, _mtime: float) -> pd.DataFrame:
    """Read all NSE index parquets. _mtime busts cache when files change."""
    frames = []
    for p in sorted(Path(parquet_dir).glob("*.parquet")):
        if any(n in p.name for n in _SKIP_NAMES):
            continue
        try:
            df = pd.read_parquet(p)
            for col in INDEX_ALL_NUM_COLS:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            frames.append(df)
        except Exception as e:
            st.warning(f"Could not read {p.name}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def _get_df() -> pd.DataFrame:
    return _load_parquets(str(PARQUET_DIR), _mtime=_dir_mtime(PARQUET_DIR))


# ─── Agent (Benchmark Lookup only) ────────────────────────────────────────────
@st.cache_resource
def _get_agent() -> IndexAgent:
    return IndexAgent()

def _agent_run(skill: str, params: dict, *, stop_on_fail: bool = True):
    r = _get_agent().run(skill, params)
    if r.status.value != "success":
        if stop_on_fail: st.error(r.error); st.stop()
        return None
    return r.output

def _prime_agent_store():
    _agent_run("load_index_files",
               {"parquet_dir": str(PARQUET_DIR.resolve()), "force_reload": False},
               stop_on_fail=False)


# ─── Page config + CSS ────────────────────────────────────────────────────────
st.set_page_config(page_title="NSE Index Dashboard", page_icon="\U0001f4ca",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif}
.stApp{background:#0d1520;color:#e2e8f0}
[data-testid="stSidebar"]{background:#0a1018!important;border-right:1px solid #1e3a5f!important}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p,[data-testid="stSidebar"] .stMarkdown{color:#64748b!important;font-family:'JetBrains Mono',monospace!important;font-size:.72rem!important}
.app-header{display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #1e3a5f;padding-bottom:1.2rem;margin-bottom:1.8rem}
.app-eyebrow{font-size:.65rem;font-family:'JetBrains Mono',monospace;color:#3b82f6;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.4rem}
.app-title{font-size:1.85rem;font-weight:700;letter-spacing:-.03em;color:#f1f5f9;line-height:1}
.app-meta{font-family:'JetBrains Mono',monospace;font-size:.62rem;color:#475569;text-align:right;line-height:1.6}
.stTabs [data-baseweb="tab-list"]{background:#131f2e;border:1px solid #1e3a5f;border-radius:8px;padding:3px;gap:2px;margin-bottom:1rem}
.stTabs [data-baseweb="tab"]{font-family:'Inter',sans-serif;font-size:.82rem;font-weight:500;color:#64748b!important;background:transparent;border-radius:6px;padding:7px 20px}
.stTabs [aria-selected="true"]{background:#1e3a5f!important;color:#93c5fd!important}
.sec-head{font-size:.62rem;font-family:'JetBrains Mono',monospace;color:#475569;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid #1e3a5f;padding-bottom:.4rem;margin:1.4rem 0 .8rem}
.kpi-strip{display:flex;gap:1px;background:#1e3a5f;border-radius:10px;overflow:hidden;margin-bottom:1.4rem;border:1px solid #1e3a5f}
.kpi-cell{flex:1;background:#131f2e;padding:1rem;text-align:center}
.kpi-cell.hi{background:#0f2744}
.kpi-num{font-size:1.6rem;font-weight:700;line-height:1;color:#f1f5f9;font-family:'JetBrains Mono',monospace}
.kpi-cell.hi .kpi-num{color:#60a5fa}
.kpi-num.pos{color:#2dd4bf!important}
.kpi-num.neg{color:#f87171!important}
.kpi-lbl{font-size:.58rem;font-family:'JetBrains Mono',monospace;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-top:.25rem}
.kpi-delta{font-size:.65rem;font-family:'JetBrains Mono',monospace;color:#334155;margin-top:.15rem}
.file-item{display:flex;align-items:center;justify-content:space-between;background:#131f2e;border:1px solid #1e3a5f;border-radius:6px;padding:.45rem .75rem;margin-bottom:.35rem;font-size:.68rem;font-family:'JetBrains Mono',monospace;color:#64748b}
.dot-ok{width:7px;height:7px;border-radius:50%;background:#0d9488;margin-right:7px;display:inline-block}
.dot-warn{width:7px;height:7px;border-radius:50%;background:#d97706;margin-right:7px;display:inline-block}
.badge{display:inline-block;background:#1e3a5f;color:#60a5fa;font-size:.62rem;font-family:'JetBrains Mono',monospace;letter-spacing:.08em;text-transform:uppercase;padding:2px 9px;border-radius:20px;margin-right:5px;margin-bottom:4px}
.period-pill{background:#0f2744;border:1px solid #1e3a5f;border-radius:7px;padding:.55rem .9rem;font-family:'JetBrains Mono',monospace;font-size:.72rem;color:#60a5fa;letter-spacing:.06em;display:inline-block;margin-top:.4rem}
.callout{background:#0f2744;border:1px solid #1e3a5f;border-left:3px solid #3b82f6;border-radius:6px;padding:.9rem 1.1rem;margin:.8rem 0;font-size:.82rem;color:#94a3b8;line-height:1.65}
.callout strong{color:#93c5fd}
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


# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div style="font-family:''JetBrains Mono'',monospace;font-size:.65rem;'
                'color:#3b82f6;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.8rem">'
                '\U0001f4c2  Index Dashboard PDFs</div>', unsafe_allow_html=True)

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        st.info(f"No PDFs in `{PDF_DIR}`. Place NSE Index Dashboard PDFs there and refresh.")
        st.stop()

    pdf_labels   = [_pdf_label(p) for p in pdf_files]
    sel_label    = st.selectbox("Select PDF", pdf_labels, label_visibility="collapsed")
    sel_pdf_path = pdf_files[pdf_labels.index(sel_label)]
    sel_parquet  = PARQUET_DIR / (sel_pdf_path.stem + ".parquet")

    if sel_parquet.exists():
        st.markdown(
            f'<div class="file-item"><span><span class="dot-ok"></span>Parquet ready</span>'
            f'<span style="color:#60a5fa">{sel_pdf_path.stem}</span></div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="file-item" style="border-color:#d97706">'
            f'<span><span class="dot-warn"></span>Not parsed</span>'
            f'<span style="color:#f59e0b">{sel_pdf_path.stem}</span></div>',
            unsafe_allow_html=True)

    btn_lbl = "\U0001f504  Refresh Parquet" if sel_parquet.exists() else "\u26a1  Parse PDF \u2192 Parquet"
    if st.button(btn_lbl, use_container_width=True):
        with st.spinner(f"Parsing {sel_pdf_path.name}\u2026"):
            try:
                _, n = pdf_to_parquet(sel_pdf_path)
                st.cache_data.clear()
                st.success(f"\u2705  {n} rows saved")
                st.rerun()
            except Exception as e:
                st.error(f"\u274c  {e}")

    if not sel_parquet.exists():
        with st.spinner(f"Auto-parsing {sel_pdf_path.name}\u2026"):
            try:
                pdf_to_parquet(sel_pdf_path)
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"\u274c  {e}"); st.stop()

    st.markdown("---")

    # Load data directly from parquets — no singletons, no agents
    df_full = _get_df()

    if df_full.empty:
        st.warning("No index data found. Parse a PDF first.")
        st.stop()

    total_rows = len(df_full)
    idx_count  = df_full["index_name"].nunique() if "index_name" in df_full.columns else 0

    if "year" not in df_full.columns or "month" not in df_full.columns:
        st.warning("Loaded data has no year/month columns.")
        st.stop()

    periods = (
        df_full[["year","month"]].dropna().drop_duplicates()
        .sort_values(["year","month"]).astype(int).to_dict(orient="records")
    )
    if not periods:
        st.warning("No period data found.")
        st.stop()

    period_labels = [f"{MONTH_NAME.get(p['month'],'?')} {p['year']}" for p in periods]
    sel_period_lbl = st.selectbox("Period", list(reversed(period_labels)),
                                  label_visibility="visible", key="sb_period")
    sel_period = next(p for p in periods
                      if f"{MONTH_NAME.get(p['month'],'?')} {p['year']}" == sel_period_lbl)
    sel_year  = sel_period["year"]
    sel_month = sel_period["month"]

    st.markdown("---")
    st.markdown('<div style="font-family:''JetBrains Mono'',monospace;font-size:.65rem;'
                'color:#3b82f6;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.6rem">'
                '\U0001f50d  Filters</div>', unsafe_allow_html=True)

    df_period = df_full[
        (df_full["year"]  == sel_year) &
        (df_full["month"] == sel_month)
    ].copy()

    all_secs = sorted(df_period["section"].dropna().unique())
    sel_secs = st.multiselect("Sections", all_secs, default=all_secs, key="sb_secs")

    st.markdown("---")
    st.markdown('<div style="font-family:''JetBrains Mono'',monospace;font-size:.65rem;'
                'color:#3b82f6;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.6rem">'
                '\u2699\ufe0f  Display</div>', unsafe_allow_html=True)
    top_n = st.slider("Top N per chart", 5, 50, 15, key="sb_topn")


# ─── Main ─────────────────────────────────────────────────────────────────────
df = df_period[df_period["section"].isin(sel_secs)].copy()
sel_month_lbl = MONTH_NAME.get(sel_month, str(sel_month))

badges = "".join(f'<span class="badge">{SECTION_ICONS.get(s,"")} {s}</span>' for s in sel_secs)
st.markdown(f"""
<div class="app-header">
  <div>
    <div class="app-eyebrow">NSE Indices Limited \u00b7 Index Dashboard</div>
    <div class="app-title">{sel_month_lbl} {sel_year}</div>
    <div style="margin-top:.7rem">{badges}</div>
  </div>
  <div class="app-meta">{idx_count} indices \u00b7 {total_rows} rows<br>{len(periods)} period(s)</div>
</div>
""", unsafe_allow_html=True)

if df.empty:
    st.warning("No data for the selected period and filters.")
    st.stop()

total   = len(df)
pos_1m  = int((df["return_1m"] > 0).sum()) if "return_1m" in df.columns else 0
avg_1yr = df["return_1yr"].mean()           if "return_1yr" in df.columns else float("nan")
avg_pe  = df["pe"].mean()                   if "pe" in df.columns else float("nan")
ret_cls = "pos" if avg_1yr >= 0 else "neg"
pos_cls = "pos" if pos_1m > total / 2 else "neg"

st.markdown(f"""
<div class="kpi-strip">
  <div class="kpi-cell hi"><div class="kpi-num">{total}</div><div class="kpi-lbl">Indices</div>
    <div class="kpi-delta">{len(sel_secs)} section(s)</div></div>
  <div class="kpi-cell"><div class="kpi-num {pos_cls}">{pos_1m}</div>
    <div class="kpi-lbl">Positive 1M</div><div class="kpi-delta">{pos_1m/total*100:.0f}%</div></div>
  <div class="kpi-cell"><div class="kpi-num {ret_cls}">{avg_1yr:.1f}%</div>
    <div class="kpi-lbl">Avg 1-Yr Return</div><div class="kpi-delta">across selected</div></div>
  <div class="kpi-cell"><div class="kpi-num">{avg_pe:.1f}x</div>
    <div class="kpi-lbl">Avg P/E</div><div class="kpi-delta">price / earnings</div></div>
</div>
""", unsafe_allow_html=True)

def _cr(val):
    try: v=float(val); return f"color:{'#2dd4bf' if v>=0 else '#f87171'};font-weight:500"
    except: return ""

def _styled(view, color_cols=None):
    skip={"Index","Section","index_name","section","source_file","year","month"}
    num=[c for c in view.columns if c not in skip]
    s=view.style.format({c:"{:.2f}" for c in num},na_rep="\u2014")
    if color_cols: s=s.map(_cr,subset=color_cols)
    return s.set_properties(**{"background-color":"#0d1a2e","color":"#e2e8f0",
                               "font-family":"JetBrains Mono, monospace","font-size":"0.76rem"})

tab_ov,tab_ret,tab_risk,tab_val,tab_bm,tab_raw = st.tabs([
    "\U0001f4ca  Overview","\U0001f4c8  Returns","\u26a1  Risk",
    "\U0001f4b0  Valuation","\U0001f50d  Benchmark Lookup","\U0001f5c3\ufe0f  Raw Data"])

with tab_ov:
    for sec in sel_secs:
        sdf=df[df["section"]==sec]; icon=SECTION_ICONS.get(sec,"")
        with st.expander(f"{icon}  {sec}  ({len(sdf)} indices)", expanded=True):
            cols=["index_name"]+INDEX_RETURN_COLS+["volatility_1yr","pe","pb","dividend_yield"]
            cols=[c for c in cols if c in sdf.columns]
            view=sdf[cols].rename(columns={"index_name":"Index","return_1m":"1M %","return_3m":"3M %",
                "return_1yr":"1 Yr %","return_3yr":"3 Yr %","return_5yr":"5 Yr %",
                "volatility_1yr":"Volatility","pe":"P/E","pb":"P/B","dividend_yield":"Div Yield"})
            rcols=[c for c in ["1M %","3M %","1 Yr %","3 Yr %","5 Yr %"] if c in view.columns]
            st.dataframe(_styled(view,rcols),use_container_width=True,hide_index=True)

with tab_ret:
    c1,c2=st.columns([1,2])
    with c1:
        r_sec=st.selectbox("Section",sel_secs,key="rs",
                           format_func=lambda s:f"{SECTION_ICONS.get(s,'')} {s}")
        r_period=st.radio("Period",list(RETURN_LABELS.values()),horizontal=True,key="rp")
    p_col={v:k for k,v in RETURN_LABELS.items()}[r_period]
    cdf=(df[df["section"]==r_sec][["index_name",p_col]].dropna()
         .sort_values(p_col,ascending=False).head(top_n))
    with c2:
        st.markdown(f'<div class="sec-head">Top {top_n} \u00b7 {r_sec} \u00b7 {r_period}</div>',unsafe_allow_html=True)
        if not cdf.empty: st.bar_chart(cdf.set_index("index_name")[p_col],color="#3b82f6")
    st.markdown("---")
    ar=(df[["index_name","section"]+INDEX_RETURN_COLS].sort_values(p_col,ascending=False)
        .rename(columns={"index_name":"Index","section":"Section",
                         **{c:RETURN_LABELS[c]+" %" for c in INDEX_RETURN_COLS}}))
    rl=[RETURN_LABELS[c]+" %" for c in INDEX_RETURN_COLS]
    st.dataframe(_styled(ar,rl),use_container_width=True,hide_index=True)

with tab_risk:
    ra=[c for c in INDEX_RISK_COLS if c in df.columns]
    if not ra:
        st.info("No risk columns found.")
    else:
        fmt=lambda c:c.replace("_"," ").replace("1yr","1Y").title()
        c1,c2=st.columns(2)
        with c1: x_ax=st.selectbox("X axis",ra,key="rx",format_func=fmt)
        with c2: y_ax=st.selectbox("Y axis",ra,index=min(1,len(ra)-1),key="ry",format_func=fmt)
        sdf=df[["index_name","section",x_ax,y_ax,"return_1yr"]].dropna()
        if not sdf.empty:
            st.scatter_chart(sdf.rename(columns={x_ax:fmt(x_ax),y_ax:fmt(y_ax)}),
                             x=fmt(x_ax),y=fmt(y_ax),color="section",size="return_1yr",
                             use_container_width=True)
        st.markdown("---")
        rt=(df[["index_name","section"]+ra].rename(columns={
            "index_name":"Index","section":"Section",
            "volatility_1yr":"Volatility 1Y","beta_1yr":"Beta 1Y",
            "correlation_1yr":"Correlation 1Y","r2_1yr":"R\u00b2 1Y"}))
        st.dataframe(rt.style.format({c:"{:.3f}" for c in rt.columns if c not in ("Index","Section")},
                                     na_rep="\u2014").set_properties(**{
            "background-color":"#0d1a2e","color":"#e2e8f0",
            "font-family":"JetBrains Mono, monospace","font-size":"0.76rem"}),
            use_container_width=True,hide_index=True)

with tab_val:
    va=[c for c in INDEX_VAL_COLS if c in df.columns]
    if va:
        rv={"pe":"Avg P/E","pb":"Avg P/B","dividend_yield":"Avg Div Yield %"}
        agg=(df.groupby("section")[va].mean().reset_index()
             .rename(columns={"section":"Section",**rv}))
        dc=[c for c in agg.columns if c!="Section"]
        st.markdown('<div class="sec-head">Average valuation by section</div>',unsafe_allow_html=True)
        st.dataframe(agg.style.format({c:"{:.2f}" for c in dc},na_rep="\u2014")
                     .background_gradient(cmap="Blues",subset=dc)
                     .set_properties(**{"font-family":"JetBrains Mono, monospace","font-size":"0.8rem"}),
                     use_container_width=True,hide_index=True)
        st.markdown("---")
        cl={"Avg P/E":"pe","Avg P/B":"pb","Avg Div Yield %":"dividend_yield"}
        c1,c2=st.columns(2)
        with c1: vm=st.selectbox("Metric",dc,key="vm")
        with c2: vs=st.selectbox("Section",sel_secs,key="vs",
                                 format_func=lambda s:f"{SECTION_ICONS.get(s,'')} {s}")
        rc=cl.get(vm,"pe")
        vdf=(df[df["section"]==vs][["index_name",rc]].dropna()
             .sort_values(rc,ascending=False).head(top_n).set_index("index_name"))
        if not vdf.empty:
            st.markdown(f'<div class="sec-head">{vm} \u00b7 {vs}</div>',unsafe_allow_html=True)
            st.bar_chart(vdf,color="#818cf8")

with tab_bm:
    st.markdown("""<div class="callout">
      <strong>Benchmark Lookup</strong> \u2014 powered by <code>IndexAgent.get_benchmark_values</code>.<br>
      4-tier fuzzy match: exact \u2192 TRI-normalised \u2192 substring \u2192 reverse substring.
    </div>""", unsafe_allow_html=True)
    bc1,bc2,bc3=st.columns([3,1,1])
    with bc1:
        bq=st.text_input("Index name",placeholder="e.g.  NIFTY 100 TRI  or  Nifty Bank",
                         key="bm_query",label_visibility="collapsed")
    with bc2:
        ay=sorted({p["year"] for p in periods},reverse=True)
        by=st.selectbox("Year",["Latest"]+ay,key="bm_year")
    with bc3:
        am=sorted({p["month"] for p in periods})
        ml=["Latest"]+[MONTH_NAME[m] for m in am]
        bml=st.selectbox("Month",ml,key="bm_month")

    if bq.strip():
        _prime_agent_store()
        bp={"benchmark":bq.strip()}
        if by!="Latest": bp["year"]=int(by)
        if bml!="Latest": bp["month"]=next((k for k,v in MONTH_NAME.items() if v==bml),None)
        bo=_agent_run("get_benchmark_values",bp,stop_on_fail=False)
        if bo is None:
            st.error(f"No match for **{bq}**. Try a shorter name.")
        else:
            p=bo.get("period",{}); metrics=bo.get("metrics",{})
            ps=f"{MONTH_NAME.get(p.get('month'),'')} {p.get('year','')}" if p else "\u2014"
            st.markdown(f"""
            <div style="margin:1rem 0 .4rem">
              <span class="badge">\u2713 matched</span>
              <span style="font-weight:600;color:#f1f5f9;font-size:1rem">{bo.get("matched_as","")}</span>
              <span style="color:#475569;font-family:'JetBrains Mono',monospace;font-size:.72rem;margin-left:.8rem">{bo.get("section","")}</span>
            </div>
            <div class="period-pill">\U0001f4c5  {ps}</div>""", unsafe_allow_html=True)

            rc=[c for c in INDEX_RETURN_COLS if c in metrics and metrics[c] is not None]
            if rc:
                st.markdown('<div class="sec-head">Returns</div>',unsafe_allow_html=True)
                cells="".join(f'<div class="kpi-cell"><div class="kpi-num {"pos" if metrics[c]>=0 else "neg"}">{metrics[c]:.2f}%</div><div class="kpi-lbl">{RETURN_LABELS.get(c,c)}</div></div>' for c in rc)
                st.markdown(f'<div class="kpi-strip">{cells}</div>',unsafe_allow_html=True)
            rv={c:metrics[c] for c in INDEX_RISK_COLS if c in metrics and metrics[c] is not None}
            if rv:
                st.markdown('<div class="sec-head">Risk Metrics</div>',unsafe_allow_html=True)
                rl={"volatility_1yr":"Volatility 1Y","beta_1yr":"Beta 1Y","correlation_1yr":"Correlation 1Y","r2_1yr":"R\u00b2 1Y"}
                cells="".join(f'<div class="kpi-cell"><div class="kpi-num" style="font-size:1.3rem">{v:.3f}</div><div class="kpi-lbl">{rl.get(c,c)}</div></div>' for c,v in rv.items())
                st.markdown(f'<div class="kpi-strip">{cells}</div>',unsafe_allow_html=True)
            vv={c:metrics[c] for c in INDEX_VAL_COLS if c in metrics and metrics[c] is not None}
            if vv:
                st.markdown('<div class="sec-head">Valuation</div>',unsafe_allow_html=True)
                vl={"pe":"P/E Ratio","pb":"P/B Ratio","dividend_yield":"Div Yield %"}
                cells="".join(f'<div class="kpi-cell"><div class="kpi-num" style="font-size:1.3rem">{v:.2f}</div><div class="kpi-lbl">{vl.get(c,c)}</div></div>' for c,v in vv.items())
                st.markdown(f'<div class="kpi-strip">{cells}</div>',unsafe_allow_html=True)
    else:
        st.markdown('<div class="sec-head">All loaded indices</div>',unsafe_allow_html=True)
        names=sorted(df_full["index_name"].dropna().unique()) if not df_full.empty else []
        st.dataframe(pd.DataFrame({"Index Name":names}),use_container_width=True,hide_index=True,height=400)

with tab_raw:
    st.markdown(f'<div class="sec-head">{len(df):,} rows \u00b7 {sel_month_lbl} {sel_year}</div>',unsafe_allow_html=True)
    srch=st.text_input("\U0001f50e Search",placeholder="e.g. Nifty Bank",key="raw_srch")
    out=df if not srch else df[df["index_name"].str.contains(srch,case=False,na=False)]
    rcav=[c for c in INDEX_RETURN_COLS if c in out.columns]
    st.dataframe(out.reset_index(drop=True).style.map(_cr,subset=rcav)
                 .format({c:"{:.2f}" for c in INDEX_ALL_NUM_COLS if c in out.columns},na_rep="\u2014")
                 .set_properties(**{"background-color":"#0d1a2e","color":"#e2e8f0",
                                    "font-family":"JetBrains Mono, monospace","font-size":"0.74rem"}),
                 use_container_width=True,hide_index=True)
    st.download_button("\u2b07\ufe0f  Download CSV",out.to_csv(index=False).encode(),
                       file_name=f"nse_{sel_year}_{sel_month:02d}.csv",mime="text/csv")

st.markdown("""<hr>
<div style="text-align:center;color:#334155;font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.1em">
NSE INDICES LIMITED \u00b7 DATA FOR REFERENCE PURPOSES ONLY \u00b7 NOT INVESTMENT ADVICE</div>
""", unsafe_allow_html=True)