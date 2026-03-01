# pages/portfolio_analysis.py
"""
Portfolio Analysis Page
=======================
Sidebar drives a strict 3-step linear load sequence:
  Step 1 → Load Customers           (list_customers)
  Step 2 → Load QoQ & Benchmarks    (load_qoq_data → build_benchmark_data
                                      → build_portfolio_trend)
  Step 3 → Load Portfolio            (load_portfolio_data → calculate_metrics)

Each step is gated — only enabled once the previous step is complete.
Step 2 is done BEFORE Step 3 so that benchmark equity_funds mapping is
available at portfolio-load time (used in build_benchmark_data).

Main area is read-only: displays whatever has been loaded.
"""

import streamlit as st
import pandas as pd

import utils.navbar as navbar
from utils.pdf_utils import format_currency_indian
from pages.config_editor import ConfigEditor
from agents.mf_portfolio_agent import MFPortfolioAgent
from agents.base import AgentStatus

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Portfolio Analysis", page_icon="📊", layout="wide")
navbar.navbar()
st.title("📊 Portfolio Analysis & PDF Generator")

# ── Agent singleton ────────────────────────────────────────────────────────────
agent = MFPortfolioAgent()

# ── Session state registry ─────────────────────────────────────────────────────
SS = {
    "all_customers":     "mf_all_customers",
    "selected_customer": "mf_selected_customer",
    "qoq_data":          "mf_qoq_data",
    "quarterly_returns": "mf_quarterly_returns",
    "quarter_labels":    "mf_quarter_labels",
    "blended_return":    "mf_blended_return",
    "portfolio_trend":   "mf_portfolio_trend",
    "customer_df":       "mf_customer_df",
    "metrics":           "mf_metrics",
    "pdf_path":          "mf_pdf_path",
    "pdf_filename":      "mf_pdf_filename",
    # persisted config
    "csv_path":          "mf_cfg_csv_path",
    "bucket_name":       "mf_cfg_bucket_name",
    "parquet_dir":       "mf_cfg_parquet_dir",
    "company_name":      "mf_cfg_company_name",
}

def _ss(key):
    return st.session_state.get(SS[key])

def _set(key, value):
    st.session_state[SS[key]] = value

def _run(skill_id: str, params: dict):
    """Run agent skill; surfaces errors inline."""
    resp = agent.run(skill_id, params)
    if resp.status == AgentStatus.FAILED:
        st.error(f"❌ `{skill_id}` — {resp.error}")
    elif resp.status == AgentStatus.RETRY:
        st.warning(f"⚠️ `{skill_id}` — {resp.error}")
    return resp

def _clear(keys: list):
    for k in keys:
        _set(k, None)

def _badge(done: bool) -> str:
    return "✅" if done else "🔘"


