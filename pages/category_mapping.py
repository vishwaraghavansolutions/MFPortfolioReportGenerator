"""
Category → Index Mapping
-------------------------
Reads SchemeData CSV (Scheme Type + Scheme Category) and NSE index parquet files,
lets users map each fund category to benchmark indices, and exports the result.

Run:
    streamlit run category_mapping.py
"""

import glob
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
PARQUET_DIR  = Path("data")
MAPPING_FILE = Path("data/category_index_mapping.json")
SCHEME_FILE  = Path("data/SchemeData2301262313SS.csv")   # default location
PARQUET_DIR.mkdir(parents=True, exist_ok=True)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Category → Index Mapping",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
.stApp { background: #f7f6f2; color: #1a1a1a; }

[data-testid="stSidebar"] { background: #1a1a1a !important; }
[data-testid="stSidebar"] * { color: #e8e4dc !important; }
[data-testid="stSidebar"] label {
    font-size: 0.72rem !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    color: #777 !important;
}

.page-header {
    padding: 2rem 0 1.5rem 0;
    border-bottom: 2px solid #1a1a1a;
    margin-bottom: 2rem;
}
.page-title {
    font-size: 2.6rem; font-weight: 700;
    letter-spacing: -0.03em; color: #1a1a1a; line-height: 1;
}
.page-subtitle {
    font-size: 0.78rem; font-family: 'JetBrains Mono', monospace;
    color: #888; letter-spacing: 0.08em; text-transform: uppercase;
    margin-bottom: 0.4rem;
}

.stats-strip {
    display: flex; gap: 1px;
    background: #1a1a1a; border-radius: 10px;
    overflow: hidden; margin-bottom: 2rem;
}
.stat-cell { flex: 1; background: #f7f6f2; padding: 1rem 1.4rem; text-align: center; }
.stat-cell.dark { background: #1a1a1a; }
.stat-num { font-size: 2rem; font-weight: 700; line-height: 1; color: #1a1a1a; }
.stat-cell.dark .stat-num { color: #f0e040; }
.stat-lbl {
    font-size: 0.66rem; font-family: 'JetBrains Mono', monospace;
    color: #999; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 0.25rem;
}
.stat-cell.dark .stat-lbl { color: #666; }

.section-head {
    font-size: 0.7rem; font-family: 'JetBrains Mono', monospace;
    color: #999; letter-spacing: 0.1em; text-transform: uppercase;
    border-bottom: 1px solid #e5e3de;
    padding-bottom: 0.5rem; margin: 1.5rem 0 1rem 0;
}

.cat-label { font-weight: 600; font-size: 0.9rem; color: #1a1a1a; }
.cat-type  {
    font-size: 0.68rem; font-family: 'JetBrains Mono', monospace;
    color: #aaa; text-transform: uppercase; letter-spacing: 0.05em;
}
.cat-count {
    font-size: 0.7rem; font-family: 'JetBrains Mono', monospace; color: #888;
}

.badge-mapped {
    display: inline-block; background: #e8f5e9; color: #2e7d32;
    border: 1px solid #a5d6a7; font-size: 0.64rem;
    font-family: 'JetBrains Mono', monospace;
    padding: 2px 8px; border-radius: 4px;
}
.badge-auto {
    display: inline-block; background: #e3f2fd; color: #1565c0;
    border: 1px solid #90caf9; font-size: 0.64rem;
    font-family: 'JetBrains Mono', monospace;
    padding: 2px 8px; border-radius: 4px;
}
.badge-unmapped {
    display: inline-block; background: #fff3e0; color: #e65100;
    border: 1px solid #ffcc80; font-size: 0.64rem;
    font-family: 'JetBrains Mono', monospace;
    padding: 2px 8px; border-radius: 4px;
}

.idx-tag {
    display: inline-block; background: #ede9fe; color: #5b21b6;
    border: 1px solid #c4b5fd; font-size: 0.66rem;
    font-family: 'JetBrains Mono', monospace;
    padding: 2px 8px; border-radius: 4px; margin: 2px;
}
.idx-tag-sec {
    display: inline-block; background: #fef9c3; color: #713f12;
    border: 1px solid #fde68a; font-size: 0.66rem;
    font-family: 'JetBrains Mono', monospace;
    padding: 2px 8px; border-radius: 4px; margin: 2px;
}
.suggestion-hint {
    font-size: 0.7rem; font-family: 'JetBrains Mono', monospace;
    color: #1565c0; margin-top: 4px;
}

.stTabs [data-baseweb="tab-list"] {
    background: white; border-radius: 8px; padding: 4px;
    border: 1px solid #e5e3de; gap: 2px;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.82rem;
    font-weight: 500; color: #888 !important;
    background: transparent; border-radius: 6px; padding: 7px 16px;
}
.stTabs [aria-selected="true"] {
    background: #1a1a1a !important; color: #f7f6f2 !important;
}

[data-baseweb="tag"] { background: #1a1a1a !important; border-radius: 4px !important; }
[data-baseweb="tag"] span { color: white !important; font-size: 0.7rem !important; }

.stButton > button {
    font-family: 'Space Grotesk', sans-serif; font-weight: 600;
    font-size: 0.82rem; background: #1a1a1a; color: white;
    border: none; border-radius: 7px; padding: 0.5rem 1.2rem;
}
.stButton > button:hover { background: #333; }

[data-testid="stDataFrame"] { border: 1px solid #e5e3de; border-radius: 8px; overflow: hidden; }
hr { border-color: #e5e3de; margin: 1rem 0; }
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #f7f6f2; }
::-webkit-scrollbar-thumb { background: #ccc; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-SUGGESTION RULES
# Maps each exact Scheme Category → list of suggested index names
# ══════════════════════════════════════════════════════════════════════════════

AUTO_SUGGESTIONS: dict[str, list[str]] = {
    # ── Equity ────────────────────────────────────────────────────────────────
    "Equity Scheme - Large Cap Fund": [
        "Nifty 50", "Nifty 100", "Nifty100 Equal Weight",
    ],
    "Equity Scheme - Mid Cap Fund": [
        "Nifty Midcap 50", "Nifty Midcap 100", "Nifty Midcap 150",
        "Nifty Midcap Select",
    ],
    "Equity Scheme - Small Cap Fund": [
        "Nifty Smallcap 50", "Nifty Smallcap 100", "Nifty Smallcap 250",
    ],
    "Equity Scheme - Large & Mid Cap Fund": [
        "Nifty LargeMidcap 250", "Nifty 100",
    ],
    "Equity Scheme - Multi Cap Fund": [
        "Nifty 500", "Nifty500 Multicap 50:25:25",
        "Nifty500 LargeMidSmall Equal-Cap Weighted",
    ],
    "Equity Scheme - Flexi Cap Fund": [
        "Nifty 500", "Nifty Total Market",
        "Nifty500 Flexicap Quality 30",
    ],
    "Equity Scheme - Focused Fund": [
        "Nifty 50", "Nifty 100", "Nifty500 Quality 50",
    ],
    "Equity Scheme - ELSS": [
        "Nifty 500", "Nifty LargeMidcap 250",
    ],
    "ELSS": [
        "Nifty 500", "Nifty LargeMidcap 250",
    ],
    "Equity Scheme - Contra Fund": [
        "Nifty200 Value 30", "Nifty500 Value 50",
    ],
    "Equity Scheme - Value Fund": [
        "Nifty200 Value 30", "Nifty500 Value 50", "Nifty50 Value 20",
    ],
    "Equity Scheme - Dividend Yield Fund": [
        "Nifty Dividend Opportunities 50",
    ],
    "Equity Scheme - Sectoral/ Thematic": [
        "Nifty Auto", "Nifty Bank", "Nifty IT", "Nifty Pharma",
        "Nifty FMCG", "Nifty Financial Services", "Nifty Realty",
        "Nifty Infrastructure", "Nifty India Defence", "Nifty Metal",
        "Nifty PSU Bank", "Nifty India Manufacturing",
    ],

    # ── Hybrid ────────────────────────────────────────────────────────────────
    "Hybrid Scheme - Aggressive Hybrid Fund": [
        "Nifty 50", "Nifty 500",
    ],
    "Hybrid Scheme - Balanced Hybrid Fund": [
        "Nifty 50", "Nifty 100",
    ],
    "Hybrid Scheme - Conservative Hybrid Fund": [
        "Nifty 50",
    ],
    "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage": [
        "Nifty 50", "Nifty 500",
    ],
    "Hybrid Scheme - Equity Savings": [
        "Nifty 50", "Nifty 100",
    ],
    "Hybrid Scheme - Arbitrage Fund": [
        "Nifty 50",
    ],
    "Hybrid Scheme - Multi Asset Allocation": [
        "Nifty 50", "Nifty Commodities", "Nifty REITs & InvITs",
    ],

    # ── Debt ──────────────────────────────────────────────────────────────────
    "Debt Scheme - Liquid Fund":              [],
    "Debt Scheme - Overnight Fund":           [],
    "Debt Scheme - Ultra Short Duration Fund":[],
    "Debt Scheme - Low Duration Fund":        [],
    "Debt Scheme - Short Duration Fund":      [],
    "Debt Scheme - Medium Duration Fund":     [],
    "Debt Scheme - Medium to Long Duration Fund": [],
    "Debt Scheme - Long Duration Fund":       [],
    "Debt Scheme - Dynamic Bond":             [],
    "Debt Scheme - Corporate Bond Fund":      [],
    "Debt Scheme - Banking and PSU Fund": [
        "Nifty Bank", "Nifty PSU Bank",
    ],
    "Debt Scheme - Credit Risk Fund":         [],
    "Debt Scheme - Gilt Fund":                [],
    "Debt Scheme - Gilt Fund with 10 year constant duration": [],
    "Debt Scheme - Floater Fund":             [],
    "Debt Scheme - Money Market Fund":        [],

    # ── Other ─────────────────────────────────────────────────────────────────
    "Other Scheme - Index Funds": [
        "Nifty 50", "Nifty Next 50", "Nifty 100", "Nifty Midcap 150",
        "Nifty Smallcap 250", "Nifty 500",
    ],
    "Other Scheme - Other  ETFs": [
        "Nifty 50", "Nifty Bank", "Nifty IT", "Nifty Pharma",
        "Nifty Auto", "Nifty Metal",
    ],
    "Other Scheme - Gold ETF":     [],
    "Other Scheme - FoF Domestic": [
        "Nifty 50", "Nifty 500",
    ],
    "Other Scheme - FoF Overseas": [],

    # ── Solution oriented ─────────────────────────────────────────────────────
    "Solution Oriented Scheme - Children s Fund": [
        "Nifty 50", "Nifty 500",
    ],
    "Solution Oriented Scheme - Retirement Fund": [
        "Nifty 50", "Nifty 500",
    ],

    # ── Legacy / Close ended ──────────────────────────────────────────────────
    "Growth": ["Nifty 50", "Nifty 500"],
    "Balanced": ["Nifty 50"],
    "Income": [],
    "Liquid": [],
    "Gilt": [],
    "Money Market": [],
    "Assured Return": [],
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

MONTH_ABBR = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
              "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
MONTH_MAP  = {1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",
              7:"July",8:"August",9:"September",10:"October",11:"November",12:"December"}


def infer_date(fname):
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


def make_key(scheme_type: str, category: str) -> str:
    return f"{scheme_type}::{category}"


def parse_key(key: str):
    parts = key.split("::", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (key, "")


@st.cache_data(show_spinner=False)
def load_indices() -> pd.DataFrame:
    files = glob.glob(str(PARQUET_DIR / "*.parquet"))
    if not files:
        return pd.DataFrame(columns=["index_name","section"])
    frames = [pd.read_parquet(f)[["index_name","section"]] for f in files
              if "mapping" not in f]
    if not frames:
        return pd.DataFrame(columns=["index_name","section"])
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates("index_name")
            .sort_values(["section","index_name"]))


@st.cache_data(show_spinner=False)
def load_scheme_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    counts = (df.groupby(["Scheme Type","Scheme Category"])
              .size().reset_index(name="scheme_count"))
    return counts


def load_mapping() -> dict:
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE) as f:
            return json.load(f)
    return {}


def save_mapping(mapping: dict):
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2)


def get_suggestion_status(key, mapping):
    """Return: 'mapped' | 'auto' | 'unmapped'"""
    val = mapping.get(key)
    if val:
        return "mapped"
    cat = parse_key(key)[1]
    if AUTO_SUGGESTIONS.get(cat):
        return "auto"
    return "unmapped"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### 📂 Scheme Data")
    scheme_upload = st.file_uploader(
        "Upload SchemeData CSV", type=["csv","xlsx","xls"],
        label_visibility="collapsed",
    )

    # Determine scheme file source
    scheme_path = None
    if scheme_upload:
        tmp = Path("data") / scheme_upload.name
        with open(tmp, "wb") as f:
            f.write(scheme_upload.read())
        scheme_path = str(tmp)
    elif SCHEME_FILE.exists():
        scheme_path = str(SCHEME_FILE)

    st.markdown("---")
    st.markdown("### 🗂️ Index Parquet Files")
    parquet_files = [p for p in sorted(PARQUET_DIR.glob("*.parquet"))
                     if "mapping" not in p.name]
    if parquet_files:
        for p in parquet_files:
            y, m = infer_date(p.name)
            label = f"{MONTH_MAP.get(m,'?')} {y}" if y else p.stem
            st.markdown(
                f'<div style="background:#252525;border-radius:6px;padding:6px 10px;'
                f'margin-bottom:4px;font-size:0.7rem;font-family:JetBrains Mono,monospace;'
                f'color:#aaa;display:flex;justify-content:space-between">'
                f'<span>{p.stem[:24]}</span>'
                f'<span style="color:#f0e040">{label}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        st.warning("No parquet files in data/. Run app.py first.")

    st.markdown("---")
    mapping = load_mapping()
    mapped_count = sum(1 for v in mapping.values() if v)
    st.markdown(
        f'<div style="background:#252525;border-radius:8px;padding:10px 14px">'
        f'<div style="font-size:0.65rem;color:#666;font-family:JetBrains Mono,monospace;'
        f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px">Saved Mappings</div>'
        f'<div style="font-size:1.8rem;font-weight:700;color:#f0e040">{mapped_count}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

idx_df  = load_indices()
mapping = load_mapping()

if not scheme_path:
    st.markdown("""
    <div class="page-header">
        <div class="page-subtitle">NSE · Fund Research Tool</div>
        <div class="page-title">Category → Index Mapping</div>
    </div>
    """, unsafe_allow_html=True)
    st.info("👈 Upload the **SchemeData CSV** in the sidebar to begin.")
    st.stop()

categories_df = load_scheme_data(scheme_path)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE HEADER + STATS
# ══════════════════════════════════════════════════════════════════════════════

n_types    = categories_df["Scheme Type"].nunique()
n_cats     = len(categories_df)
n_mapped   = sum(1 for _, r in categories_df.iterrows()
                 if mapping.get(make_key(r["Scheme Type"], r["Scheme Category"])))
n_auto     = sum(1 for _, r in categories_df.iterrows()
                 if not mapping.get(make_key(r["Scheme Type"], r["Scheme Category"]))
                 and AUTO_SUGGESTIONS.get(r["Scheme Category"]))
n_unmapped = n_cats - n_mapped - n_auto
n_indices  = len(idx_df)

st.markdown("""
<div class="page-header">
    <div class="page-subtitle">NSE · Fund Research Tool</div>
    <div class="page-title">Category → Index Mapping</div>
</div>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="stats-strip">
  <div class="stat-cell">
    <div class="stat-num">{n_types}</div>
    <div class="stat-lbl">Scheme Types</div>
  </div>
  <div class="stat-cell">
    <div class="stat-num">{n_cats}</div>
    <div class="stat-lbl">Categories</div>
  </div>
  <div class="stat-cell dark">
    <div class="stat-num">{n_mapped}</div>
    <div class="stat-lbl">Manually Mapped</div>
  </div>
  <div class="stat-cell">
    <div class="stat-num" style="color:#1565c0">{n_auto}</div>
    <div class="stat-lbl">Auto-suggested</div>
  </div>
  <div class="stat-cell">
    <div class="stat-num" style="color:#e65100">{n_unmapped}</div>
    <div class="stat-lbl">Unmapped</div>
  </div>
  <div class="stat-cell">
    <div class="stat-num">{n_indices}</div>
    <div class="stat-lbl">Indices Available</div>
  </div>
</div>
""", unsafe_allow_html=True)

if idx_df.empty:
    st.error("No index data found. Run **app.py** first to generate parquet files.")
    st.stop()

idx_options     = idx_df["index_name"].tolist()
idx_section_map = dict(zip(idx_df["index_name"], idx_df["section"]))


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

tab_map, tab_view, tab_export = st.tabs([
    "🗂️ Map Categories", "📋 View Mapping", "⬇️ Export"
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — MAP CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────
with tab_map:

    # ── Derive category group from the "Scheme Category" prefix ──────────────
    # e.g. "Equity Scheme - Large Cap Fund"  → "Equity Scheme"
    #      "Debt Scheme - Liquid Fund"        → "Debt Scheme"
    #      "Hybrid Scheme - ..."              → "Hybrid Scheme"
    #      "Other Scheme - ..."               → "Other Scheme"
    #      "Solution Oriented Scheme - ..."   → "Solution Oriented Scheme"
    #      "ELSS" / "Growth" / "Income" etc.  → "Other / Legacy"
    def cat_group(cat: str) -> str:
        return cat.split(" - ")[0].strip() if " - " in cat else "Other / Legacy"

    categories_df["cat_group"] = categories_df["Scheme Category"].apply(cat_group)

    # ── Filter bar ────────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 1])
    with fc1:
        cat_group_filter = st.selectbox(
            "Scheme Category",
            ["All"] + sorted(categories_df["cat_group"].unique()),
            key="cat_group_f",
        )
    with fc2:
        status_filter = st.selectbox(
            "Status",
            ["All", "Manually Mapped", "Auto-suggested", "Unmapped"],
            key="status_f",
        )
    with fc3:
        search_q = st.text_input("Search", placeholder="e.g. Large Cap, Banking…")
    with fc4:
        if st.button("⚡ Apply All Auto-suggestions", use_container_width=True):
            for _, row in categories_df.iterrows():
                key  = make_key(row["Scheme Type"], row["Scheme Category"])
                sugg = AUTO_SUGGESTIONS.get(row["Scheme Category"], [])
                if sugg and not mapping.get(key):
                    valid = [s for s in sugg if s in idx_options]
                    if valid:
                        mapping[key] = valid
            save_mapping(mapping)
            st.cache_data.clear()
            st.rerun()

    # Apply filters
    filtered = categories_df.copy()
    if cat_group_filter != "All":
        filtered = filtered[filtered["cat_group"] == cat_group_filter]
    if search_q:
        filtered = filtered[
            filtered["Scheme Category"].str.contains(search_q, case=False, na=False)
        ]
    if status_filter == "Manually Mapped":
        filtered = filtered[filtered.apply(
            lambda r: bool(mapping.get(make_key(r["Scheme Type"], r["Scheme Category"]))), axis=1)]
    elif status_filter == "Auto-suggested":
        filtered = filtered[filtered.apply(
            lambda r: (not mapping.get(make_key(r["Scheme Type"], r["Scheme Category"])))
                      and bool(AUTO_SUGGESTIONS.get(r["Scheme Category"])), axis=1)]
    elif status_filter == "Unmapped":
        filtered = filtered[filtered.apply(
            lambda r: not mapping.get(make_key(r["Scheme Type"], r["Scheme Category"]))
                      and not AUTO_SUGGESTIONS.get(r["Scheme Category"]), axis=1)]

    st.markdown(
        f'<div style="font-size:0.72rem;color:#999;margin-bottom:1rem;'
        f'font-family:JetBrains Mono,monospace">'
        f'Showing {len(filtered)} of {len(categories_df)} categories</div>',
        unsafe_allow_html=True,
    )

    # ── Grouped by Scheme Category group ─────────────────────────────────────
    for cat_grp, grp in filtered.groupby("cat_group"):
        n_grp_mapped = sum(
            1 for _, r in grp.iterrows()
            if mapping.get(make_key(r["Scheme Type"], r["Scheme Category"]))
        )
        n_grp_auto = sum(
            1 for _, r in grp.iterrows()
            if not mapping.get(make_key(r["Scheme Type"], r["Scheme Category"]))
            and AUTO_SUGGESTIONS.get(r["Scheme Category"])
        )

        with st.expander(
            f"**{cat_grp}**  ·  {len(grp)} categories  "
            f"·  ✅ {n_grp_mapped} mapped  "
            f"·  🔵 {n_grp_auto} suggested  "
            f"·  ⚠️ {len(grp)-n_grp_mapped-n_grp_auto} unmapped",
            expanded=(cat_group_filter != "All"),
        ):
            for _, row in grp.iterrows():
                key      = make_key(row["Scheme Type"], row["Scheme Category"])
                cur_val  = mapping.get(key, [])
                sugg     = [s for s in AUTO_SUGGESTIONS.get(row["Scheme Category"], [])
                            if s in idx_options]
                status   = get_suggestion_status(key, mapping)

                # Status badge
                if status == "mapped":
                    badge = f'<span class="badge-mapped">✓ {len(cur_val)} {"indices" if len(cur_val)>1 else "index"} mapped</span>'
                elif status == "auto":
                    badge = f'<span class="badge-auto">🔵 {len(sugg)} auto-suggested</span>'
                else:
                    badge = '<span class="badge-unmapped">⚠️ unmapped</span>'

                c1, c2 = st.columns([5, 7])
                with c1:
                    st.markdown(
                        f'<div style="padding:0.6rem 0">'
                        f'<div class="cat-label">{row["Scheme Category"]}</div>'
                        f'<div class="cat-type">{row["Scheme Type"]}</div>'
                        f'<div class="cat-count">{row["scheme_count"]:,} schemes</div>'
                        f'<div style="margin-top:6px">{badge}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                with c2:
                    # Default value: existing mapping, or auto-suggestions if none
                    default_val = cur_val if cur_val else sugg

                    selected = st.multiselect(
                        f"_{key}_",
                        options=idx_options,
                        default=default_val,
                        key=f"ms_{key}",
                        label_visibility="collapsed",
                        placeholder="Select indices…",
                    )

                    # Show section tags for selected indices
                    if selected:
                        SECTION_SHORT = {
                            "Broad Market Indices": "BROAD",
                            "Sectoral Indices":     "SECTOR",
                            "Strategy Indices":     "STRATEGY",
                            "Thematic Indices":     "THEMATIC",
                        }
                        tags = ""
                        for s in selected:
                            sec   = idx_section_map.get(s, "")
                            short = SECTION_SHORT.get(sec, sec[:3].upper())
                            cls   = "idx-tag-sec" if "Sectoral" in sec else "idx-tag"
                            tags += f'<span class="{cls}">{short} · {s}</span>'
                        st.markdown(tags, unsafe_allow_html=True)

                    # Auto-save on change
                    if selected != cur_val:
                        mapping[key] = selected
                        save_mapping(mapping)

                st.markdown("<hr style='margin:0.3rem 0;border-color:#ede8df'>",
                            unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("💾  Save All Mappings", use_container_width=True):
        save_mapping(mapping)
        st.success("✅ Mapping saved to data/category_index_mapping.json")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — VIEW MAPPING
# ─────────────────────────────────────────────────────────────────────────────
with tab_view:
    if not mapping:
        st.info("No mappings saved yet. Go to **Map Categories** to create mappings.")
    else:
        # Flat table
        rows = []
        for key, indices in mapping.items():
            ft, cat = parse_key(key)
            scheme_count = categories_df[
                (categories_df["Scheme Type"] == ft) &
                (categories_df["Scheme Category"] == cat)
            ]["scheme_count"].values
            rows.append({
                "Scheme Type":     ft,
                "Scheme Category": cat,
                "Mapped Indices":  ", ".join(indices) if indices else "—",
                "# Indices":       len(indices),
                "# Schemes":       int(scheme_count[0]) if len(scheme_count) else 0,
                "Status":          "✅ Mapped" if indices else "⚠️ Empty",
            })

        view_df = (pd.DataFrame(rows)
                   .sort_values(["Scheme Type","Scheme Category"])
                   .reset_index(drop=True))

        # Summary by Scheme Type
        st.markdown('<div class="section-head">Summary by Scheme Type</div>',
                    unsafe_allow_html=True)
        summary = (
            view_df.groupby("Scheme Type")
            .agg(
                Categories  =("Scheme Category","count"),
                Mapped       =("# Indices", lambda x: (x>0).sum()),
                Total_Schemes=("# Schemes","sum"),
                Avg_Indices  =("# Indices","mean"),
            )
            .reset_index()
            .rename(columns={"Avg_Indices":"Avg Indices/Cat","Total_Schemes":"Total Schemes"})
        )
        summary["Avg Indices/Cat"] = summary["Avg Indices/Cat"].round(1)
        st.dataframe(
            summary.style.set_properties(**{
                "font-family":"JetBrains Mono, monospace","font-size":"0.8rem"
            }),
            use_container_width=True, hide_index=True,
        )

        # Full detail
        st.markdown('<div class="section-head">Full Mapping Detail</div>',
                    unsafe_allow_html=True)
        sv_col1, sv_col2 = st.columns([3, 1])
        with sv_col1:
            sv_search = st.text_input("🔎 Search", placeholder="Category or index…", key="vs")
        with sv_col2:
            sv_type = st.selectbox("Scheme Type", ["All"] + sorted(view_df["Scheme Type"].unique()), key="vt")

        sv_filt = view_df.copy()
        if sv_search:
            sv_filt = sv_filt[
                sv_filt["Scheme Category"].str.contains(sv_search, case=False, na=False) |
                sv_filt["Mapped Indices"].str.contains(sv_search, case=False, na=False)
            ]
        if sv_type != "All":
            sv_filt = sv_filt[sv_filt["Scheme Type"] == sv_type]

        def hl(val):
            if "✅" in str(val): return "background:#e8f5e9;color:#2e7d32"
            if "⚠️" in str(val): return "background:#fff3e0;color:#e65100"
            return ""

        st.dataframe(
            sv_filt.style
            .applymap(hl, subset=["Status"])
            .set_properties(**{"font-family":"JetBrains Mono, monospace","font-size":"0.76rem"}),
            use_container_width=True, hide_index=True,
        )

        # Index coverage
        st.markdown('<div class="section-head">Index Usage — How Many Categories Use Each Index</div>',
                    unsafe_allow_html=True)
        all_used = [idx for idxs in mapping.values() for idx in idxs]
        if all_used:
            cov = (pd.DataFrame(Counter(all_used).items(), columns=["Index","Categories Mapped"])
                   .sort_values("Categories Mapped", ascending=False)
                   .merge(idx_df[["index_name","section"]].rename(columns={"index_name":"Index"}),
                          on="Index", how="left"))
            st.dataframe(
                cov.style.background_gradient(cmap="Blues", subset=["Categories Mapped"])
                .set_properties(**{"font-family":"JetBrains Mono, monospace","font-size":"0.76rem"}),
                use_container_width=True, hide_index=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — EXPORT
# ─────────────────────────────────────────────────────────────────────────────
with tab_export:
    if not mapping:
        st.info("No mappings to export yet.")
    else:
        # Build flat export df
        export_rows = []
        for key, indices in mapping.items():
            ft, cat = parse_key(key)
            scheme_count = categories_df[
                (categories_df["Scheme Type"] == ft) &
                (categories_df["Scheme Category"] == cat)
            ]["scheme_count"].values
            n_schemes = int(scheme_count[0]) if len(scheme_count) else 0
            if indices:
                for idx in indices:
                    sec = idx_df[idx_df["index_name"] == idx]["section"].values
                    export_rows.append({
                        "scheme_type":     ft,
                        "scheme_category": cat,
                        "scheme_count":    n_schemes,
                        "index_name":      idx,
                        "index_section":   sec[0] if len(sec) else "",
                    })
            else:
                export_rows.append({
                    "scheme_type":     ft,
                    "scheme_category": cat,
                    "scheme_count":    n_schemes,
                    "index_name":      "",
                    "index_section":   "",
                })

        export_df = pd.DataFrame(export_rows)

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**CSV — flat, one row per category–index pair**")
            st.dataframe(export_df.head(8), use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Download CSV",
                export_df.to_csv(index=False).encode(),
                file_name="category_index_mapping.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with c2:
            st.markdown("**JSON — nested by Scheme Type → Category → [Indices]**")
            grouped = {}
            for key, indices in mapping.items():
                ft, cat = parse_key(key)
                grouped.setdefault(ft, {})[cat] = indices
            json_str = json.dumps(grouped, indent=2)
            st.code(json_str[:1000] + ("\n…" if len(json_str) > 1000 else ""), language="json")
            st.download_button(
                "⬇️ Download JSON",
                json_str.encode(),
                file_name="category_index_mapping.json",
                mime="application/json",
                use_container_width=True,
            )

        # Enriched parquet — mapping merged with index metrics
        st.markdown("---")
        st.markdown("**Parquet — enriched with index metrics from latest NSE data**")
        pq_files = [p for p in sorted(PARQUET_DIR.glob("*.parquet")) if "mapping" not in p.name]
        if pq_files and not export_df.empty:
            latest_idx = pd.read_parquet(pq_files[-1])
            enriched = export_df.merge(
                latest_idx.drop(columns=["source_file","section"], errors="ignore"),
                on="index_name", how="left"
            )
            st.dataframe(enriched.head(10), use_container_width=True, hide_index=True)
            out_pq = PARQUET_DIR / "category_index_mapping.parquet"
            enriched.to_parquet(out_pq, index=False)
            with open(out_pq, "rb") as f:
                st.download_button(
                    "⬇️ Download Enriched Parquet",
                    f.read(),
                    file_name="category_index_mapping.parquet",
                    mime="application/octet-stream",
                    use_container_width=True,
                )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<hr>
<div style="text-align:center;color:#bbb;font-family:JetBrains Mono,monospace;
     font-size:0.65rem;letter-spacing:0.08em">
    FOR RESEARCH PURPOSES ONLY · NOT INVESTMENT ADVICE
</div>
""", unsafe_allow_html=True)