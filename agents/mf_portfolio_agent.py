"""
agents/mf_portfolio_agent.py
============================
MF Portfolio Agent — orchestrates the mutual-fund report pipeline.

Customer data is sourced exclusively by calling WinrichMFDataAgent; this
agent never touches GCS or CSV files directly.

Dependency graph
----------------
    MFPortfolioAgent
        └── WinrichMFDataAgent          (data layer — GCS / CSV)
                └── datawarehouse_loader
                └── customer_portfolio
        └── PortfolioDataLoader         (QoQ parquets from GCS)
        └── MFPortfolioPDFGenerator     (PDF rendering)
        └── generate_ai_commentary      (Claude API)

Skills (public)
---------------
  1. list_customers            – proxy to WinrichMFDataAgent.list_customers
  2. load_portfolio_data       – proxy to WinrichMFDataAgent.load_customer_portfolio
  3. calculate_metrics         – derive allocation %, fund lists, AMC concentration
  4. load_qoq_and_benchmarks   – QoQ quarter data + benchmark returns + trend
  5. generate_ai_commentary    – Claude narrative commentary
  6. generate_pdf_report       – assemble portfolio_data and render PDF
  7. generate_quarterly_report – end-to-end orchestrator (single call)
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from agents.base import Agent, AgentResponse, AgentStatus
from agents.winrich_mf_data_agent import WinrichMFDataAgent

from utils.mf_portfolio_pdf_generator import (
    MFPortfolioPDFGenerator,
    generate_ai_commentary as _generate_ai_commentary,
)
from utils.pdf_utils import format_currency_indian
from utils.mf_qoq_loader import PortfolioDataLoader
from utils.build_qoq_data import build_qoq_data
from utils.benchmark_utils import (
    build_quarterly_returns_with_benchmarks,
    _QUARTER_MONTH_MAP,
)

_DEFAULT_BUCKET = "winrich"

_QUARTER_LABEL_MAP: Dict[str, str] = {
    "Q3_2023":  "Q4 FY23 (Jan-Mar '23)",
    "Q6_2023":  "Q1 FY24 (Apr-Jun '23)",
    "Q9_2023":  "Q2 FY24 (Jul-Sep '23)",
    "Q12_2023": "Q3 FY24 (Oct-Dec '23)",
    "Q3_2024":  "Q4 FY24 (Jan-Mar '24)",
    "Q6_2024":  "Q1 FY25 (Apr-Jun '24)",
    "Q9_2024":  "Q2 FY25 (Jul-Sep '24)",
    "Q12_2024": "Q3 FY25 (Oct-Dec '24)",
    "Q3_2025":  "Q4 FY25 (Jan-Mar '25)",
    "Q6_2025":  "Q1 FY26 (Apr-Jun '25)",
    "Q9_2025":  "Q2 FY26 (Jul-Sep '25)",
    "Q12_2025": "Q3 FY26 (Oct-Dec '25)",
    "Q3_2026":  "Q4 FY26 (Jan-Mar '26)",
}

_BENCH_FILES: Dict[str, str] = {
    "Q3_2025":  "Index_Dashboard_MAR2025.parquet",
    "Q6_2025":  "Index_Dashboard_JUN2025.parquet",
    "Q9_2025":  "Index_Dashboard_SEP2025.parquet",
    "Q12_2025": "Index_Dashboard_DEC2025.parquet",
    "Q3_2024":  "Index_Dashboard_MAR2024.parquet",
    "Q6_2024":  "Index_Dashboard_JUN2024.parquet",
    "Q9_2024":  "Index_Dashboard_SEP2024.parquet",
    "Q12_2024": "Index_Dashboard_DEC2024.parquet",
}

_SCHEME_DF: pd.DataFrame = pd.read_csv("data/SchemeData2301262313SS.csv")
_SCHEME_DF.columns = _SCHEME_DF.columns.str.strip()


def _clean_fund_name(name: str) -> str:
    for pat in (
        r"\s*-\s*Regular.*$", r"\s*-\s*Direct.*$", r"\s*-\s*Growth.*$",
        r"\s*-\s*IDCW.*$",    r"\s*-\s*Dividend.*$", r"\s*\(.*\)$",
    ):
        name = re.sub(pat, "", name, flags=re.IGNORECASE)
    return name.strip()


def _get_amc(fund_name: str) -> str:
    cleaned = _clean_fund_name(fund_name)
    match   = _SCHEME_DF[
        _SCHEME_DF["Scheme Name"].str.contains(cleaned, case=False, na=False, regex=False)
    ]
    if match.empty:
        return "Unknown"
    amc = match.iloc[0]["AMC"]
    for suffix in (" Limited", " Ltd.", " Ltd", " Pvt.", " Private",
                   " Asset Management Company", " Asset Management",
                   " Investment Managers (India)", " Investment Managers",
                   " Mutual Fund"):
        amc = amc.replace(suffix, "")
    amc = amc.strip()
    # Append "AMC" for readability if not already present
    if not amc.endswith(" AMC"):
        amc = amc + " AMC"
    return amc


def _sort_quarter_keys(keys) -> list:
    def _rank(k: str) -> int:
        parts = k.split("_")
        return int(parts[1]) * 100 + int(parts[0].replace("Q", ""))
    return sorted(keys, key=_rank)


def _mask_phone(phone: str) -> str:
    digits = "".join(filter(str.isdigit, str(phone)))
    return "*" * (len(digits) - 4) + digits[-4:]


def _mask_email(email: str) -> str:
    user, domain = email.split("@")
    return user[0] + "*" * (len(user) - 1) + "@" + domain


class MFPortfolioAgent(Agent):
    """
    Orchestrates the MF portfolio report pipeline.

    Customer data is fetched by delegating to WinrichMFDataAgent.
    This agent owns: metrics calculation, QoQ loading, AI commentary, PDF rendering.

    Parameters
    ----------
    data_agent : WinrichMFDataAgent, optional
        Pass a pre-built instance to share the SchemeLookup cache across
        multiple agents. If None, one is created lazily on first use.
    """

    name = "MFPortfolioAgent"

    def __init__(self, data_agent: Optional[WinrichMFDataAgent] = None):
        self._data_agent = data_agent

    def _get_data_agent(self) -> WinrichMFDataAgent:
        if self._data_agent is None:
            self._data_agent = WinrichMFDataAgent()
        return self._data_agent

    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "list_customers":            self._list_customers,
            "load_portfolio_data":       self._load_portfolio_data,
            "calculate_metrics":         self._calculate_metrics,
            "load_qoq_and_benchmarks":   self._load_qoq_and_benchmarks,
            "generate_ai_commentary":    self._generate_ai_commentary,
            "generate_pdf_report":       self._generate_pdf_report,
            "generate_quarterly_report": self._generate_quarterly_report,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ── Skill 0: list_customers (proxy) ───────────────────────────────────────
    def _list_customers(self, params: Dict[str, Any]) -> AgentResponse:
        """Proxy to WinrichMFDataAgent.list_customers."""
        resp = self._get_data_agent().run("list_customers", params)
        if resp.status == AgentStatus.FAILED:
            resp.error = f"[WinrichMFDataAgent] {resp.error}"
        return resp

    # ── Skill 1: load_portfolio_data (proxy) ───────────────────────────────────
    def _load_portfolio_data(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Proxy to WinrichMFDataAgent.load_customer_portfolio.

        Required params: customer_name : str
        Optional params: bucket_name, as_of_date, max_lookback_days

        Output keys: customer_df, selected_customer, all_customers, resolved_path
        """
        resp = self._get_data_agent().run("load_customer_portfolio", params)
        if resp.status == AgentStatus.FAILED:
            resp.error = f"[WinrichMFDataAgent] {resp.error}"
        return resp

    # ── Skill 2: calculate_metrics ────────────────────────────────────────────
    def _calculate_metrics(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Derive allocation %, equity_funds, hybrid_funds, amc_concentration.

        Required params: customer_df : pd.DataFrame
        Output keys:     metrics : dict
        """
        customer_df: pd.DataFrame = params.get("customer_df")
        if customer_df is None:
            return AgentResponse(AgentStatus.FAILED, error="'customer_df' is required")

        # Force-coerce numeric columns (CSV may have comma-formatted strings)
        customer_df = customer_df.copy()
        for col in ("CurValue", "TotalInvAmt", "FolioXIRR", "NatureXIRR", "benchmark_xirr"):
            if col in customer_df.columns:
                customer_df[col] = pd.to_numeric(
                    customer_df[col].astype(str).str.replace(",", "").str.strip(),
                    errors="coerce",
                )

        total_value   = float(customer_df["CurValue"].fillna(0).sum())
        equity_value  = float(customer_df[customer_df["Nature"] == "Equity"]["CurValue"].fillna(0).sum())
        balance_value = float(customer_df[customer_df["Nature"] == "Balance"]["CurValue"].fillna(0).sum())
        debt_value    = float(customer_df[customer_df["Nature"] == "Debt"]["CurValue"].fillna(0).sum())

        def _pct(v):
            return (v / total_value * 100) if total_value > 0 else 0.0

        equity_funds = [
            {
                "name":               row["s_name"],
                "xirr":               row["FolioXIRR"],
                "benchmark":          row["NatureXIRR"],
                "benchmark_index":    row.get("benchmark_index",    0),
                "benchmark_return_1m":  row.get("benchmark_return_1m",  0),
                "benchmark_return_3m":  row.get("benchmark_return_3m",  0),
                "benchmark_return_1yr": row.get("benchmark_return_1yr", 0),
                "benchmark_return_3yr": row.get("benchmark_return_3yr", 0),
                "benchmark_return_5yr": row.get("benchmark_return_5yr", 0),
            }
            for _, row in customer_df[customer_df["Nature"] == "Equity"].iterrows()
        ]

        hybrid_funds = [
            {"name": row["s_name"], "xirr": row["FolioXIRR"]}
            for _, row in customer_df[customer_df["Nature"] == "Balance"].iterrows()
        ]

        # AMC concentration — {amc_name: {'value': float, 'pct': float}}
        # Try lookup-based short names first; fall back to CSV "AMC" column
        amc_value_map: Dict[str, float] = {}
        has_amc_col = "AMC" in customer_df.columns
        for _, row in customer_df.iterrows():
            cur_val = float(row.get("CurValue") or 0)
            if cur_val <= 0:
                continue
            amc = _get_amc(row["s_name"])
            if amc == "Unknown" and has_amc_col:
                # Fall back to CSV AMC column — strip common suffixes
                raw = str(row["AMC"]).strip()
                for suffix in (" Limited", " Ltd.", " Ltd", " Pvt.", " Private",
                               " (India) Private", " (India)", " Asset Management Company",
                               " Asset Management", " Investment Managers", " Mutual Fund"):
                    raw = raw.replace(suffix, "")
                amc = raw.strip() + " AMC" if raw and not raw.endswith(" AMC") else raw.strip()
            if amc and amc != "Unknown":
                amc_value_map[amc] = amc_value_map.get(amc, 0.0) + cur_val
        amc_concentration: Dict[str, dict] = {
            amc: {
                "value": val,
                "pct":   (val / total_value * 100) if total_value > 0 else 0.0,
            }
            for amc, val in sorted(amc_value_map.items(), key=lambda x: -x[1])
        }

        # Other nature = everything that isn't Equity / Balance / Debt
        known_natures = {"Equity", "Balance", "Debt"}
        other_value   = float(customer_df[~customer_df["Nature"].isin(known_natures)]["CurValue"].fillna(0).sum())

        metrics = {
            "total_value":       total_value,
            "allocation": {
                "Equity": _pct(equity_value),
                "Hybrid": _pct(balance_value),
                "Other":  _pct(other_value),
                "Debt":   _pct(debt_value),
            },
            "equity_funds":      equity_funds,
            "hybrid_funds":      hybrid_funds,
            "amc_concentration": amc_concentration,
            "num_funds":         len(customer_df),
        }

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={"metrics": metrics},
            metadata={"num_equity": len(equity_funds), "num_hybrid": len(hybrid_funds)},
        )

    # ── Skill 3: load_qoq_and_benchmarks ──────────────────────────────────────
    def _load_qoq_and_benchmarks(self, params: Dict[str, Any]) -> AgentResponse:
        customer_name = params.get("customer_name") or params.get("selected_customer")
        equity_funds  = params.get("equity_funds", [])
        bucket_name   = params.get("bucket_name",  _DEFAULT_BUCKET)
        parquet_dir   = params.get("parquet_dir",  "data")
        as_of_date    = params.get("as_of_date",   datetime.now())

        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")

        try:
            qoq_data = self.__fetch_qoq_data(customer_name, bucket_name, as_of_date)
        except ValueError as exc:
            return AgentResponse(AgentStatus.RETRY, error=str(exc),
                                 metadata={"customer": customer_name})
        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=f"QoQ fetch failed: {exc}")

        benchmark_error = None
        quarterly_returns, quarter_labels, blended = [], [], {}
        try:
            quarterly_returns, quarter_labels, blended = \
                self.__build_benchmark_df(qoq_data, equity_funds, parquet_dir)
        except Exception as exc:
            benchmark_error = str(exc)

        portfolio_trend = self.__build_portfolio_trend(qoq_data)
        out = {
            "quarterly_returns": quarterly_returns,
            "quarter_labels":    quarter_labels,
            "blended_return":    blended,
            "portfolio_trend":   portfolio_trend,
        }
        meta = {
            "quarters_loaded": list(qoq_data.keys()),
            "return_rows":     len(quarterly_returns),
            "trend_quarters":  len(portfolio_trend),
        }
        if benchmark_error:
            meta["benchmark_error"] = benchmark_error
            return AgentResponse(AgentStatus.RETRY,
                                 error=f"Benchmark build failed: {benchmark_error}",
                                 output=out, metadata=meta)
        return AgentResponse(AgentStatus.SUCCESS, output=out, metadata=meta)

    def __fetch_qoq_data(self, customer_name, bucket_name, as_of_date):
        loader   = PortfolioDataLoader(bucket_name=bucket_name)
        raw_qoq  = loader.load_last_4_quarters(as_of_date, customer=customer_name)
        qoq_data = {k: df for k, df in raw_qoq.items()
                    if isinstance(df, pd.DataFrame) and not df.empty}
        if len(qoq_data) < 2:
            raise ValueError(
                f"Only {len(qoq_data)} non-empty quarter(s) for '{customer_name}' — need 2+.")
        return qoq_data

    def __build_benchmark_df(self, qoq_data, equity_funds, parquet_dir):
        dfs = []
        for qkey, fname in _BENCH_FILES.items():
            path = os.path.join(parquet_dir, fname)
            if not os.path.exists(path):
                continue
            df = pd.read_parquet(path)
            df["index_name"] = df["index_name"].astype(str).str.strip()
            if "year" not in df.columns or "month" not in df.columns:
                year, month = _QUARTER_MONTH_MAP[qkey]
                df["year"] = year; df["month"] = month
            else:
                df["year"]  = pd.to_numeric(df["year"],  errors="coerce").astype("Int64")
                df["month"] = pd.to_numeric(df["month"], errors="coerce").astype("Int64")
            dfs.append(df)
        if not dfs:
            raise FileNotFoundError(f"No Index_Dashboard_*.parquet in: {parquet_dir}")
        benchmark_df  = pd.concat(dfs, ignore_index=True)
        qoq_summary   = build_qoq_data(qoq_data)
        portfolio_data = {
            "equity_funds":      equity_funds, "hybrid_funds": [],
            "quarter_labels":    qoq_summary["quarter_labels"],
            "blended_return":    qoq_summary["blended_return"],
            "quarterly_returns": qoq_summary.get("quarterly_returns", []),
        }
        qr = build_quarterly_returns_with_benchmarks(
            portfolio_data, benchmark_df, _sort_quarter_keys(qoq_data.keys()))
        return qr, qoq_summary["quarter_labels"], qoq_summary["blended_return"]

    def __build_portfolio_trend(self, qoq_data):
        trend = []
        for k in _sort_quarter_keys(qoq_data.keys()):
            df = qoq_data[k].copy()
            df.columns        = [str(c).strip().strip("'\"") for c in df.columns]
            df["TotalInvAmt"] = pd.to_numeric(df["TotalInvAmt"], errors="coerce").fillna(0)
            df["CurValue"]    = pd.to_numeric(df["CurValue"],    errors="coerce").fillna(0)
            df["foliono"]     = df["foliono"].astype(str).str.strip()
            fl = df.groupby("foliono").last().reset_index()
            trend.append({
                "label":    _QUARTER_LABEL_MAP.get(k, k),
                "invested": float(fl["TotalInvAmt"].sum()),
                "current":  float(fl["CurValue"].sum()),
            })
        return trend

    # ── Skill 4: generate_ai_commentary ───────────────────────────────────────
    def _generate_ai_commentary(self, params: Dict[str, Any]) -> AgentResponse:
        portfolio_data = params.get("portfolio_data")
        if not portfolio_data:
            return AgentResponse(AgentStatus.FAILED, error="'portfolio_data' is required")
        try:
            commentary = _generate_ai_commentary(portfolio_data)
            return AgentResponse(AgentStatus.SUCCESS, output={"commentary": commentary})
        except Exception as exc:
            return AgentResponse(AgentStatus.RETRY, error=str(exc),
                                 output={"commentary": []},
                                 metadata={"skippable": True})

    # ── Skill 5: generate_pdf_report ──────────────────────────────────────────
    def _generate_pdf_report(self, params: Dict[str, Any]) -> AgentResponse:
        customer_df       = params.get("customer_df")
        metrics           = params.get("metrics")
        selected_customer = params.get("selected_customer", "Customer")
        company_name      = params.get("company_name", "WinRich Professional Services")
        output_dir        = params.get("output_dir", ".")

        missing = [k for k, v in {"customer_df": customer_df, "metrics": metrics}.items()
                   if v is None]
        if missing:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"Missing required params: {missing}")

        quarterly_returns = params.get("quarterly_returns", [])
        quarter_labels    = params.get("quarter_labels",    [])
        blended_return    = params.get("blended_return",    {})
        portfolio_trend   = params.get("portfolio_trend",   [])
        commentary        = params.get("commentary") or []
        if isinstance(commentary, str):
            commentary = [{"heading": "Performance Commentary", "body": commentary}] if commentary else []

        # ── Force-coerce numeric columns (CSV may load them as strings) ────────
        customer_df = customer_df.copy()
        for col in ("CurValue", "TotalInvAmt", "FolioXIRR", "NatureXIRR", "benchmark_xirr"):
            if col in customer_df.columns:
                customer_df[col] = pd.to_numeric(
                    customer_df[col].astype(str).str.replace(",", "").str.strip(),
                    errors="coerce",
                )

        # ── Section 1: Portfolio Snapshot KPIs ────────────────────────────────
        total_current_value = float(customer_df["CurValue"].fillna(0).sum())
        total_invested      = float(customer_df["TotalInvAmt"].fillna(0).sum())
        total_gain          = total_current_value - total_invested
        # Portfolio XIRR — use value-weighted average of fund XIRRs
        try:
            cur_vals = customer_df["CurValue"].fillna(0)
            xirr_weighted = (
                (customer_df["FolioXIRR"].fillna(0) * cur_vals).sum()
                / cur_vals.sum()
            ) if cur_vals.sum() > 0 else None
            portfolio_xirr = float(xirr_weighted) if xirr_weighted is not None else None
        except Exception:
            portfolio_xirr = None

        # ── Section 1: Allocation rows ─────────────────────────────────────────
        _known_natures = {"Equity", "Balance", "Debt"}
        _other_df      = customer_df[~customer_df["Nature"].isin(_known_natures)]
        alloc          = metrics["allocation"]
        allocation_rows = []
        for nature_key, display_name in [
            ("Equity",  "Equity"),
            ("Balance", "Hybrid"),
            (None,      "Other"),   # None → use _other_df
            ("Debt",    "Debt"),
        ]:
            subset     = _other_df if nature_key is None else customer_df[customer_df["Nature"] == nature_key]
            fund_names = " | ".join(subset["s_name"].tolist()) if not subset.empty else "—"
            pct        = alloc.get(display_name, 0.0)
            if subset.empty and display_name != "Debt":
                continue
            allocation_rows.append({
                "asset_class":        display_name,
                "your_allocation":    f"{pct:.2f}%",
                "funds_in_portfolio": fund_names,
            })

        # ── Section 2: all_funds (Fund Performance vs Benchmark) ──────────────
        # Resolve as_of_date for age-based benchmark suppression
        _as_of = params.get("as_of_date", datetime.now())
        if isinstance(_as_of, str):
            try:
                _as_of = datetime.strptime(_as_of, "%Y-%m-%d")
            except Exception:
                _as_of = datetime.now()

        all_funds = []
        for _, row in customer_df.iterrows():
            bench_idx = ""
            if "benchmark_index" in customer_df.columns:
                raw_bi = row["benchmark_index"]
                bench_idx = str(raw_bi).strip() if pd.notna(raw_bi) else ""

            def _bench_col(col):
                if col in customer_df.columns:
                    v = row[col]
                    if pd.notna(v):
                        try:
                            f = float(v)
                            return f if f != 0.0 else None
                        except (TypeError, ValueError):
                            pass
                return None

            # Folio age in days
            folio_age_days = None
            raw_date = row.get("FolioStartDate") or row.get("folio_start_date") or ""
            if raw_date:
                try:
                    parsed = pd.to_datetime(str(raw_date), errors="coerce")
                    if not pd.isnull(parsed):
                        folio_age_days = (_as_of - parsed.to_pydatetime().replace(tzinfo=None)).days
                except Exception:
                    pass

            # Suppress benchmark returns if folio is too young for that horizon
            b3m  = _bench_col("benchmark_return_3m")  if (folio_age_days is None or folio_age_days >= 90)   else None
            b1yr = _bench_col("benchmark_return_1yr") if (folio_age_days is None or folio_age_days >= 365)  else None
            b5yr = _bench_col("benchmark_return_5yr") if (folio_age_days is None or folio_age_days >= 1825) else None

            folio_xirr = None
            if "FolioXIRR" in customer_df.columns and pd.notna(row["FolioXIRR"]):
                try:
                    folio_xirr = float(row["FolioXIRR"])
                except (TypeError, ValueError):
                    pass

            winrich_rank = "N/A"
            if "winrich_rank" in customer_df.columns and pd.notna(row["winrich_rank"]):
                winrich_rank = str(row["winrich_rank"])

            all_funds.append({
                "name":                  row["s_name"],
                "benchmark_index":       bench_idx or "—",
                "winrich_rank":          winrich_rank,
                "xirr":                  folio_xirr,
                "benchmark_return_3m":   b3m,
                "benchmark_return_1yr":  b1yr,
                "benchmark_return_5yr":  b5yr,
            })

        # ── Section 2a: fund_gains (Fund-wise Gains table) ────────────────────
        fund_gains = []
        for _, row in customer_df.iterrows():
            inv  = float(row.get("TotalInvAmt") or 0)
            cur  = float(row.get("CurValue")    or 0)
            gain = cur - inv
            abs_return = (gain / inv * 100) if inv > 0 else 0
            raw_date = row.get("FolioStartDate") or row.get("folio_start_date") or ""
            try:
                parsed_date = pd.to_datetime(str(raw_date), errors="coerce")
                folio_start_fmt = parsed_date.strftime("%d-%b-%Y") if not pd.isnull(parsed_date) else "—"
            except Exception:
                folio_start_fmt = str(raw_date).split("T")[0] if raw_date else "—"
            fund_gains.append({
                "name":            row["s_name"],
                "folio_start_date": folio_start_fmt,
                "amount_invested":  inv,
                "current_value":    cur,
                "gain":             gain,
                "abs_return":       abs_return,
                "xirr":             float(row["FolioXIRR"]) if pd.notna(row.get("FolioXIRR")) else None,
            })

        # ── Assemble portfolio_data using the exact keys the PDF generator reads ─
        _warnings = [w for cond, w in [
            (not quarterly_returns, "quarterly_returns is empty"),
            (not quarter_labels,    "quarter_labels is empty"),
            (not portfolio_trend,   "portfolio_trend is empty"),
        ] if cond]

        # Derive investment_start from earliest FolioStartDate across all funds
        try:
            start_dates = pd.to_datetime(customer_df["FolioStartDate"], errors="coerce").dropna()
            investment_start = start_dates.min().strftime("%B %d, %Y") if not start_dates.empty else ""
        except Exception:
            investment_start = ""

        # Derive data_as_on from the resolved GCS path date (YYYY/MM/DD in path)
        # e.g. gs://winrich/Datawarehouse/MutualFunds/2026/03/08/mutualfunds.csv → 08-Mar-2026
        resolved_path = params.get("resolved_path", "")
        data_as_on = ""
        if resolved_path:
            import re as _re
            m = _re.search(r"/(\d{4})/(\d{2})/(\d{2})/", resolved_path)
            if m:
                try:
                    data_as_on = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%d-%b-%Y")
                except Exception:
                    pass
        if not data_as_on:
            as_of_date = params.get("as_of_date", datetime.now())
            if isinstance(as_of_date, str):
                try:
                    as_of_date = datetime.strptime(as_of_date, "%Y-%m-%d")
                except Exception:
                    as_of_date = datetime.now()
            data_as_on = as_of_date.strftime("%d-%b-%Y")

        portfolio_data = {
            # Header
            "company_name":       company_name,
            "client_name":        selected_customer,
            "report_date":        datetime.now().strftime("%B %d, %Y"),
            "investment_start":   investment_start,
            "prepared_by":        params.get("prepared_by", "WinRich Research Desk"),
            "data_as_on":         data_as_on,
            "risk_profile":       params.get("risk_profile", ""),
            "logo_path":          params.get("logo_path", ""),
            "website":            params.get("website", "www.winrich.in"),
            "email":              params.get("email", "support@winrich.in"),
            "n_funds":            len(customer_df),
            "n_amcs":             len(metrics["amc_concentration"]),

            # Section 1 — Portfolio Snapshot
            "total_current_value": total_current_value,
            "total_invested":      total_invested,
            "total_gain":          total_gain,
            "portfolio_xirr":      portfolio_xirr,
            "allocation_rows":     allocation_rows,

            # Section 2 — Fund Performance vs Benchmark
            "all_funds":           all_funds,

            # Section 2a — Fund-wise Gains
            "fund_gains":          fund_gains,

            # Section 3 — AMC Concentration
            "amc_concentration":   metrics["amc_concentration"],

            # Sections 4 & 5 — QoQ
            "quarter_labels":      quarter_labels,
            "quarterly_returns":   quarterly_returns,
            "blended_return":      blended_return,

            # Commentary
            "commentary":          commentary or None,
        }

        filename    = (f"{datetime.now().strftime('%Y%m%d')}__{selected_customer.replace(' ', '')}"
                       "_portfolio_report.pdf")
        output_dir  = os.path.normpath(os.path.abspath(output_dir or "."))
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        try:
            gen         = MFPortfolioPDFGenerator(company_name)
            output_file = gen.generate_report(portfolio_data, output_path)
        except Exception as exc:
            import traceback
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"PDF generation failed: {exc}",
                                 metadata={"traceback": traceback.format_exc(),
                                           "portfolio_data_keys": list(portfolio_data.keys())})

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={"pdf_path": output_file, "filename": filename},
            metadata={"client": selected_customer, "company": company_name,
                      "data_warnings": _warnings, "qoq_rows": len(quarterly_returns),
                      "trend_quarters": len(portfolio_trend),
                      "n_funds": len(customer_df), "n_amcs": len(metrics["amc_concentration"])},
        )

    # ── Skill 6: generate_quarterly_report (orchestrator) ─────────────────────
    def _generate_quarterly_report(self, params: Dict[str, Any]) -> AgentResponse:
        """
        End-to-end orchestrator. Customer data is fetched via WinrichMFDataAgent.

        Required params: customer_name : str
        Optional params: bucket_name, max_lookback_days, parquet_dir,
                         as_of_date, model_allocation, company_name,
                         output_dir, skip_commentary
        """
        customer_name = params.get("customer_name") or params.get("selected_customer")
        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")

        base = {
            "bucket_name":       params.get("bucket_name",       _DEFAULT_BUCKET),
            "max_lookback_days": params.get("max_lookback_days", 10),
            "parquet_dir":       params.get("parquet_dir",       "data"),
            "as_of_date":        params.get("as_of_date",        datetime.now()),
            "company_name":      params.get("company_name",      "WinRich Professional Services"),
            "output_dir":        params.get("output_dir",        "."),
        }

        # Step 1 — portfolio data via WinrichMFDataAgent
        r1 = self.run("load_portfolio_data", {**base, "customer_name": customer_name})
        if r1.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[load_portfolio_data] {r1.error}",
                                 metadata=r1.metadata)

        customer_df       = r1.output["customer_df"]
        selected_customer = r1.output["selected_customer"]
        resolved_path     = r1.output.get("resolved_path", "")

        # Step 2 — metrics
        r2 = self.run("calculate_metrics", {"customer_df": customer_df})
        if r2.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[calculate_metrics] {r2.error}")
        metrics = r2.output["metrics"]

        # Step 3 — QoQ + benchmarks + trend
        r3 = self.run("load_qoq_and_benchmarks", {
            **base, "customer_name": selected_customer,
            "equity_funds": metrics["equity_funds"],
        })
        if r3.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[load_qoq_and_benchmarks] {r3.error}",
                                 metadata=r3.metadata)

        qoq_warning = r3.error if r3.status == AgentStatus.RETRY else None

        # Step 4 — AI commentary (non-fatal)
        commentary         = []
        commentary_warning = None
        if not params.get("skip_commentary", False):
            r4 = self.run("generate_ai_commentary", {
                "portfolio_data": {
                    "client_name":       selected_customer,
                    "equity_funds":      metrics["equity_funds"],
                    "hybrid_funds":      metrics["hybrid_funds"],
                    "amc_concentration": metrics["amc_concentration"],
                    "quarterly_returns": r3.output.get("quarterly_returns", []),
                    "blended_return":    r3.output.get("blended_return",    {}),
                    "portfolio_trend":   r3.output.get("portfolio_trend",   []),
                    "client_allocation": metrics["allocation"],
                }
            })
            if r4.status == AgentStatus.SUCCESS:
                commentary = r4.output.get("commentary", [])
            else:
                commentary_warning = r4.error

        # Step 5 — render PDF
        r5 = self.run("generate_pdf_report", {
            **base,
            "customer_df":       customer_df,
            "metrics":           metrics,
            "selected_customer": selected_customer,
            "resolved_path":     resolved_path,
            "quarterly_returns": r3.output.get("quarterly_returns", []),
            "quarter_labels":    r3.output.get("quarter_labels",    []),
            "blended_return":    r3.output.get("blended_return",    {}),
            "portfolio_trend":   r3.output.get("portfolio_trend",   []),
            "commentary":        commentary,
            "model_allocation":  params.get("model_allocation", {}),
            "risk_profile":      params.get("risk_profile", ""),
            "logo_path":         params.get("logo_path", ""),
        })

        if r5.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[generate_pdf_report] {r5.error}",
                                 metadata=r5.metadata)

        warnings = [w for w in [qoq_warning, commentary_warning] if w]
        return AgentResponse(
            AgentStatus.SUCCESS,
            output={"pdf_path": r5.output["pdf_path"], "filename": r5.output["filename"]},
            metadata={
                **r5.metadata,
                "steps_completed": [
                    "load_portfolio_data (→ WinrichMFDataAgent)",
                    "calculate_metrics",
                    "load_qoq_and_benchmarks",
                    *([] if params.get("skip_commentary") else ["generate_ai_commentary"]),
                    "generate_pdf_report",
                ],
                "warnings": warnings,
            },
        )