def _blended_display(blended) -> str:
    """Extract a single display string from blended_return (dict or float)."""
    if not blended:
        return "—"
    if isinstance(blended, dict):
        val = blended.get("ttm")
        if val is None:
            vals = [v for k, v in blended.items() if v is not None]
            val  = sum(vals) / len(vals) if vals else None
    else:
        try:
            val = float(blended)
        except (TypeError, ValueError):
            val = None
    return f"{val:.2f}%" if val is not None else "—"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR ─ 3-step linear flow
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📋 Load Data")
    st.caption("Complete each step in order.")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1 — Load Customers
    # ─────────────────────────────────────────────────────────────────────────
    step1_done = bool(_ss("all_customers"))
    with st.expander(f"{_badge(step1_done)}  Step 1 · Customers", expanded=not step1_done):
        csv_path = st.text_input(
            "CSV path",
            value=_ss("csv_path") or "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv",
            key="sb_csv",
        )
        _set("csv_path", csv_path)

        if st.button("🔄 Load Customers", use_container_width=True, key="btn_customers"):
            with st.spinner("Reading customer list…"):
                r = _run("list_customers", {"csv_path": csv_path})
            if r.status == AgentStatus.SUCCESS:
                customers = r.output["all_customers"]
                _set("all_customers", customers)
                # Pre-select first customer so selected_customer is never None on next rerun
                if not _ss("selected_customer") and customers:
                    _set("selected_customer", customers[0])
                # CSV changed → invalidate everything downstream
                _clear(["qoq_data","quarterly_returns","quarter_labels",
                        "blended_return","portfolio_trend",
                        "customer_df","metrics","pdf_path","pdf_filename"])
                st.success(f"{r.metadata['customer_count']} customers loaded")

        if step1_done:
            st.caption(f"✅ {len(_ss('all_customers'))} customers")

    # Customer picker — always visible once step 1 done
    all_customers = _ss("all_customers") or []
    if all_customers:
        # Ensure a valid selection is always stored before the widget renders
        if _ss("selected_customer") not in all_customers:
            _set("selected_customer", all_customers[0])

        def _on_customer_change():
            """Called by Streamlit when selectbox value changes — wipe downstream."""
            _set("selected_customer", st.session_state["sb_customer_pick"])
            _clear(["qoq_data","quarterly_returns","quarter_labels",
                    "blended_return","portfolio_trend",
                    "customer_df","metrics","pdf_path","pdf_filename"])

        selected_customer = st.selectbox(
            "👤 Customer",
            options=all_customers,
            index=all_customers.index(_ss("selected_customer")),
            key="sb_customer_pick",
            on_change=_on_customer_change,
        )
    else:
        selected_customer = None
        st.caption("_Load customers first (Step 1)_")

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2 — Load Portfolio  (must come first — equity_funds needed by benchmarks)
    # ─────────────────────────────────────────────────────────────────────────
    _cdf          = _ss("customer_df")
    step2_done    = _cdf is not None and not _cdf.empty
    step2_enabled = bool(selected_customer)

    with st.expander(
        f"{_badge(step2_done)}  Step 2 · Portfolio",
        expanded=(step2_enabled and not step2_done),
    ):
        if not step2_enabled:
            st.caption("_Select a customer first (Step 1)_")

        clicked_portfolio = st.button(
            "📥 Load Portfolio",
            use_container_width=True,
            disabled=not step2_enabled,
            type="primary" if (step2_enabled and not step2_done) else "secondary",
            key="btn_portfolio",
        )

        if clicked_portfolio:
            with st.spinner("Loading portfolio…"):
                rp = _run("load_portfolio_data", {
                    "csv_path":      csv_path,
                    "customer_name": selected_customer,
                })
            if rp.status == AgentStatus.SUCCESS:
                _set("customer_df", rp.output["customer_df"])
                # Customer data changed → wipe QoQ + PDF downstream
                _clear(["qoq_data","quarterly_returns","quarter_labels",
                        "blended_return","portfolio_trend","pdf_path","pdf_filename"])

                with st.spinner("Calculating metrics…"):
                    rm = _run("calculate_metrics", {"customer_df": rp.output["customer_df"]})
                if rm.status == AgentStatus.SUCCESS:
                    _set("metrics", rm.output["metrics"])
                    m = rm.output["metrics"]
                    st.success(
                        f"✅ {rp.metadata['records']} holdings · "
                        f"{m['num_funds']} funds · "
                        f"{len(m['amc_concentration'])} AMCs"
                    )

        if step2_done and _ss("metrics"):
            m = _ss("metrics")
            st.caption(
                f"Value: {format_currency_indian(m['total_value'])}  |  "
                f"E {m['allocation']['Equity']:.1f}%  "
                f"H {m['allocation']['Hybrid']:.1f}%  "
                f"D {m['allocation']['Debt']:.1f}%"
            )

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3 — Load QoQ & Benchmarks  (needs equity_funds from Step 2)
    # ─────────────────────────────────────────────────────────────────────────
    step3_done    = bool(_ss("quarterly_returns"))
    step3_enabled = step2_done   # portfolio must be loaded first

    with st.expander(
        f"{_badge(step3_done)}  Step 3 · QoQ & Benchmarks",
        expanded=(step3_enabled and not step3_done),
    ):
        if not step3_enabled:
            st.caption("_Complete Step 2 first — equity fund list is required for benchmark matching_")

        bucket_name = st.text_input(
            "GCS bucket",
            value=_ss("bucket_name") or "winrich",
            disabled=not step3_enabled,
            key="sb_bucket",
        )
        parquet_dir = st.text_input(
            "Benchmark parquet dir",
            value=_ss("parquet_dir") or "data",
            disabled=not step3_enabled,
            key="sb_parquet",
        )
        _set("bucket_name", bucket_name)
        _set("parquet_dir", parquet_dir)

        clicked_qoq = st.button(
            "📦 Load QoQ & Benchmarks",
            use_container_width=True,
            disabled=not step3_enabled,
            type="primary" if (step3_enabled and not step3_done) else "secondary",
            key="btn_qoq",
        )

        if clicked_qoq:
            equity_funds = (_ss("metrics") or {}).get("equity_funds", [])
            with st.spinner(f"Loading QoQ & benchmarks for {len(equity_funds)} equity funds…"):
                r3 = _run("load_qoq_and_benchmarks", {
                    "customer_name": selected_customer,
                    "equity_funds":  equity_funds,
                    "bucket_name":   bucket_name,
                    "parquet_dir":   parquet_dir,
                })

            if r3.status in (AgentStatus.SUCCESS, AgentStatus.RETRY):
                # Store whatever came back — even partial results are useful
                _set("quarterly_returns", r3.output.get("quarterly_returns", []))
                _set("quarter_labels",    r3.output.get("quarter_labels",    []))
                _set("blended_return",    r3.output.get("blended_return",    {}))
                _set("portfolio_trend",   r3.output.get("portfolio_trend",   []))

                n_qtrs = r3.metadata.get("trend_quarters", 0)
                n_rows = r3.metadata.get("return_rows",    0)

                if r3.status == AgentStatus.SUCCESS:
                    st.success(f"✅ {n_qtrs} quarters · {n_rows} return rows")
                else:
                    bench_err = r3.metadata.get("benchmark_error", r3.error)
                    st.warning(f"⚠️ Trend loaded ({n_qtrs} quarters) but benchmark rows failed: {bench_err}")

        if step3_done:
            labels  = _ss("quarter_labels") or []
            blended = _ss("blended_return")
            st.caption(f"Quarters: {', '.join(labels)}")
            if blended:
                st.caption(f"Blended return (TTM): {_blended_display(blended)}")

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────────
    # Report settings (always visible)
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Report Settings")
    company_name = st.text_input(
        "Company name",
        value=_ss("company_name") or "Winrich Professional Services",
        key="sb_company",
    )
    _set("company_name", company_name)
    include_commentary = st.checkbox("🤖 Include AI Commentary", value=False, key="sb_ai")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN AREA — gated on all 3 steps complete
