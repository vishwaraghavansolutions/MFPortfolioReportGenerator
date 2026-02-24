"""
NSE Index Dashboard — All-in-One Streamlit App
------------------------------------------------
• Upload PDF files via the sidebar
• Parses and saves them as Parquet in data/
• Browse data by month/year with charts & tables

Run:
    pip install streamlit pandas pyarrow pdfplumber
    streamlit run app.py
"""

import os
import re
import glob
from pathlib import Path

import pandas as pd
import pdfplumber
import streamlit as st

# ── Dirs ──────────────────────────────────────────────────────────────────────
PDF_DIR     = Path("data/pdfs")
PARQUET_DIR = Path("data")
PDF_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_DIR.mkdir(parents=True, exist_ok=True)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NSE Index Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');

html, body, [class*="css"] { font-family: 'DM Mono', monospace; }
.stApp { background: #0a0e1a; color: #e2e8f0; }

[data-testid="stSidebar"] {
    background: #0d1220 !important;
    border-right: 1px solid #1e2d4a;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p {
    color: #94a3b8 !important;
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.hero {
    background: linear-gradient(135deg, #0f1e3d 0%, #0a2040 50%, #091830 100%);
    border: 1px solid #1e3a5f;
    border-radius: 16px;
    padding: 2rem 2.5rem;
    margin-bottom: 1.5rem;
    position: relative;
    overflow: hidden;
}
.hero::before {
    content: '';
    position: absolute;
    top: -60px; right: -60px;
    width: 220px; height: 220px;
    background: radial-gradient(circle, rgba(56,189,248,0.12) 0%, transparent 70%);
    border-radius: 50%;
}
.hero-title {
    font-family: 'Syne', sans-serif;
    font-size: 2.2rem;
    font-weight: 800;
    color: #f0f9ff;
    margin: 0;
    letter-spacing: -0.02em;
}
.hero-sub {
    font-size: 0.78rem;
    color: #38bdf8;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-top: 0.4rem;
}

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1rem;
    margin-bottom: 1.5rem;
}
.kpi-card {
    background: #0d1a2e;
    border: 1px solid #1e3454;
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    position: relative;
    overflow: hidden;
}
.kpi-card::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, #38bdf8, #818cf8);
}
.kpi-label {
    font-size: 0.68rem;
    color: #64748b;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-bottom: 0.4rem;
}
.kpi-value {
    font-family: 'Syne', sans-serif;
    font-size: 1.7rem;
    font-weight: 700;
    color: #e2e8f0;
}
.kpi-delta { font-size: 0.7rem; color: #38bdf8; margin-top: 0.15rem; }

.stTabs [data-baseweb="tab-list"] {
    background: #0d1220; border-radius: 10px;
    padding: 4px; gap: 4px; border: 1px solid #1e2d4a;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'DM Mono', monospace; font-size: 0.75rem;
    letter-spacing: 0.06em; color: #64748b !important;
    background: transparent; border-radius: 7px; padding: 8px 18px;
}
.stTabs [aria-selected="true"] {
    background: #1e3a5f !important; color: #38bdf8 !important;
}

[data-testid="stDataFrame"] {
    border: 1px solid #1e3454; border-radius: 10px; overflow: hidden;
}

.badge {
    display: inline-block; background: #1e3a5f; color: #38bdf8;
    font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase;
    padding: 3px 10px; border-radius: 20px;
    margin-right: 6px; margin-bottom: 4px;
}

.file-item {
    display: flex; align-items: center; justify-content: space-between;
    background: #0f1e3d; border: 1px solid #1e3454;
    border-radius: 8px; padding: 0.5rem 0.8rem; margin-bottom: 0.4rem;
    font-size: 0.72rem; color: #94a3b8;
}
.dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #4ade80; margin-right: 8px; display: inline-block;
}

.pos { color: #4ade80; }
.neg { color: #f87171; }

hr { border-color: #1e2d4a; margin: 1.5rem 0; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0a0e1a; }
::-webkit-scrollbar-thumb { background: #1e3454; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PDF → PARQUET  LOGIC
# ══════════════════════════════════════════════════════════════════════════════

COLUMNS = [
    "index_name", "return_1m", "return_3m", "return_1yr", "return_3yr",
    "return_5yr", "volatility_1yr", "beta_1yr", "correlation_1yr",
    "r2_1yr", "pe", "pb", "dividend_yield",
]
NUMERIC_COLS = COLUMNS[1:]

SECTION_LABELS = {
    "Broad Market Indices", "Sectoral Indices",
    "Strategy Indices",     "Thematic Indices",
}

SKIP_PATTERN = re.compile(
    r"^(index name|returns|volatility|beta|correlation|r\^?2|p/e|p/b|dividend"
    r"|1m|3m|1 yr|3 yr|5 yr|based on|returns for|p/e,|index returns"
    r"|-\s*returns|- index|-\s*p/e)",
    re.IGNORECASE,
)

MONTH_ABBR = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
MONTH_MAP = {
    1: "January",  2: "February", 3: "March",    4: "April",
    5: "May",      6: "June",     7: "July",      8: "August",
    9: "September",10: "October", 11: "November", 12: "December",
}
MONTH_INV = {v: k for k, v in MONTH_MAP.items()}


def infer_date(fname: str):
    """Return (year, month) from filename e.g. Index_Dashboard_JAN2026 → (2026, 1)."""
    stem = Path(fname).stem.upper()
    m = re.search(
        r"(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?"
        r"|JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
        r"(\d{4})", stem
    )
    if m:
        return int(m.group(2)), MONTH_ABBR.get(m.group(1)[:3])
    m = re.search(r"(\d{4})[_\-]?(\d{2})", stem)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 2000 <= y <= 2099 and 1 <= mo <= 12:
            return y, mo
    return None, None


def _clean(val):
    return "" if val is None else str(val).strip().replace(",", "")


def _is_data_row(row):
    if not row or len(row) < 13:
        return False
    first = _clean(row[0])
    if not first or SKIP_PATTERN.match(first):
        return False
    second = _clean(row[1]).replace("-", "").replace(".", "")
    return second.isdigit()


def _detect_section(row):
    if not row:
        return None
    first = _clean(row[0])
    for label in SECTION_LABELS:
        if first.lower() == label.lower():
            return label
    return None


def _parse_row(row: list) -> dict | None:
    """
    The PDF table has 13 raw columns laid out like this:
      [0]  index_name
      [1]  1M return
      [2]  (empty — merged cell artifact)
      [3]  3M return
      [4]  (empty)
      [5]  1 Yr return
      [6]  (empty)
      [7]  3 Yr return
      [8]  (empty)
      [9]  5 Yr return
      [10] (empty)
      [11] "volatility beta correlation r2"  (space-separated in one cell)
      [12] "pe pb dividend_yield"            (space-separated in one cell)

    Returns a dict of {column: value} or None if parsing fails.
    """
    cells = [_clean(c) for c in row]
    while len(cells) < 13:
        cells.append("")

    # Return columns are at odd indices 1,3,5,7,9
    return_vals = [cells[1], cells[3], cells[5], cells[7], cells[9]]

    # Risk metrics — 4 space-separated values in cell [11]
    risk_parts = cells[11].split() if cells[11] else []
    while len(risk_parts) < 4:
        risk_parts.append("")

    # Valuation — 3 space-separated values in cell [12]
    val_parts = cells[12].split() if cells[12] else []
    while len(val_parts) < 3:
        val_parts.append("")

    return {
        "index_name":      cells[0],
        "return_1m":       return_vals[0],
        "return_3m":       return_vals[1],
        "return_1yr":      return_vals[2],
        "return_3yr":      return_vals[3],
        "return_5yr":      return_vals[4],
        "volatility_1yr":  risk_parts[0],
        "beta_1yr":        risk_parts[1],
        "correlation_1yr": risk_parts[2],
        "r2_1yr":          risk_parts[3],
        "pe":              val_parts[0],
        "pb":              val_parts[1],
        "dividend_yield":  val_parts[2],
    }


def parse_pdf_to_df(pdf_path: str, source_filename: str) -> pd.DataFrame:
    """Extract all index rows from a PDF into a DataFrame."""
    records = []
    current_section = "Unknown"

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    if not row:
                        continue
                    sec = _detect_section(row)
                    if sec:
                        current_section = sec
                        continue
                    if _is_data_row(row):
                        parsed = _parse_row(row)
                        if parsed:
                            parsed["section"]     = current_section
                            parsed["source_file"] = source_filename
                            records.append(parsed)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col].replace({"": None, "-": None}), errors="coerce")

    year, month = infer_date(source_filename)
    df["year"]  = year
    df["month"] = month
    return df


def pdf_to_parquet(pdf_path: Path) -> tuple[Path, int]:
    """Parse PDF → save parquet. Returns (parquet_path, row_count)."""
    out_path = PARQUET_DIR / (pdf_path.stem + ".parquet")
    df = parse_pdf_to_df(str(pdf_path), pdf_path.name)
    if not df.empty:
        df.to_parquet(out_path, index=False, engine="pyarrow")
    return out_path, len(df)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

RETURN_COLS  = ["return_1m","return_3m","return_1yr","return_3yr","return_5yr"]
RISK_COLS    = ["volatility_1yr","beta_1yr","correlation_1yr","r2_1yr"]
VAL_COLS     = ["pe","pb","dividend_yield"]
ALL_NUM_COLS = RETURN_COLS + RISK_COLS + VAL_COLS
RETURN_LABELS = {
    "return_1m":"1M","return_3m":"3M","return_1yr":"1 Yr",
    "return_3yr":"3 Yr","return_5yr":"5 Yr",
}
SECTION_ICONS = {
    "Broad Market Indices": "🏛️", "Sectoral Indices": "🏭",
    "Strategy Indices": "♟️",    "Thematic Indices": "🎯",
    "Unknown": "❓",
}


@st.cache_data(show_spinner=False)
def load_parquet(parquet_path: str) -> pd.DataFrame:
    """Load a single parquet file by path."""
    try:
        df = pd.read_parquet(parquet_path)
        if "year" not in df.columns or df["year"].isna().all():
            y, m = infer_date(parquet_path)
            df["year"], df["month"] = y, m
        for col in ALL_NUM_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def color_return(val):
    try:
        v = float(val)
        return f"color:{'#4ade80' if v >= 0 else '#f87171'};font-weight:500"
    except (TypeError, ValueError):
        return ""


def styled_df(view, color_cols=None):
    s = view.style
    if color_cols:
        s = s.applymap(color_return, subset=color_cols)
    skip = {"Index","Section","index_name","section","source_file","year","month"}
    num_cols = [c for c in view.columns if c not in skip]
    return (s
            .format({c: "{:.2f}" for c in num_cols}, na_rep="—")
            .set_properties(**{
                "background-color": "#0d1a2e",
                "color": "#e2e8f0",
                "font-family": "DM Mono, monospace",
                "font-size": "0.78rem",
            }))


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:

    # ── PDF Selector ──────────────────────────────────────────────────────────
    st.markdown("### 📂 Index Dashboard PDFs")

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))

    if not pdf_files:
        st.info(f"No PDFs found in `{PDF_DIR}`.\nPlace NSE Index Dashboard PDFs there and refresh.")
        st.stop()

    # Build display labels:  "January 2026  (JAN2026.pdf)"
    def _pdf_label(p: Path) -> str:
        y, m = infer_date(p.name)
        date_str = f"{MONTH_MAP.get(m,'?')} {y}" if y else p.stem
        return f"{date_str}  ·  {p.name}"

    pdf_labels   = [_pdf_label(p) for p in pdf_files]
    sel_label    = st.selectbox("Select PDF", pdf_labels, label_visibility="collapsed")
    sel_pdf_path = pdf_files[pdf_labels.index(sel_label)]
    sel_parquet  = PARQUET_DIR / (sel_pdf_path.stem + ".parquet")

    # Parquet status indicator
    if sel_parquet.exists():
        st.markdown(
            f'<div class="file-item">'
            f'<span><span class="dot"></span>Parquet ready</span>'
            f'<span style="color:#38bdf8">{sel_pdf_path.stem}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="file-item" style="border-color:#f59e0b">'
            f'<span><span style="width:8px;height:8px;border-radius:50%;'
            f'background:#f59e0b;margin-right:8px;display:inline-block"></span>'
            f'Not yet parsed</span>'
            f'<span style="color:#f59e0b">{sel_pdf_path.stem}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Refresh / Parse button
    btn_label = "🔄  Refresh Parquet" if sel_parquet.exists() else "⚡  Parse PDF → Parquet"
    if st.button(btn_label, use_container_width=True):
        with st.spinner(f"Parsing {sel_pdf_path.name}…"):
            try:
                _, n_rows = pdf_to_parquet(sel_pdf_path)
                st.cache_data.clear()
                st.success(f"✅ {n_rows} rows saved to parquet")
                st.rerun()
            except Exception as e:
                st.error(f"❌ {e}")

    # Auto-parse on first selection if parquet missing
    if not sel_parquet.exists():
        with st.spinner(f"Auto-parsing {sel_pdf_path.name}…"):
            try:
                _, n_rows = pdf_to_parquet(sel_pdf_path)
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"❌ Could not parse PDF: {e}")
                st.stop()

    st.markdown("---")

    # ── Load selected parquet ─────────────────────────────────────────────────
    df_raw = load_parquet(str(sel_parquet))
    if df_raw.empty:
        st.warning("No data loaded. Try refreshing the parquet.")
        st.stop()

    # ── Period — inferred from filename, no picker needed ────────────────────
    years        = sorted(df_raw["year"].dropna().unique().astype(int), reverse=True)
    sel_year     = int(years[0]) if years else 0
    months_avail = sorted(df_raw[df_raw["year"]==sel_year]["month"].dropna().unique().astype(int))
    sel_month    = int(months_avail[-1]) if months_avail else 0
    sel_month_lbl = MONTH_MAP.get(sel_month, str(sel_month))

    st.markdown(
        f'<div style="background:#0f1e3d;border:1px solid #1e3454;border-radius:8px;'
        f'padding:0.6rem 1rem;font-size:0.75rem;color:#38bdf8;letter-spacing:0.06em;">'
        f'📅  {sel_month_lbl} {sel_year}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── Filters ───────────────────────────────────────────────────────────────
    st.markdown("### 🔍 Filter")
    all_secs  = sorted(df_raw["section"].dropna().unique())
    sel_secs  = st.multiselect("Sections", all_secs, default=all_secs)

    st.markdown("---")
    st.markdown("### ⚙️ Display")
    top_n = st.slider("Top N per chart", 5, 50, 15)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CONTENT
# ══════════════════════════════════════════════════════════════════════════════

df = df_raw[
    (df_raw["year"]  == sel_year) &
    (df_raw["month"] == sel_month) &
    (df_raw["section"].isin(sel_secs))
].copy()

# ── Hero ──────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="hero">
    <div class="hero-sub">NSE Indices Limited · Index Dashboard</div>
    <div class="hero-title">{sel_month_lbl} {sel_year}</div>
    <div style="margin-top:0.8rem">
        {''.join(f'<span class="badge">{SECTION_ICONS.get(s,"")} {s}</span>' for s in sel_secs)}
    </div>
</div>
""", unsafe_allow_html=True)

if df.empty:
    st.warning("No data for the selected period and filters.")
    st.stop()

# ── KPI strip ─────────────────────────────────────────────────────────────────
total   = len(df)
pos_1m  = int((df["return_1m"] > 0).sum()) if "return_1m" in df else 0
avg_1yr = df["return_1yr"].mean()           if "return_1yr" in df else float("nan")
avg_pe  = df["pe"].mean()                   if "pe"         in df else float("nan")

pos_class = "pos" if pos_1m > total / 2 else "neg"
ret_class = "pos" if avg_1yr >= 0 else "neg"

st.markdown(f"""
<div class="kpi-grid">
  <div class="kpi-card">
    <div class="kpi-label">Total Indices</div>
    <div class="kpi-value">{total}</div>
    <div class="kpi-delta">{len(sel_secs)} sections</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Positive 1M Returns</div>
    <div class="kpi-value {pos_class}">{pos_1m}</div>
    <div class="kpi-delta">{pos_1m/total*100:.0f}% of indices</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Avg 1-Year Return</div>
    <div class="kpi-value {ret_class}">{avg_1yr:.1f}%</div>
    <div class="kpi-delta">across all selected</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Avg P/E Ratio</div>
    <div class="kpi-value">{avg_pe:.1f}x</div>
    <div class="kpi-delta">price / earnings</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

tab_ov, tab_ret, tab_risk, tab_val, tab_raw = st.tabs([
    "📊 Overview", "📈 Returns", "⚡ Risk", "💰 Valuation", "🗃️ Raw Data"
])

# ── Overview ──────────────────────────────────────────────────────────────────
with tab_ov:
    for sec in sel_secs:
        sec_df = df[df["section"] == sec]
        icon   = SECTION_ICONS.get(sec, "")
        with st.expander(f"{icon}  {sec}  ({len(sec_df)} indices)", expanded=True):
            cols = ["index_name"] + RETURN_COLS + ["volatility_1yr","pe","pb","dividend_yield"]
            cols = [c for c in cols if c in sec_df.columns]
            view = sec_df[cols].rename(columns={
                "index_name":"Index","return_1m":"1M %","return_3m":"3M %",
                "return_1yr":"1 Yr %","return_3yr":"3 Yr %","return_5yr":"5 Yr %",
                "volatility_1yr":"Volatility","pe":"P/E","pb":"P/B","dividend_yield":"Div Yield",
            })
            ret_cols = [c for c in ["1M %","3M %","1 Yr %","3 Yr %","5 Yr %"] if c in view]
            st.dataframe(styled_df(view, ret_cols), use_container_width=True, hide_index=True)

# ── Returns ───────────────────────────────────────────────────────────────────
with tab_ret:
    c1, c2 = st.columns([1, 2])
    with c1:
        r_sec    = st.selectbox("Section", sel_secs, key="rs",
                                format_func=lambda s: f"{SECTION_ICONS.get(s,'')} {s}")
        r_period = st.radio("Period", list(RETURN_LABELS.values()), horizontal=True, key="rp")

    p_col    = {v: k for k, v in RETURN_LABELS.items()}[r_period]
    chart_df = (df[df["section"]==r_sec][["index_name",p_col]]
                .dropna().sort_values(p_col, ascending=False).head(top_n))
    with c2:
        st.markdown(f"**Top {top_n} · {r_sec} · {r_period}**")
        if not chart_df.empty:
            st.bar_chart(chart_df.set_index("index_name")[p_col], color="#38bdf8")

    st.markdown("---")
    all_ret = (df[["index_name","section"]+RETURN_COLS]
               .sort_values(p_col, ascending=False)
               .rename(columns={"index_name":"Index","section":"Section",
                                **{c: RETURN_LABELS[c]+" %" for c in RETURN_COLS}}))
    rlbls = [RETURN_LABELS[c]+" %" for c in RETURN_COLS]
    st.dataframe(styled_df(all_ret, rlbls), use_container_width=True, hide_index=True)

# ── Risk ──────────────────────────────────────────────────────────────────────
with tab_risk:
    risk_avail = [c for c in RISK_COLS if c in df.columns]
    if not risk_avail:
        st.info("No risk columns found.")
    else:
        c1, c2 = st.columns(2)
        fmt    = lambda c: c.replace("_"," ").title()
        with c1:
            x_ax = st.selectbox("X axis", risk_avail, key="rx", format_func=fmt)
        with c2:
            y_ax = st.selectbox("Y axis", risk_avail, index=min(1,len(risk_avail)-1),
                                key="ry", format_func=fmt)

        sdf = df[["index_name","section",x_ax,y_ax,"return_1yr"]].dropna()
        if not sdf.empty:
            st.scatter_chart(
                sdf.rename(columns={x_ax: fmt(x_ax), y_ax: fmt(y_ax)}),
                x=fmt(x_ax), y=fmt(y_ax),
                color="section", size="return_1yr",
                use_container_width=True,
            )
        st.markdown("---")
        rtbl = (df[["index_name","section"]+risk_avail]
                .rename(columns={"index_name":"Index","section":"Section",
                                 "volatility_1yr":"Volatility 1Y","beta_1yr":"Beta 1Y",
                                 "correlation_1yr":"Correlation 1Y","r2_1yr":"R² 1Y"}))
        st.dataframe(
            rtbl.style
            .format({c: "{:.3f}" for c in rtbl.columns if c not in ("Index","Section")}, na_rep="—")
            .set_properties(**{"background-color":"#0d1a2e","color":"#e2e8f0",
                               "font-family":"DM Mono, monospace","font-size":"0.78rem"}),
            use_container_width=True, hide_index=True,
        )

# ── Valuation ─────────────────────────────────────────────────────────────────
with tab_val:
    val_avail = [c for c in VAL_COLS if c in df.columns]
    if val_avail:
        col_rename = {"pe":"Avg P/E","pb":"Avg P/B","dividend_yield":"Avg Div Yield %"}
        agg = (df.groupby("section")[val_avail].mean().reset_index()
               .rename(columns={"section":"Section",**col_rename}))
        disp_cols = [c for c in agg.columns if c != "Section"]
        st.markdown("**Average valuation by section**")
        st.dataframe(
            agg.style
            .format({c: "{:.2f}" for c in disp_cols}, na_rep="—")
            .background_gradient(cmap="YlOrRd", subset=disp_cols)
            .set_properties(**{"font-family":"DM Mono, monospace","font-size":"0.8rem"}),
            use_container_width=True, hide_index=True,
        )
        st.markdown("---")
        col_lookup = {"Avg P/E":"pe","Avg P/B":"pb","Avg Div Yield %":"dividend_yield"}
        c1, c2 = st.columns(2)
        with c1:
            val_met = st.selectbox("Metric", disp_cols, key="vm")
        with c2:
            val_sec = st.selectbox("Section", sel_secs, key="vs",
                                   format_func=lambda s: f"{SECTION_ICONS.get(s,'')} {s}")
        raw_col = col_lookup.get(val_met, "pe")
        vdf = (df[df["section"]==val_sec][["index_name",raw_col]]
               .dropna().sort_values(raw_col, ascending=False).head(top_n)
               .set_index("index_name"))
        if not vdf.empty:
            st.bar_chart(vdf, color="#818cf8")

# ── Raw Data ──────────────────────────────────────────────────────────────────
with tab_raw:
    st.markdown(f"**{len(df)} rows** · {sel_month_lbl} {sel_year}")
    search = st.text_input("🔎 Search", placeholder="e.g. Nifty Bank")
    out    = df if not search else df[df["index_name"].str.contains(search, case=False, na=False)]
    rcp    = [c for c in RETURN_COLS if c in out.columns]
    st.dataframe(
        out.reset_index(drop=True).style
        .applymap(color_return, subset=rcp)
        .format({c: "{:.2f}" for c in ALL_NUM_COLS if c in out.columns}, na_rep="—")
        .set_properties(**{"background-color":"#0d1a2e","color":"#e2e8f0",
                           "font-family":"DM Mono, monospace","font-size":"0.75rem"}),
        use_container_width=True, hide_index=True,
    )
    st.download_button(
        "⬇️  Download CSV", out.to_csv(index=False).encode(),
        file_name=f"nse_{sel_year}_{sel_month:02d}.csv", mime="text/csv",
    )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<hr>
<div style="text-align:center;color:#334155;font-family:'DM Mono',monospace;
     font-size:0.68rem;letter-spacing:0.08em;">
    NSE INDICES LIMITED · DATA FOR REFERENCE PURPOSES ONLY · NOT INVESTMENT ADVICE
</div>
""", unsafe_allow_html=True)