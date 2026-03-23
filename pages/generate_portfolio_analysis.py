"""
pages/portfolio_analysis_v2.py
================================
WinRich — MF Portfolio Performance Report Generator (Streamlit page)

Loads the MutualFunds datawarehouse CSV from GCS automatically:
    gs://winrich/Datawarehouse/MutualFunds/<YYYY>/<MM>/mutualfunds.csv

The path resolves to the most recent business day's month, stepping back
up to 10 business days if today's file isn't available yet.

All pipeline steps run through MFPortfolioAgent skills, which conform
to the Agent/AgentResponse/AgentStatus contract in agents/base.py.
"""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
import streamlit as st

from agents.base import AgentStatus
from agents.mf_portfolio_agent import MFPortfolioAgent
from utils.auth import require_login
from utils.navbar import navbar

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="WinRich — Portfolio Report",
    page_icon="📊",
    layout="wide",
)

require_login()

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_BUCKET  = "winrich"
_DEFAULT_PARQUET = "data"
_DEFAULT_OUT_DIR = "reports"


# ── Session-state initialisation ──────────────────────────────────────────────
def _init_state():
    defaults = {
        "agent":            None,
        "all_customers":    [],
        "customers_loaded": False,
        "customer_df":      None,
        "metrics":          None,
        "pdf_path":         None,
        "pdf_bytes":        None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


def _get_agent() -> MFPortfolioAgent:
    if st.session_state.agent is None:
        st.session_state.agent = MFPortfolioAgent()
    return st.session_state.agent


# ── GCS settings sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    if os.path.exists("assets/winrich-logo.png"):
        st.image("assets/winrich-logo.png", width=180)
    st.title("WinRich Reports")
    st.divider()

    st.subheader("⚙️ Settings")
    bucket_name: str = st.text_input(
        "GCS Bucket", value=_DEFAULT_BUCKET,
        help="GCS bucket containing the datawarehouse CSV.",
    )
    as_of_date = st.date_input(
        "As-of Date",
        value=datetime.today(),
        help="The loader will resolve to the nearest available business day at or before this date.",
    )
    max_lookback: int = int(st.number_input(
        "Max lookback (business days)", min_value=1, max_value=30, value=10,
        step=1,
        help="How many business days to look back if today's file isn't available.",
    ))
    parquet_dir: str = st.text_input(
        "Benchmark parquet folder", value=_DEFAULT_PARQUET,
        help="Local folder containing Index_Dashboard_*.parquet files.",
    )
    logo_path: str = st.text_input(
        "Logo path", value="assets/winrich-logo.png",
        help="Path to the logo image, relative to the app root (e.g. assets/winrich-logo.png).",
    )
    if logo_path:
        _abs = os.path.abspath(logo_path)
        if os.path.exists(_abs):
            st.caption(f"✅ Logo found: `{_abs}`")
        else:
            st.caption(f"⚠️ Logo not found at: `{_abs}`")
    skip_commentary: bool = st.checkbox(
        "Skip AI commentary (faster)", value=False,
        help="Skips the Claude API call. Useful for quick test runs.",
    )
    output_dir: str = st.text_input("PDF output folder", value=_DEFAULT_OUT_DIR)

    st.divider()

    # ── Load customers button ─────────────────────────────────────────────────
    if st.button("🔄 Load Customers from GCS", use_container_width=True):
        with st.spinner("Connecting to GCS and loading customer list…"):
            agent = _get_agent()
            resp  = agent.run("list_customers", {
                "bucket_name":       bucket_name,
                "as_of_date":        datetime(as_of_date.year, as_of_date.month, as_of_date.day),
                "max_lookback_days": max_lookback,
            })

        if resp.status == AgentStatus.SUCCESS:
            st.session_state.all_customers    = resp.output["all_customers"]
            st.session_state.customers_loaded = True
            st.success(
                f"✅ Loaded {resp.metadata.get('customer_count', 0)} customers "
                f"({resp.output['total_records']:,} records)"
            )
        else:
            st.error(f"❌ {resp.error}")

    if st.session_state.customers_loaded:
        st.caption(f"{len(st.session_state.all_customers)} customers available")


# ── Main panel ────────────────────────────────────────────────────────────────
navbar()
st.title("📊 Portfolio Performance Report")
st.caption(
    f"Data source: `gs://{bucket_name}/Datawarehouse/MutualFunds/<YYYY>/<MM>/mutualfunds.csv` "
    f"— resolves to nearest business day on or before **{as_of_date}**"
)

if not st.session_state.customers_loaded:
    st.info("👈 Click **Load Customers from GCS** in the sidebar to begin.")
    st.stop()

# ── Customer selector ─────────────────────────────────────────────────────────
col_sel, col_btn = st.columns([3, 1])

with col_sel:
    selected_customer = st.selectbox(
        "Select customer",
        options=st.session_state.all_customers,
        index=0,
    )

with col_btn:
    st.markdown("&nbsp;", unsafe_allow_html=True)  # alignment spacer
    generate_clicked = st.button(
        "⚡ Generate Report", type="primary", use_container_width=True
    )

st.divider()

# ── Report generation ─────────────────────────────────────────────────────────
if generate_clicked and selected_customer:
    agent = _get_agent()

    base_params = {
        "bucket_name":       bucket_name,
        "as_of_date":        datetime(as_of_date.year, as_of_date.month, as_of_date.day),
        "max_lookback_days": max_lookback,
        "parquet_dir":       parquet_dir,
        "output_dir":        output_dir,
        "logo_path":         os.path.abspath(logo_path) if logo_path else "",
        "company_name":      "WinRich Professional Services",
        "prepared_by":       "WinRich Research Desk",
        "website":           "www.winrich.in",
        "email":             "support@winrich.in",
        "skip_commentary":   skip_commentary,
    }

    progress = st.progress(0, text="Starting…")

    # ── Step 1: load portfolio ────────────────────────────────────────────────
    progress.progress(10, text="📥 Loading portfolio from GCS…")
    r1 = agent.run("load_portfolio_data", {
        **base_params, "customer_name": selected_customer
    })
    if r1.status == AgentStatus.FAILED:
        st.error(f"❌ Could not load portfolio: {r1.error}")
        st.stop()

    customer_df       = r1.output["customer_df"]
    selected_customer = r1.output["selected_customer"]
    resolved_path     = r1.output.get("resolved_path", "")

    # ── Step 2: enrich benchmarks ─────────────────────────────────────────────
    progress.progress(25, text="🔍 Resolving fund benchmarks…")
    r2 = agent.run("enrich_benchmarks", {**base_params, "customer_df": customer_df})
    if r2.status == AgentStatus.SUCCESS:
        customer_df = r2.output["customer_df"]
    else:
        st.warning(f"⚠️ Benchmark enrichment failed: {r2.error}")

    # ── Step 3: enrich fund ranks ─────────────────────────────────────────────
    progress.progress(40, text="🏆 Fetching WinRich fund rankings…")
    r3 = agent.run("enrich_fund_ranks", {"customer_df": customer_df})
    if r3.status == AgentStatus.SUCCESS:
        customer_df = r3.output["customer_df"]
    else:
        st.warning(f"⚠️ Fund ranking failed: {r3.error}")

    # ── Step 4: calculate metrics ─────────────────────────────────────────────
    progress.progress(55, text="📐 Calculating metrics…")
    r4 = agent.run("calculate_metrics", {"customer_df": customer_df})
    if r4.status == AgentStatus.FAILED:
        st.error(f"❌ Metrics failed: {r4.error}")
        st.stop()
    metrics = r4.output["metrics"]

    # ── Step 5: AI commentary ─────────────────────────────────────────────────
    commentary = []
    if not skip_commentary:
        import math as _math

        def _cf(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if (_math.isnan(f) or _math.isinf(f)) else f
            except (TypeError, ValueError):
                return None

        _cur_vals   = customer_df["CurValue"].fillna(0)
        _inv_vals   = customer_df["TotalInvAmt"].fillna(0) if "TotalInvAmt" in customer_df.columns else _cur_vals * 0
        _total_cur  = float(_cur_vals.sum())
        _total_inv  = float(_inv_vals.sum())
        try:
            _port_xirr = float(
                (customer_df["FolioXIRR"].fillna(0) * _cur_vals).sum() / _cur_vals.sum()
            ) if _cur_vals.sum() > 0 else None
        except Exception:
            _port_xirr = None
        try:
            _start_dates      = pd.to_datetime(customer_df["FolioStartDate"], errors="coerce").dropna()
            _investment_start = _start_dates.min().strftime("%B %d, %Y") if not _start_dates.empty else ""
        except Exception:
            _investment_start = ""

        _equity_names = {f["name"] for f in metrics["equity_funds"]}
        _hybrid_names = {f["name"] for f in metrics["hybrid_funds"]}

        _all_funds = []
        for _, _row in customer_df.iterrows():
            fsd = _row.get("FolioStartDate", "")
            if hasattr(fsd, "strftime"):
                fsd = fsd.strftime("%d-%b-%Y")
            _all_funds.append({
                "name":                 str(_row.get("s_name", "")),
                "nature":               str(_row.get("Nature", "")),
                "xirr":                 _cf(_row.get("FolioXIRR")),
                "winrich_rank":         str(_row.get("winrich_rank", "N/A") or "N/A"),
                "benchmark_index":      str(_row.get("benchmark_index") or ""),
                "benchmark_xirr":       _cf(_row.get("benchmark_return_1yr")) or _cf(_row.get("benchmark_return_3yr")) or _cf(_row.get("benchmark_return_5yr")),
                "benchmark_return_1yr": _cf(_row.get("benchmark_return_1yr")),
                "benchmark_return_3yr": _cf(_row.get("benchmark_return_3yr")),
                "benchmark_return_5yr": _cf(_row.get("benchmark_return_5yr")),
                "folio_start_date":     str(fsd).strip(),
            })

        progress.progress(70, text="🤖 Generating AI commentary…")
        r5_com = agent.run("generate_ai_commentary", {
            "portfolio_data": {
                "client_name":         selected_customer,
                "report_date":         as_of_date.strftime("%B %d, %Y"),
                "investment_start":    _investment_start,
                "total_invested":      _cf(_total_inv),
                "total_current_value": _cf(_total_cur),
                "total_gain":          round(_total_cur - _total_inv, 2),
                "portfolio_xirr":      _cf(_port_xirr),
                "n_funds":             int(metrics["num_funds"]),
                "n_amcs":              len(metrics["amc_concentration"]),
                "equity_funds":        metrics["equity_funds"],
                "hybrid_funds":        metrics["hybrid_funds"],
                "amc_concentration":   metrics["amc_concentration"],
                "client_allocation":   metrics["allocation"],
                "allocation_rows": [
                    {
                        "asset_class":        ac,
                        "your_allocation":    f"{pct:.2f}%",
                        "funds_in_portfolio": " | ".join(
                            str(row["s_name"])
                            for _, row in customer_df[
                                customer_df["Nature"] == ("Balance" if ac == "Hybrid" else ac)
                            ].iterrows()
                        ) or "—",
                    }
                    for ac, pct in metrics["allocation"].items()
                    if (pct or 0) > 0
                ],
                "all_funds": _all_funds,
            }
        })
        if r5_com.status == AgentStatus.SUCCESS:
            commentary = r5_com.output.get("commentary", [])
        else:
            st.warning(f"⚠️ Commentary skipped: {r5_com.error}")

    # ── Step 5b: store portfolio summary (metrics + commentary) ───────────────
    progress.progress(78, text="💾 Storing portfolio summary…")
    r5b = agent.run("store_portfolio_summary", {
        "customer_name": selected_customer,
        "metrics":       metrics,
        "commentary":    commentary,
        "as_of_date":    str(as_of_date),
    })
    if r5b.status != AgentStatus.SUCCESS:
        st.warning(f"⚠️ Portfolio summary not stored: {r5b.error}")

    # ── Step 6: generate PDF ──────────────────────────────────────────────────
    progress.progress(85, text="📄 Rendering PDF…")
    r6 = agent.run("generate_pdf_report", {
        **base_params,
        "customer_df":       customer_df,
        "metrics":           metrics,
        "selected_customer": selected_customer,
        "resolved_path":     resolved_path,
        "commentary":        commentary,
    })

    progress.progress(100, text="✅ Done!")
    progress.empty()

    if r6.status == AgentStatus.FAILED:
        st.error(f"❌ PDF generation failed: {r6.error}")
        if r6.metadata.get("traceback"):
            with st.expander("Full traceback"):
                st.code(r6.metadata["traceback"])
        st.stop()

    # ── Cache results ─────────────────────────────────────────────────────────
    pdf_path = r6.output["pdf_path"]
    st.session_state.pdf_path = pdf_path
    try:
        with open(pdf_path, "rb") as f:
            st.session_state.pdf_bytes = f.read()
    except Exception:
        st.session_state.pdf_bytes = None

    st.session_state.customer_df = customer_df
    st.session_state.metrics     = metrics


# ── Results panel (shown after generation) ────────────────────────────────────
if st.session_state.pdf_path:
    metrics = st.session_state.metrics

    st.success(f"✅ Report ready — `{os.path.basename(st.session_state.pdf_path)}`")

    if st.session_state.pdf_bytes:
        st.download_button(
            label="⬇️ Download PDF",
            data=st.session_state.pdf_bytes,
            file_name=os.path.basename(st.session_state.pdf_path),
            mime="application/pdf",
            type="primary",
        )

    st.divider()

    # ── KPI summary tiles ─────────────────────────────────────────────────────
    if metrics:
        total = metrics["total_value"]
        alloc = metrics["allocation"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Portfolio Value", f"Rs. {total:,.0f}")
        c2.metric("Equity",          f"{alloc['Equity']:.1f}%")
        c3.metric("Hybrid",          f"{alloc['Hybrid']:.1f}%")
        c4.metric("Debt",            f"{alloc['Debt']:.1f}%")

    # ── Fund tables ───────────────────────────────────────────────────────────
    tabs = st.tabs(["Equity Funds", "Hybrid Funds", "Debug"])

    with tabs[0]:
        eq = metrics.get("equity_funds", []) if metrics else []
        if eq:
            import pandas as pd
            df_eq = pd.DataFrame(eq)[["name", "xirr", "benchmark_index",
                                      "benchmark_return_1yr", "benchmark_return_3yr"]]
            df_eq.columns = ["Fund", "XIRR %", "Benchmark", "1Y Bench %", "3Y Bench %"]
            st.dataframe(df_eq, use_container_width=True, hide_index=True)
        else:
            st.info("No equity funds found.")

    with tabs[1]:
        hy = metrics.get("hybrid_funds", []) if metrics else []
        if hy:
            import pandas as pd
            st.dataframe(pd.DataFrame(hy), use_container_width=True, hide_index=True)
        else:
            st.info("No hybrid funds found.")

    with tabs[2]:
        st.json({
            "pdf_path":   st.session_state.pdf_path,
            "num_equity": len(metrics.get("equity_funds", [])) if metrics else 0,
            "num_hybrid": len(metrics.get("hybrid_funds", [])) if metrics else 0,
        })