# ══════════════════════════════════════════════════════════════════════════════
customer_df = _ss("customer_df")
metrics     = _ss("metrics")

_cdf_loaded = customer_df is not None and not customer_df.empty

if not _cdf_loaded or metrics is None:
    steps_done = sum([
        bool(_ss("all_customers")),
        (_ss("customer_df") is not None and not _ss("customer_df").empty),
        bool(_ss("quarterly_returns")),
    ])
    st.progress(steps_done / 3, text=f"Step {steps_done} of 3 complete")
    messages = {
        0: "👈 Start with **Step 1** in the sidebar — click **Load Customers**.",
        1: "👈 Continue with **Step 2** — select a customer, then click **Load Portfolio**.",
        2: "👈 Almost there — click **Step 3 · Load QoQ & Benchmarks** in the sidebar.",
    }
    st.info(messages.get(steps_done, ""))
    st.stop()

selected_customer = _ss("selected_customer") or ""

# ── Model allocation — hoisted to page scope so PDF button always has values ──
_editor     = ConfigEditor()
_def_alloc  = _editor.config_data.get("default_model_allocation", {})
_use_custom = _editor.config_data.get("use_custom_thresholds", False)

if _use_custom:
    model_equity = metrics["allocation"]["Equity"]
    model_hybrid = metrics["allocation"]["Hybrid"]
    model_debt   = metrics["allocation"]["Debt"]
