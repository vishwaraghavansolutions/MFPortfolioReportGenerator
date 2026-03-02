# agents/mf_portfolio_agent.py
"""
MF Portfolio Agent
==================
Encapsulates every step of the mutual-fund portfolio report pipeline as
discrete, testable Agent skills that conform to the Agent/AgentResponse/
AgentStatus contract defined in agents/base.py.

Skills (public)
---------------
  1. list_customers          – List all unique customer names from CSV
  2. load_portfolio_data     – Load CSV and filter to one customer's holdings
  3. calculate_metrics       – Compute allocation %, fund lists, AMC concentration
  4. load_qoq_and_benchmarks – Fetch GCS QoQ data + benchmark returns + trend (combined)
  5. generate_ai_commentary  – Call Claude to write narrative commentary
  6. generate_pdf_report     – Assemble all data and render the PDF

Internal helpers (private, not in skill map)
---------------------------------------------
  __fetch_qoq_data           – GCS fetch, returns dict[str, DataFrame]
  __build_benchmark_df       – Load index parquets, build quarterly_returns list
  __build_portfolio_trend    – Build [{label, invested, current}, ...] per quarter

Data contract (portfolio_data keys passed to MFPortfolioPDFGenerator)
----------------------------------------------------------------------
  amc_concentration  : dict  {amc_name: fund_count}          ← .values() called
  blended_return     : dict  {q0..qN, ttm}                   ← .get("q0") called
  quarterly_returns  : list[dict]  name, is_benchmark,
                         returns: {q0..qN, ttm}
  commentary         : list[dict]  {heading, body}            ← from generate_ai_commentary
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List

import pandas as pd

from agents.base import Agent, AgentResponse, AgentStatus

# ── External project utilities ────────────────────────────────────────────────
from utils.mf_portfolio_pdf_generator import (
    MFPortfolioPDFGenerator,
    generate_ai_commentary as _generate_ai_commentary,
)
from utils.pdf_utils import format_currency_indian
from utils.Indices_lookup import SchemeLookup
from utils.customer_portfolio import get_customer_portfolio
from utils.mf_qoq_loader import PortfolioDataLoader
from utils.build_qoq_data import build_qoq_data
from utils.benchmark_utils import (
    build_quarterly_returns_with_benchmarks,
    _QUARTER_MONTH_MAP,
)


# ── Module-level constants ────────────────────────────────────────────────────

_QUARTER_LABEL_MAP: Dict[str, str] = {
    "Q3_2023":  "Q1 FY23 (Jan-Mar '23)",
    "Q6_2023":  "Q2 FY23 (Apr-Jun '23)",
    "Q9_2023":  "Q3 FY23 (Jul-Sep '23)",
    "Q12_2023": "Q4 FY23 (Oct-Dec '23)",
    "Q3_2024":  "Q1 FY24 (Jan-Mar '24)",
    "Q6_2024":  "Q2 FY24 (Apr-Jun '24)",
    "Q9_2024":  "Q3 FY24 (Jul-Sep '24)",
    "Q12_2024": "Q4 FY24 (Oct-Dec '24)",
    "Q3_2025":  "Q1 FY25 (Jan-Mar '25)",
    "Q6_2025":  "Q2 FY25 (Apr-Jun '25)",
    "Q9_2025":  "Q3 FY25 (Jul-Sep '25)",
    "Q12_2025": "Q4 FY25 (Oct-Dec '25)",
    "Q3_2026":  "Q1 FY26 (Jan-Mar '26)",
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

# Loaded once at module import so it is available to all skill calls
_SCHEME_DF: pd.DataFrame = pd.read_csv("data/SchemeData2301262313SS.csv")
_SCHEME_DF.columns = _SCHEME_DF.columns.str.strip()


# ── Private helpers (pure functions, no side-effects) ─────────────────────────

def _clean_fund_name(fund_name: str) -> str:
    """Strip plan-type suffixes from a fund name."""
    for pattern in (
        r"\s*-\s*Regular.*$",
        r"\s*-\s*Direct.*$",
        r"\s*-\s*Growth.*$",
        r"\s*-\s*IDCW.*$",
        r"\s*-\s*Dividend.*$",
        r"\s*\(.*\)$",
    ):
        fund_name = re.sub(pattern, "", fund_name, flags=re.IGNORECASE)
    return fund_name.strip()


def _get_amc(fund_name: str) -> str:
    """Return a cleaned AMC name for *fund_name*, or 'Unknown'."""
    cleaned = _clean_fund_name(fund_name)
    match = _SCHEME_DF[
        _SCHEME_DF["Scheme Name"].str.contains(cleaned, case=False, na=False, regex=False)
    ]
    if match.empty:
        return "Unknown"
    amc = match.iloc[0]["AMC"]
    return amc.replace(" Limited", "").replace(" Ltd", "").replace(" Pvt.", "").strip()


def _sort_quarter_keys(keys) -> list:
    """Sort quarter keys chronologically (e.g. Q3_2024 < Q6_2024 < Q3_2025)."""
    def _rank(k: str) -> int:
        parts = k.split("_")
        month = int(parts[0].replace("Q", ""))
        year  = int(parts[1])
        return year * 100 + month
    return sorted(keys, key=_rank)


def _mask_phone(phone: str) -> str:
    digits = "".join(filter(str.isdigit, str(phone)))
    return "*" * (len(digits) - 4) + digits[-4:]


def _mask_email(email: str) -> str:
    user, domain = email.split("@")
    return user[0] + "*" * (len(user) - 1) + "@" + domain


# ═════════════════════════════════════════════════════════════════════════════
class MFPortfolioAgent(Agent):
    """
    Agent that turns a mutual-fund data warehouse CSV into a full PDF report.

    All mutable state lives in `params` dicts that flow between skills — the
    agent itself is stateless and safe to instantiate once and reuse.
    """

    name = "MFPortfolioAgent"

    # ── Skill map ─────────────────────────────────────────────────────────────
    
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "list_customers":            self._list_customers,
            "load_portfolio_data":       self._load_portfolio_data,
            "calculate_metrics":         self._calculate_metrics,
            "load_qoq_and_benchmarks":   self._load_qoq_and_benchmarks,
            "generate_ai_commentary":    self._generate_ai_commentary,
            "generate_pdf_report":       self._generate_pdf_report,
            "generate_quarterly_report": self._generate_quarterly_report,  # ← NEW
        }   
    
    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 0 — list_customers   (lightweight — never filters or enriches)
    # ──────────────────────────────────────────────────────────────────────────
    def _list_customers(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Read only the c_name column from the CSV to return the sorted customer list.
        No SchemeLookup, no get_customer_portfolio — fast sidebar population.

        Required params
        ---------------
        csv_path : str   – path to the data-warehouse CSV

        Output keys
        -----------
        all_customers : list[str]
        total_records : int
        """
        csv_path = params.get(
            "csv_path",
            "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv",
        )
        try:
            # Read only the customer column — much faster on large files
            df = pd.read_csv(csv_path, usecols=["c_name"])
        except FileNotFoundError:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"CSV not found at: {csv_path}",
            )
        except ValueError:
            # usecols fails if column doesn't exist — fall back to full read
            try:
                df = pd.read_csv(csv_path)[["c_name"]]
            except Exception as exc:
                return AgentResponse(AgentStatus.FAILED, error=str(exc))

        all_customers = sorted(df["c_name"].dropna().unique().tolist())
        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "all_customers": all_customers,
                "total_records": len(df),
            },
            metadata={"customer_count": len(all_customers)},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 1 — load_portfolio_data
    # ──────────────────────────────────────────────────────────────────────────
    def _load_portfolio_data(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Load the master CSV and filter to a single customer.

        Required params
        ---------------
        csv_path        : str   – path to the data-warehouse CSV
        customer_name   : str   – exact customer name (c_name column)

        Output keys
        -----------
        customer_df     : pd.DataFrame   – filtered, enriched holdings frame
        all_customers   : list[str]      – sorted list of all customer names
        selected_customer: str
        """
        csv_path      = params.get("csv_path", "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv")
        customer_name = params.get("customer_name")

        if not customer_name:
            return AgentResponse(
                AgentStatus.FAILED,
                error="'customer_name' is required in params",
            )

        try:
            df = pd.read_csv(csv_path)
        except FileNotFoundError:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"CSV not found at: {csv_path}",
            )

        all_customers = sorted(df["c_name"].unique().tolist())

        if customer_name not in all_customers:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"Customer '{customer_name}' not found in CSV",
                metadata={"available_count": len(all_customers)},
            )

        lookup = SchemeLookup()
        customer_df = get_customer_portfolio(df, customer_name, lookup=lookup)

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "customer_df":        customer_df,
                "all_customers":      all_customers,
                "selected_customer":  customer_name,
            },
            metadata={"records": len(customer_df)},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 2 — calculate_metrics
    # ──────────────────────────────────────────────────────────────────────────
    def _calculate_metrics(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Derive allocation %, fund performance lists and AMC concentration.

        Required params
        ---------------
        customer_df : pd.DataFrame   – output of load_portfolio_data

        Output keys
        -----------
        metrics : dict with keys:
            total_value, allocation, equity_funds, hybrid_funds,
            amc_concentration, num_funds
        """
        customer_df: pd.DataFrame = params.get("customer_df")
        if customer_df is None:
            return AgentResponse(AgentStatus.FAILED, error="'customer_df' is required")

        total_value   = customer_df["CurValue"].sum()
        equity_value  = customer_df[customer_df["Nature"] == "Equity"]["CurValue"].sum()
        balance_value = customer_df[customer_df["Nature"] == "Balance"]["CurValue"].sum()
        debt_value    = customer_df[customer_df["Nature"] == "Debt"]["CurValue"].sum()

        def _pct(v):
            return (v / total_value * 100) if total_value > 0 else 0.0

        allocation = {
            "Equity": _pct(equity_value),
            "Hybrid": _pct(balance_value),
            "Debt":   _pct(debt_value),
        }

        equity_funds = [
            {
                "name":                  row["s_name"],
                "xirr":                  row["FolioXIRR"],
                "benchmark":             row["NatureXIRR"],
                "benchmark_index":       row.get("benchmark_index", 0),
                "benchmark_return_1m":   row.get("benchmark_return_1m", 0),
                "benchmark_return_3m":   row.get("benchmark_return_3m", 0),
                "benchmark_return_1yr":  row.get("benchmark_return_1yr", 0),
                "benchmark_return_3yr":  row.get("benchmark_return_3yr", 0),
                "benchmark_return_5yr":  row.get("benchmark_return_5yr", 0),
            }
            for _, row in customer_df[customer_df["Nature"] == "Equity"].iterrows()
        ]

        hybrid_funds = [
            {"name": row["s_name"], "xirr": row["FolioXIRR"]}
            for _, row in customer_df[customer_df["Nature"] == "Balance"].iterrows()
        ]

        amc_concentration: Dict[str, int] = {}
        for _, row in customer_df.iterrows():
            amc = _get_amc(row["s_name"])
            if amc != "Unknown":
                amc_concentration[amc] = amc_concentration.get(amc, 0) + 1

        metrics = {
            "total_value":        total_value,
            "allocation":         allocation,
            "equity_funds":       equity_funds,
            "hybrid_funds":       hybrid_funds,
            "amc_concentration":  amc_concentration,
            "num_funds":          len(customer_df),
        }

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={"metrics": metrics},
            metadata={"num_equity": len(equity_funds), "num_hybrid": len(hybrid_funds)},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 3 — load_qoq_and_benchmarks   (public)
    # Internally calls three private helpers in sequence:
    #   _fetch_qoq_data  →  _build_benchmark_df  →  _build_portfolio_trend
    # ──────────────────────────────────────────────────────────────────────────
    def _load_qoq_and_benchmarks(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Single skill that fetches QoQ quarter data from GCS, loads benchmark
        index parquets, merges fund vs benchmark returns, and builds the
        portfolio growth trend — returning everything the PDF needs in one call.

        Required params
        ---------------
        customer_name : str
        equity_funds  : list[dict]   – from calculate_metrics output
        bucket_name   : str          – GCS bucket (default "winrich")
        parquet_dir   : str          – local dir with index_dashboard parquets (default "data")
        as_of_date    : datetime     – (default now)

        Output keys
        -----------
        quarterly_returns : list[dict]
        quarter_labels    : list[str]
        blended_return    : float
        portfolio_trend   : list[dict]  – [{label, invested, current}, ...]
        """
        customer_name = params.get("customer_name") or params.get("selected_customer")
        equity_funds  = params.get("equity_funds", [])
        bucket_name   = params.get("bucket_name", "winrich")
        parquet_dir   = params.get("parquet_dir", "data")
        as_of_date    = params.get("as_of_date", datetime.now())

        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")

        # ── Step A: fetch raw QoQ frames from GCS ─────────────────────────────
        try:
            qoq_data = self.__fetch_qoq_data(customer_name, bucket_name, as_of_date)
        except ValueError as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=str(exc),
                metadata={"customer": customer_name},
            )
        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=f"QoQ fetch failed: {exc}")

        # ── Step B: load benchmark parquets & build quarterly return rows ──────
        benchmark_error = None
        quarterly_returns, quarter_labels, blended_return = [], [], 0.0
        try:
            quarterly_returns, quarter_labels, blended_return = \
                self.__build_benchmark_df(qoq_data, equity_funds, parquet_dir)
        except Exception as exc:
            benchmark_error = str(exc)
            # Don't return FAILED — still build the trend and surface the error in metadata

        # ── Step C: build portfolio growth trend (independent of benchmarks) ───
        portfolio_trend = self.__build_portfolio_trend(qoq_data)

        if benchmark_error:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"Benchmark build failed: {benchmark_error}",
                output={
                    "quarterly_returns": quarterly_returns,
                    "quarter_labels":    quarter_labels,
                    "blended_return":    blended_return,
                    "portfolio_trend":   portfolio_trend,
                },
                metadata={
                    "quarters_loaded":  list(qoq_data.keys()),
                    "return_rows":      0,
                    "trend_quarters":   len(portfolio_trend),
                    "benchmark_error":  benchmark_error,
                },
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "quarterly_returns": quarterly_returns,
                "quarter_labels":    quarter_labels,
                "blended_return":    blended_return,
                "portfolio_trend":   portfolio_trend,
            },
            metadata={
                "quarters_loaded": list(qoq_data.keys()),
                "return_rows":     len(quarterly_returns),
                "trend_quarters":  len(portfolio_trend),
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Private helper A — fetch QoQ frames from GCS
    # ──────────────────────────────────────────────────────────────────────────
    def __fetch_qoq_data(
        self,
        customer_name: str,
        bucket_name: str,
        as_of_date: datetime,
    ) -> Dict[str, pd.DataFrame]:
        """
        Load last-4-quarter parquet frames from GCS and return only non-empty ones.
        Raises ValueError if fewer than 2 quarters are available.
        """
        loader  = PortfolioDataLoader(bucket_name=bucket_name)
        raw_qoq = loader.load_last_4_quarters(as_of_date, customer=customer_name)

        qoq_data = {
            k: df for k, df in raw_qoq.items()
            if isinstance(df, pd.DataFrame) and not df.empty
        }

        if len(qoq_data) < 2:
            raise ValueError(
                f"Only {len(qoq_data)} non-empty quarter(s) found for "
                f"'{customer_name}' — need at least 2"
            )
        return qoq_data

    # ──────────────────────────────────────────────────────────────────────────
    # Private helper B — load index parquets & build fund vs benchmark rows
    # ──────────────────────────────────────────────────────────────────────────
    def __build_benchmark_df(
        self,
        qoq_data: Dict[str, pd.DataFrame],
        equity_funds: List[dict],
        parquet_dir: str,
    ):
        """
        Returns (quarterly_returns, quarter_labels, blended_return_float).
        Raises FileNotFoundError if no parquet files are found.
        """
        dfs = []
        for qkey, filename in _BENCH_FILES.items():
            path = os.path.join(parquet_dir, filename)
            if not os.path.exists(path):
                continue
            df = pd.read_parquet(path)
            df["index_name"] = df["index_name"].astype(str).str.strip()
            if "year" not in df.columns or "month" not in df.columns:
                year, month = _QUARTER_MONTH_MAP[qkey]
                df["year"]  = year
                df["month"] = month
            else:
                df["year"]  = pd.to_numeric(df["year"],  errors="coerce").astype("Int64")
                df["month"] = pd.to_numeric(df["month"], errors="coerce").astype("Int64")
            dfs.append(df)

        if not dfs:
            raise FileNotFoundError(
                f"No index_dashboard parquet files found in: {parquet_dir}"
            )

        benchmark_df = pd.concat(dfs, ignore_index=True)
        qoq_summary  = build_qoq_data(qoq_data)

        # build_quarterly_returns_with_benchmarks iterates portfolio_data['quarterly_returns']
        # (the fund rows produced by build_qoq_data) and interleaves benchmark rows.
        # We must pass those fund rows through — an empty list produces 0 output rows.
        portfolio_data = {
            "equity_funds":      equity_funds,
            "hybrid_funds":      [],   # benchmark lookup also checks hybrid_funds
            "quarter_labels":    qoq_summary["quarter_labels"],
            "blended_return":    qoq_summary["blended_return"],
            "quarterly_returns": qoq_summary.get("quarterly_returns", []),
        }

        quarter_keys      = _sort_quarter_keys(qoq_data.keys())
        quarterly_returns = build_quarterly_returns_with_benchmarks(
            portfolio_data, benchmark_df, quarter_keys,
        )

        # blended_return must stay as dict {q0..qN, ttm} — the generator calls
        # blended_return.get("q0"), blended_return.get("ttm") etc.
        blended_return = qoq_summary["blended_return"]
        return quarterly_returns, qoq_summary["quarter_labels"], blended_return

    # ──────────────────────────────────────────────────────────────────────────
    # Private helper C — build portfolio growth trend
    # ──────────────────────────────────────────────────────────────────────────
    def __build_portfolio_trend(
        self,
        qoq_data: Dict[str, pd.DataFrame],
    ) -> List[dict]:
        """
        Returns [{label, invested, current}, ...] sorted oldest → newest quarter.
        Uses last record per folio within each quarter to avoid double-counting.
        """
        trend = []
        for k in _sort_quarter_keys(qoq_data.keys()):
            df = qoq_data[k].copy()
            df.columns        = [str(c).strip().strip("'\"") for c in df.columns]
            df["TotalInvAmt"] = pd.to_numeric(df["TotalInvAmt"], errors="coerce").fillna(0)
            df["CurValue"]    = pd.to_numeric(df["CurValue"],    errors="coerce").fillna(0)
            df["foliono"]     = df["foliono"].astype(str).str.strip()
            folio_latest      = df.groupby("foliono").last().reset_index()
            trend.append({
                "label":    _QUARTER_LABEL_MAP.get(k, k),
                "invested": float(folio_latest["TotalInvAmt"].sum()),
                "current":  float(folio_latest["CurValue"].sum()),
            })
        return trend

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 6 — generate_ai_commentary
    # ──────────────────────────────────────────────────────────────────────────
    def _generate_ai_commentary(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Call the AI commentary generator with assembled portfolio_data.

        Required params
        ---------------
        portfolio_data : dict   – fully assembled portfolio data dict

        Output keys
        -----------
        commentary : str
        """
        portfolio_data = params.get("portfolio_data")
        if not portfolio_data:
            return AgentResponse(AgentStatus.FAILED, error="'portfolio_data' is required")

        try:
            commentary = _generate_ai_commentary(portfolio_data)
            return AgentResponse(
                AgentStatus.SUCCESS,
                output={"commentary": commentary},
            )
        except Exception as exc:
            # Commentary is non-critical — return RETRY so orchestrator can
            # decide whether to skip or retry rather than failing the whole run
            return AgentResponse(
                AgentStatus.RETRY,
                error=str(exc),
                output={"commentary": ""},
                metadata={"skippable": True},
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 6 — generate_pdf_report
    # ──────────────────────────────────────────────────────────────────────────
    def _generate_pdf_report(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Assemble the full portfolio_data dict and call MFPortfolioPDFGenerator.

        Data contract aligns exactly with MFPortfolioPDFGenerator.generate_report():
          amc_concentration  → dict {amc_name: fund_count}   (generator calls .values())
          blended_return     → dict {q0..qN, ttm}            (generator calls .get("q0"))
          quarterly_returns  → list[dict] name, is_benchmark,
                                 returns: {q0..qN, ttm}
          commentary         → list[dict] {heading, body}    (from generate_ai_commentary)

        Required params
        ---------------
        customer_df       : pd.DataFrame
        metrics           : dict           — output of calculate_metrics
        quarterly_returns : list[dict]     — output of load_qoq_and_benchmarks
        quarter_labels    : list[str]      — output of load_qoq_and_benchmarks
        blended_return    : dict           — output of load_qoq_and_benchmarks
        portfolio_trend   : list[dict]     — output of load_qoq_and_benchmarks
        commentary        : list[dict]     — output of generate_ai_commentary (optional)
        model_allocation  : dict           — {Equity: %, Hybrid: %, Debt: %}
        selected_customer : str
        company_name      : str            (default "Winrich Professional Services")
        output_dir        : str            (default ".")

        Output keys
        -----------
        pdf_path       : str
        filename       : str
        """
        customer_df       = params.get("customer_df")
        metrics           = params.get("metrics")
        selected_customer = params.get("selected_customer", "Customer")
        company_name      = params.get("company_name", "Winrich Professional Services")
        output_dir        = params.get("output_dir", ".")
        model_allocation  = params.get("model_allocation", {
            "Equity": metrics["allocation"]["Equity"] if metrics else 0,
            "Hybrid": metrics["allocation"]["Hybrid"] if metrics else 0,
            "Debt":   metrics["allocation"]["Debt"]   if metrics else 0,
        })

        missing = [k for k, v in {"customer_df": customer_df, "metrics": metrics}.items()
                   if v is None]
        if missing:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"Missing required params: {missing}",
            )

        # ── PII masking ───────────────────────────────────────────────────────
        first_row = customer_df.iloc[0]
        try:
            email  = _mask_email(str(first_row.get("Email",  "")).strip())
            mobile = _mask_phone(str(first_row.get("Mobile", "")))
        except Exception:
            email  = "***@***.com"
            mobile = "****"

        # ── QoQ / benchmark data ──────────────────────────────────────────────
        quarterly_returns = params.get("quarterly_returns", [])
        quarter_labels    = params.get("quarter_labels",    [])
        blended_return    = params.get("blended_return",    {})   # dict {q0..qN, ttm}
        portfolio_trend   = params.get("portfolio_trend",   [])

        # commentary must be list[dict] {heading, body} or absent
        commentary = params.get("commentary") or []
        if isinstance(commentary, str):
            # Tolerate a raw string being passed — wrap it as a single block
            commentary = [{"heading": "Performance Commentary", "body": commentary}] if commentary else []

        _warnings = []
        if not quarterly_returns:
            _warnings.append("quarterly_returns is empty — QoQ table will be blank")
        if not quarter_labels:
            _warnings.append("quarter_labels is empty")
        if not portfolio_trend:
            _warnings.append("portfolio_trend is empty — growth chart will be blank")

        # ── Assemble portfolio_data ───────────────────────────────────────────
        # Keys and types match MFPortfolioPDFGenerator exactly — see module docstring.
        portfolio_data: Dict[str, Any] = {
            "company_name":  company_name,
            "client_name":   selected_customer,
            "report_date":   datetime.now().strftime("%B %d, %Y"),
            "prepared_by":   company_name,
            "summary": {
                "Client Name":           selected_customer,
                "Email":                 email,
                "Mobile":                mobile,
                "Report Date":           datetime.now().strftime("%B %d, %Y"),
                "Total Portfolio Value": format_currency_indian(metrics["total_value"]),
                "Total Funds":           str(metrics["num_funds"]),
                "Equity Allocation":     f"{metrics['allocation']['Equity']:.2f}%",
                "Hybrid Allocation":     f"{metrics['allocation']['Hybrid']:.2f}%",
                "Debt Allocation":       f"{metrics['allocation']['Debt']:.2f}%",
                "Number of AMCs":        str(len(metrics["amc_concentration"])),
            },
            "client_allocation": {
                "Equity": metrics["allocation"]["Equity"],
                "Hybrid": metrics["allocation"]["Hybrid"],
                "Debt":   metrics["allocation"]["Debt"],
            },
            "model_allocation":  model_allocation,
            "equity_funds":      metrics["equity_funds"],
            "hybrid_funds":      metrics["hybrid_funds"],
            # dict {amc_name: fund_count} — generator calls .values() / .items()
            "amc_concentration": metrics["amc_concentration"],
            # QoQ — generator handles Sections 6 & 7 natively
            "portfolio_trend":   portfolio_trend,
            "quarter_labels":    quarter_labels,
            # list[dict] with returns: {q0..qN, ttm} — see data contract above
            "quarterly_returns": quarterly_returns,
            # dict {q0..qN, ttm} — generator calls .get("q0"), .get("ttm")
            "blended_return":    blended_return,
            # list[dict] {heading, body} — rendered as commentary section
            "commentary":        commentary or None,
        }

        # ── Generate PDF ──────────────────────────────────────────────────────
        filename = (f"{datetime.now().strftime('%Y%m%d')}__{selected_customer.replace(' ', '')}_portfolio_report.pdf")
        # ── Normalise output_dir to an absolute, OS-native path ──────────────────
        output_dir  = os.path.normpath(os.path.abspath(output_dir or "."))
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        try:
            generator   = MFPortfolioPDFGenerator(company_name)
            output_file = generator.generate_report(portfolio_data, output_path)
        except Exception as exc:
            import traceback
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"PDF generation failed: {exc}",
                metadata={
                    "traceback":           traceback.format_exc(),
                    "portfolio_data_keys": list(portfolio_data.keys()),
                },
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "pdf_path": output_file,
                "filename": filename,
            },
            metadata={
                "client":         selected_customer,
                "company":        company_name,
                "data_warnings":  _warnings,
                "qoq_rows":       len(quarterly_returns),
                "trend_quarters": len(portfolio_trend),
            },
        )

    # ──────────────────────────────────────────────────────────────────────────────
    # Skill 7 — generate_quarterly_report   (orchestrator skill)
    # ──────────────────────────────────────────────────────────────────────────────
    def _generate_quarterly_report(self, params: Dict[str, Any]) -> AgentResponse:
        """
        End-to-end orchestrator: given only a customer name, runs every pipeline
        skill in sequence and returns the finished PDF.

        Required params
        ---------------
        customer_name   : str   – exact name matching the c_name column in the CSV

        Optional params (forwarded to underlying skills)
        ------------------------------------------------
        csv_path        : str       default "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv"
        bucket_name     : str       GCS bucket for QoQ parquets (default "winrich")
        parquet_dir     : str       local dir with index_dashboard parquets (default "data")
        as_of_date      : datetime
        model_allocation: dict      {Equity: %, Hybrid: %, Debt: %}
        company_name    : str       default "Winrich Professional Services"
        output_dir      : str       directory to write the PDF (default ".")
        skip_commentary : bool      skip AI commentary for faster runs (default False)

        Output keys
        -----------
        pdf_path  : str   – absolute path of the generated PDF
        filename  : str   – basename of the PDF file

        AgentStatus
        -----------
        SUCCESS  – PDF generated (commentary errors are non-fatal)
        RETRY    – recoverable step failed (QoQ fetch, benchmarks)
        FAILED   – hard failure (CSV missing, customer not found, PDF crash)
        """
        customer_name = params.get("customer_name") or params.get("selected_customer")
        if not customer_name:
            return AgentResponse(
                AgentStatus.FAILED,
                error="'customer_name' is required in params",
            )

        # Common defaults forwarded to every sub-skill
        base = {
            "csv_path":     params.get("csv_path",     "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv"),
            "bucket_name":  params.get("bucket_name",  "winrich"),
            "parquet_dir":  params.get("parquet_dir",  "data"),
            "as_of_date":   params.get("as_of_date",   datetime.now()),
            "company_name": params.get("company_name", "Winrich Professional Services"),
            "output_dir":   params.get("output_dir",   "."),
        }

        # ── Step 1: load & filter portfolio data ──────────────────────────────────
        r1 = self._load_portfolio_data({**base, "customer_name": customer_name})
        if r1.status == AgentStatus.FAILED:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"[load_portfolio_data] {r1.error}",
                metadata=r1.metadata,
            )
        customer_df       = r1.output["customer_df"]
        selected_customer = r1.output["selected_customer"]

        # ── Step 2: calculate allocation & fund metrics ────────────────────────────
        r2 = self._calculate_metrics({"customer_df": customer_df})
        if r2.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED, error=f"[calculate_metrics] {r2.error}")
        metrics = r2.output["metrics"]

        # ── Step 3: fetch QoQ data + benchmarks + portfolio trend ─────────────────
        r3 = self._load_qoq_and_benchmarks({
            **base,
            "customer_name": selected_customer,
            "equity_funds":  metrics["equity_funds"],
        })
        if r3.status == AgentStatus.FAILED:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"[load_qoq_and_benchmarks] {r3.error}",
                metadata=r3.metadata,
            )

        # RETRY is tolerated — surface in metadata but continue to PDF
        qoq_warning       = r3.error if r3.status == AgentStatus.RETRY else None
        quarterly_returns = r3.output.get("quarterly_returns", [])
        quarter_labels    = r3.output.get("quarter_labels",    [])
        blended_return    = r3.output.get("blended_return",    {})
        portfolio_trend   = r3.output.get("portfolio_trend",   [])

        # ── Step 4: AI commentary (optional / non-fatal) ──────────────────────────
        commentary         = []
        commentary_warning = None

        if not params.get("skip_commentary", False):
            r4 = self._generate_ai_commentary({
                "portfolio_data": {
                    "client_name":       selected_customer,
                    "equity_funds":      metrics["equity_funds"],
                    "hybrid_funds":      metrics["hybrid_funds"],
                    "amc_concentration": metrics["amc_concentration"],
                    "quarterly_returns": quarterly_returns,
                    "blended_return":    blended_return,
                    "portfolio_trend":   portfolio_trend,
                    "client_allocation": metrics["allocation"],
                }
            })
            if r4.status == AgentStatus.SUCCESS:
                commentary = r4.output.get("commentary", [])
            else:
                commentary_warning = r4.error   # non-fatal; PDF proceeds without commentary

        # ── Step 5: assemble & render PDF ─────────────────────────────────────────
        r5 = self._generate_pdf_report({
            **base,
            "customer_df":       customer_df,
            "metrics":           metrics,
            "selected_customer": selected_customer,
            "quarterly_returns": quarterly_returns,
            "quarter_labels":    quarter_labels,
            "blended_return":    blended_return,
            "portfolio_trend":   portfolio_trend,
            "commentary":        commentary,
            "model_allocation":  params.get("model_allocation", {}),
        })

        if r5.status == AgentStatus.FAILED:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"[generate_pdf_report] {r5.error}",
                metadata=r5.metadata,
            )

        warnings = [w for w in [qoq_warning, commentary_warning] if w]

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "pdf_path": r5.output["pdf_path"],
                "filename": r5.output["filename"],
            },
            metadata={
                **r5.metadata,
                "steps_completed": [
                    "load_portfolio_data",
                    "calculate_metrics",
                    "load_qoq_and_benchmarks",
                    *([] if params.get("skip_commentary") else ["generate_ai_commentary"]),
                    "generate_pdf_report",
                ],
                "warnings": warnings,
            },
        )