else:
    with st.expander("⚙️ Model Allocation Overrides", expanded=False):
        _c1, _c2, _c3 = st.columns(3)
        model_equity = _c1.number_input("Equity %", 0.0, 100.0,
                                         float(_def_alloc.get("Equity", 60.0)), key="model_eq")
        model_hybrid = _c2.number_input("Hybrid %", 0.0, 100.0,
                                         float(_def_alloc.get("Balance (Hybrid)", 20.0)), key="model_hy")
        model_debt   = _c3.number_input("Debt %",   0.0, 100.0,
                                         float(_def_alloc.get("Debt", 20.0)), key="model_dt")

# ── KPI strip ──────────────────────────────────────────────────────────────────
st.header(f"Portfolio: {selected_customer}")
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Value",      format_currency_indian(metrics["total_value"]))
k2.metric("Total Funds",      metrics["num_funds"])
k3.metric("Equity %",         f"{metrics['allocation']['Equity']:.1f}%")
k4.metric("AMCs",             len(metrics["amc_concentration"]))
_bl = _ss("blended_return")
k5.metric("Blended Return (TTM)", _blended_display(_bl))

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Holdings", "📈 Allocations", "💼 Performance", "📉 QoQ & Benchmarks", "⚙️ Settings"
])

with tab1:
    st.subheader("Current Holdings")
    disp = customer_df[
        ["h_name","s_name","Nature","BalUnit","CurValue","FolioXIRR","absReturn"]
    ].copy()
    disp.columns = ["AMC","Scheme","Type","Units","Current Value","XIRR %","Abs Return %"]
    disp["Current Value"] = disp["Current Value"].apply(lambda x: f"₹{x:,.2f}")
    st.dataframe(disp, use_container_width=True)

with tab2:
    st.subheader("Asset Allocation")
    cl, cr = st.columns(2)
    with cl:
        st.write("**Current Allocation**")
        st.dataframe(pd.DataFrame({
            "Asset Class": ["Equity","Hybrid","Debt"],
            "Current %":   [round(metrics["allocation"][k], 2) for k in ("Equity","Hybrid","Debt")],
        }), use_container_width=True)
    with cr:
        st.write("**Model vs Current**")
        st.dataframe(pd.DataFrame({
            "Asset Class": ["Equity","Hybrid","Debt"],
            "Model %":     [model_equity, model_hybrid, model_debt],
            "Current %":   [round(metrics["allocation"][k], 2) for k in ("Equity","Hybrid","Debt")],
            "Variance":    [
                round(metrics["allocation"]["Equity"] - model_equity, 2),
                round(metrics["allocation"]["Hybrid"] - model_hybrid, 2),
                round(metrics["allocation"]["Debt"]   - model_debt,   2),
            ],
        }), use_container_width=True)

with tab3:
    st.subheader("Fund Performance")
    if metrics["equity_funds"]:
        st.write("**Equity Funds**")
        eq_df = pd.DataFrame(metrics["equity_funds"])
        eq_df.columns = [
            "Fund","XIRR %","Cat XIRR %","Benchmark","1M %","3M %","1Y %","3Y %","5Y %"
        ]
        st.dataframe(eq_df, use_container_width=True)
    if metrics["hybrid_funds"]:
        st.write("**Hybrid Funds**")
        hy_df = pd.DataFrame(metrics["hybrid_funds"])
        hy_df.columns = ["Fund","XIRR %"]
        st.dataframe(hy_df, use_container_width=True)

with tab4:
    st.subheader("Quarter-on-Quarter Performance & Benchmarks")
    st.caption("Loaded via sidebar Step 2 — reload by changing customer or clicking Step 2 again.")

    _labels  = _ss("quarter_labels")    or []
    _returns = _ss("quarterly_returns") or []
    _trend   = _ss("portfolio_trend")   or []
    _blended = _ss("blended_return")

    if not _returns:
        st.info("No QoQ data. Complete **Step 3** in the sidebar.")
    else:
        if _blended:
            st.metric("Blended Portfolio Return (TTM)", _blended_display(_blended))

        if _trend:
            st.write("**Portfolio Growth — Quarter on Quarter**")
            tdf = pd.DataFrame(_trend).copy()
            tdf["gain"]     = tdf["current"] - tdf["invested"]
            tdf["return%"]  = ((tdf["gain"] / tdf["invested"]) * 100).round(2)
            tdf["invested"] = tdf["invested"].apply(lambda x: f"₹{x:,.0f}")
            tdf["current"]  = tdf["current"].apply(lambda x: f"₹{x:,.0f}")
            tdf["gain"]     = tdf["gain"].apply(lambda x: f"₹{x:,.0f}")
            tdf["return%"]  = tdf["return%"].apply(lambda x: f"{x:+.2f}%")
            tdf.columns     = ["Quarter","Invested","Current Value","Gain / Loss","Return %"]
            st.dataframe(tdf, use_container_width=True)

        if _returns:
            st.write("**Quarterly Returns — Fund vs Benchmark**")
            st.dataframe(pd.DataFrame(_returns), use_container_width=True)

with tab5:
    st.subheader("Settings")
    st.info("Company name and AI commentary toggle are in the sidebar **Report Settings** section.")

# ══════════════════════════════════════════════════════════════════════════════
# PDF GENERATION FOOTER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
_, col_pdf, _ = st.columns([1, 2, 1])

with col_pdf:
    if not _ss("quarterly_returns"):
        st.warning("⚠️ QoQ data not loaded — PDF will omit QoQ tables.")

    if st.button("🚀 Generate PDF Report", use_container_width=True, type="primary"):
        try:
            commentary = ""
            if include_commentary:
                with st.spinner("Generating AI commentary…"):
                    r6 = _run("generate_ai_commentary", {
                        "portfolio_data": {
                            "equity_funds":      metrics["equity_funds"],
                            "allocation":        metrics["allocation"],
                            "quarterly_returns": _ss("quarterly_returns") or [],
                            "blended_return":    _ss("blended_return")    or 0,
                        }
                    })
                if r6.status == AgentStatus.SUCCESS:
                    commentary = r6.output.get("commentary", "")

            with st.spinner("Rendering PDF…"):
                r7 = _run("generate_pdf_report", {
                    "selected_customer": selected_customer,
                    "company_name":      company_name,
                    "customer_df":       customer_df,
                    "metrics":           metrics,
                    "quarterly_returns": _ss("quarterly_returns") or [],
                    "quarter_labels":    _ss("quarter_labels")    or [],
                    "blended_return":    _ss("blended_return")    or {},
                    "portfolio_trend":   _ss("portfolio_trend")   or [],
                    "commentary":        commentary,
                    "model_allocation": {
                        "Equity": model_equity,
                        "Hybrid": model_hybrid,
                        "Debt":   model_debt,
                    },
                })

            if r7.status == AgentStatus.SUCCESS:
                _set("pdf_path",     r7.output["pdf_path"])
                _set("pdf_filename", r7.output["filename"])

                for w in r7.metadata.get("data_warnings", []):
                    st.warning(f"⚠️ {w}")

                st.success("✅ PDF Report Generated Successfully!")
                with open(r7.output["pdf_path"], "rb") as f:
                    st.download_button(
                        label="📥 Download PDF Report",
                        data=f,
                        file_name=r7.output["filename"],
                        mime="application/pdf",
                        use_container_width=True,
                    )

                qr = r7.metadata.get("qoq_rows", 0)
                tq = r7.metadata.get("trend_quarters", 0)
                st.info(
                    f"**{selected_customer}** · {format_currency_indian(metrics['total_value'])} · "
                    f"{len(metrics['equity_funds'])} equity · {len(metrics['hybrid_funds'])} hybrid · "
                    f"{len(metrics['amc_concentration'])} AMCs · {qr} QoQ rows · {tq} quarters"
                )

        except Exception as e:
            st.error(f"❌ Unexpected error: {e}")
            with st.expander("Stack trace"):
                st.exception(e)
