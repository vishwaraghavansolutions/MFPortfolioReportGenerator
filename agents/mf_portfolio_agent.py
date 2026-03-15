"""
agents/mf_portfolio_agent.py
============================
MF Portfolio Agent — orchestrates the mutual-fund report pipeline.

Customer data is sourced exclusively by calling WinrichMFDataAgent; this
agent never touches GCS or CSV files directly.

Benchmark Resolution Pipeline
------------------------------
For every fund in customer_df:
  1. MutualFundBenchmarkAgent.get_fund_benchmark(scheme_code)
       → scheme_category → SEBI benchmark name  (e.g. "NIFTY Midcap 150 TRI")
  2. IndexAgent.get_benchmark_values(benchmark_name)
       → return_1m, return_3m, return_1yr, return_3yr, return_5yr
  3. If all fail: benchmark columns set to None (show N/A in PDF)

Fund Ranking Pipeline
----------------------
For every fund in customer_df (after benchmark enrichment):
  1. FundRankingAgent.get_fund_rank(fund_name, scheme_type, scheme_category)
       → rank, max_rank, rank_label  (e.g. "3 / 29")
  2. Writes winrich_rank column on customer_df
  3. Falls back to "N/A" if the fund is not found in the ranking data
     (e.g. gold/silver FOFs that have no category peers)

Dependency graph
----------------
    MFPortfolioAgent
        ├── WinrichMFDataAgent          (data layer — GCS / CSV)
        │       └── datawarehouse_loader
        │       └── customer_portfolio
        ├── MutualFundBenchmarkAgent    (SEBI benchmark name per fund)
        ├── IndexAgent                  (actual return values per benchmark)
        ├── FundRankingAgent            (WinRich rank within category)
        ├── MFPortfolioPDFGenerator     (PDF rendering)
        └── generate_ai_commentary      (Claude API)

Skills (public)
---------------
  1. list_customers            – proxy to WinrichMFDataAgent.list_customers
  2. load_portfolio_data       – proxy to WinrichMFDataAgent.load_customer_portfolio
  3. enrich_benchmarks         – resolve SEBI benchmark + returns per fund
  4. enrich_fund_ranks         – attach WinRich rank to every fund row
  5. calculate_metrics         – derive allocation %, fund lists, AMC concentration
  6. generate_ai_commentary    – Claude narrative commentary
  7. generate_pdf_report       – assemble portfolio_data and render PDF
  8. generate_quarterly_report – end-to-end orchestrator (single call)
  9. store_portfolio_summary   – persist flat metrics + commentary to GCS parquet (winrich_shared/data/mf_portfolio_summary/)
 10. load_portfolio_summary    – read the portfolio-summary parquet (optionally filtered by customer)
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from agents.base import Agent, AgentResponse, AgentStatus
from agents.winrich_mf_data_agent import WinrichMFDataAgent
from agents.mf_benchmark_agent import MutualFundBenchmarkAgent
from agents.index_agent import IndexAgent
from agents.mf_funds_ranking_agent import FundRankingAgent
from agents.gcs_storage_agent import GCSStorageAgent

from utils.mf_portfolio_pdf_generator import (
    MFPortfolioPDFGenerator,
    generate_ai_commentary as _generate_ai_commentary,
)
from utils.pdf_utils import format_currency_indian
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.root.setLevel(logging.DEBUG)

_DEFAULT_BUCKET = "winrich"

_SCHEME_DF: pd.DataFrame = pd.read_csv("data/SchemeData2301262313SS.csv")
_SCHEME_DF.columns = _SCHEME_DF.columns.str.strip()

_BENCHMARK_RETURN_COLS = [
    "benchmark_return_1m",
    "benchmark_return_3m",
    "benchmark_return_1yr",
    "benchmark_return_3yr",
    "benchmark_return_5yr",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    if not amc.endswith(" AMC"):
        amc = amc + " AMC"
    return amc


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
    Benchmark names are resolved via MutualFundBenchmarkAgent (SEBI mapping).
    Benchmark return values are fetched via IndexAgent (NSE index dashboard).
    WinRich fund ranks are resolved via FundRankingAgent.

    Parameters
    ----------
    data_agent : WinrichMFDataAgent, optional
    benchmark_agent : MutualFundBenchmarkAgent, optional
    index_agent : IndexAgent, optional
    ranking_agent : FundRankingAgent, optional
    """

    name = "MFPortfolioAgent"

    def __init__(
        self,
        data_agent:      Optional[WinrichMFDataAgent]         = None,
        benchmark_agent: Optional[MutualFundBenchmarkAgent]   = None,
        index_agent:     Optional[IndexAgent]                  = None,
        ranking_agent:   Optional[FundRankingAgent]            = None,
        gcs_agent:       Optional[GCSStorageAgent]             = None,
    ):
        self._data_agent      = data_agent
        self._benchmark_agent = benchmark_agent
        self._index_agent     = index_agent
        self._ranking_agent   = ranking_agent
        self._gcs_agent       = gcs_agent

    # ── Lazy agent accessors ───────────────────────────────────────────────────

    def _get_data_agent(self) -> WinrichMFDataAgent:
        if self._data_agent is None:
            self._data_agent = WinrichMFDataAgent()
        return self._data_agent

    def _get_benchmark_agent(self) -> MutualFundBenchmarkAgent:
        if self._benchmark_agent is None:
            self._benchmark_agent = MutualFundBenchmarkAgent()
        return self._benchmark_agent

    def _get_index_agent(self) -> IndexAgent:
        if self._index_agent is None:
            self._index_agent = IndexAgent()
        return self._index_agent

    def _get_ranking_agent(self) -> FundRankingAgent:
        if self._ranking_agent is None:
            self._ranking_agent = FundRankingAgent()
        return self._ranking_agent

    def _get_gcs_agent(self) -> GCSStorageAgent:
        if self._gcs_agent is None:
            self._gcs_agent = GCSStorageAgent()
        return self._gcs_agent

    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "list_customers":            self._list_customers,
            "load_portfolio_data":       self._load_portfolio_data,
            "enrich_benchmarks":         self._enrich_benchmarks,
            "enrich_fund_ranks":         self._enrich_fund_ranks,
            "calculate_metrics":         self._calculate_metrics,
            "generate_ai_commentary":    self._generate_ai_commentary,
            "generate_pdf_report":       self._generate_pdf_report,
            "generate_quarterly_report": self._generate_quarterly_report,
            "store_portfolio_summary":   self._store_portfolio_summary,
            "load_portfolio_summary":    self._load_portfolio_summary,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ── Skill 0: list_customers (proxy) ───────────────────────────────────────

    def _list_customers(self, params: Dict[str, Any]) -> AgentResponse:
        resp = self._get_data_agent().run("list_customers", params)
        if resp.status == AgentStatus.FAILED:
            resp.error = f"[WinrichMFDataAgent] {resp.error}"
        return resp

    # ── Skill 1: load_portfolio_data (proxy) ──────────────────────────────────

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

    # ── Skill 2: enrich_benchmarks ────────────────────────────────────────────

    def _enrich_benchmarks(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Resolve SEBI benchmark name and return metrics for every fund row.

        Resolution order per fund:
          1. MutualFundBenchmarkAgent.get_fund_benchmark(scheme_code)
          2. IndexAgent.get_benchmark_values(benchmark_name)
          3. All columns set to None  (PDF shows N/A)

        Required params
        ---------------
          customer_df  : pd.DataFrame
          parquet_dir  : str

        Optional params
        ---------------
          year, month  : int

        Output keys
        -----------
          customer_df        : pd.DataFrame  (original + new benchmark columns)
          enrichment_summary : dict
        """
        customer_df: pd.DataFrame = params.get("customer_df")
        if customer_df is None:
            return AgentResponse(AgentStatus.FAILED, error="'customer_df' is required")

        parquet_dir = params.get("parquet_dir", "data")
        year        = params.get("year")
        month       = params.get("month")

        try:
            index_agent = self._get_index_agent()
            if index_agent.store.is_empty():
                index_agent.run(
                    "load_index_files",
                    {"parquet_dir": parquet_dir, "force_reload": False},
                )
        except Exception as exc:
            import traceback as _tb
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"IndexAgent setup failed: {type(exc).__name__}: {exc}",
                metadata={"traceback": _tb.format_exc()},
            )

        try:
            benchmark_agent = self._get_benchmark_agent()
        except Exception as exc:
            import traceback as _tb
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"BenchmarkAgent setup failed: {type(exc).__name__}: {exc}",
                metadata={"traceback": _tb.format_exc()},
            )

        enriched_rows = []
        summary: Dict[str, Any] = {}

        for _, row in customer_df.iterrows():
            fund_name   = str(row.get("s_name", ""))
            scheme_code = row.get("scheme_code")

            outcome = {
                "fund":           fund_name,
                "scheme_code":    scheme_code,
                "benchmark_name": None,
                "source":         "N/A",
                "error":          None,
            }

            benchmark_name    = None
            scheme_category   = None
            return_values: Dict[str, Optional[float]] = {c: None for c in _BENCHMARK_RETURN_COLS}

            if scheme_code is not None:
                try:
                    bm_resp = benchmark_agent.run(
                        "get_fund_benchmark",
                        {"scheme_code": int(scheme_code)},
                    )
                    if bm_resp.status == AgentStatus.SUCCESS:
                        benchmark_name  = bm_resp.output.get("benchmark")
                        scheme_category = bm_resp.output.get("scheme_category")
                        outcome["benchmark_name"] = benchmark_name
                    else:
                        outcome["error"] = f"MFBenchmarkAgent: {bm_resp.error}"
                except Exception as exc:
                    outcome["error"] = f"MFBenchmarkAgent exception: {exc}"

            if benchmark_name and not index_agent.store.is_empty():
                try:
                    idx_params: Dict[str, Any] = {
                        "benchmark": benchmark_name,
                        "metrics":   _BENCHMARK_RETURN_COLS,
                    }
                    if year  is not None: idx_params["year"]  = year
                    if month is not None: idx_params["month"] = month

                    idx_resp = index_agent.run("get_benchmark_values", idx_params)

                    if idx_resp.status == AgentStatus.SUCCESS:
                        metrics_out = idx_resp.output.get("metrics", {})
                        for col in _BENCHMARK_RETURN_COLS:
                            return_values[col] = metrics_out.get(col)
                        outcome["source"] = "MFBenchmarkAgent+IndexAgent"
                    else:
                        outcome["error"] = (
                            (outcome["error"] or "") +
                            f" | IndexAgent: {idx_resp.error}"
                        )
                except Exception as exc:
                    outcome["error"] = (
                        (outcome["error"] or "") +
                        f" | IndexAgent exception: {exc}"
                    )

            enriched_row = row.to_dict()
            # Only overwrite when the API lookup actually resolved a value.
            # Preserves pre-populated benchmark_index / scheme_category from
            # WinrichMFDataAgent (SchemeLookup) when the live API fails.
            if benchmark_name is not None:
                enriched_row["benchmark_index"] = benchmark_name
            elif "benchmark_index" not in enriched_row:
                enriched_row["benchmark_index"] = None
            if scheme_category is not None:
                enriched_row["scheme_category"] = scheme_category
            elif "scheme_category" not in enriched_row:
                enriched_row["scheme_category"] = None
            enriched_row["benchmark_source"] = outcome["source"]
            for col in _BENCHMARK_RETURN_COLS:
                # Only write return values when resolved; don't zero out existing data.
                if return_values[col] is not None:
                    enriched_row[col] = return_values[col]
                elif col not in enriched_row:
                    enriched_row[col] = None

            enriched_rows.append(enriched_row)
            summary[fund_name] = outcome

        try:
            enriched_df = pd.DataFrame(enriched_rows)
        except Exception as exc:
            import traceback as _tb
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"Failed to build enriched DataFrame: {type(exc).__name__}: {exc}",
                metadata={"traceback": _tb.format_exc(), "rows_built": len(enriched_rows)},
            )

        resolved   = sum(1 for o in summary.values() if o["source"] != "N/A")
        unresolved = sum(1 for o in summary.values() if o["source"] == "N/A")

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "customer_df":        enriched_df,
                "enrichment_summary": summary,
            },
            metadata={
                "total_funds":  len(enriched_rows),
                "resolved":     resolved,
                "unresolved":   unresolved,
            },
        )

    # ── Skill 3: enrich_fund_ranks ────────────────────────────────────────────

    def _enrich_fund_ranks(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Attach WinRich rank to every fund row using FundRankingAgent.

        For each row the agent calls:
            FundRankingAgent.get_fund_rank(fund_name, scheme_type, scheme_category)

        scheme_type and scheme_category are taken from the row's existing
        columns (populated by enrich_benchmarks).  If those columns are absent
        the agent falls back to the values supplied in params, and finally to
        empty strings (which will result in a rank miss and "N/A").

        Resolution strategy per fund
        -----------------------------
          1. Exact fund name  → FundRankingAgent
          2. Cleaned fund name (suffixes stripped) → FundRankingAgent
          3. Neither matched  → winrich_rank = "N/A"

        Required params
        ---------------
          customer_df : pd.DataFrame
            Must have at minimum: s_name
            Ideally also has: scheme_category (from enrich_benchmarks)

        Optional params
        ---------------
          default_scheme_type     : str  e.g. "Equity Scheme"
          default_scheme_category : str  e.g. "Large Cap Fund"

        Output keys
        -----------
          customer_df     : pd.DataFrame  – original df + winrich_rank column
          ranking_summary : dict          – per-fund outcome details
        """
        customer_df: pd.DataFrame = params.get("customer_df")
        if customer_df is None:
            return AgentResponse(AgentStatus.FAILED, error="'customer_df' is required")

        default_scheme_type     = params.get("default_scheme_type",     "Equity Scheme")
        default_scheme_category = params.get("default_scheme_category", "")

        # ── benchmark_index → SEBI scheme_category inference map ──────────────
        _BENCH_TO_CATEGORY: Dict[str, str] = {
            # Large Cap
            "nifty 50":                        "Large Cap Fund",
            "nifty 50 tri":                    "Large Cap Fund",
            "nifty 100":                       "Large Cap Fund",
            "nifty 100 tri":                   "Large Cap Fund",
            "s&p bse sensex":                  "Large Cap Fund",
            "s&p bse 100":                     "Large Cap Fund",
            # Large & Mid Cap
            "nifty largemidcap 250":           "Large & Mid Cap Fund",
            "nifty largemidcap 250 tri":       "Large & Mid Cap Fund",
            "s&p bse 250 largecap index":      "Large & Mid Cap Fund",
            # Mid Cap
            "nifty midcap 150":                "Mid Cap Fund",
            "nifty midcap 150 tri":            "Mid Cap Fund",
            "nifty midcap 50":                 "Mid Cap Fund",
            "nifty midcap 50 tri":             "Mid Cap Fund",
            "s&p bse midcap":                  "Mid Cap Fund",
            # Small Cap
            "nifty smallcap 250":              "Small Cap Fund",
            "nifty smallcap 250 tri":          "Small Cap Fund",
            "nifty smallcap 100":              "Small Cap Fund",
            "s&p bse smallcap":                "Small Cap Fund",
            # Flexi Cap / Multi Cap
            "nifty 500":                       "Flexi Cap Fund",
            "nifty 500 tri":                   "Flexi Cap Fund",
            "s&p bse 500":                     "Flexi Cap Fund",
            # ELSS
            "nifty 500 tri elss":              "ELSS",
            # Balanced Advantage / Dynamic Asset Allocation
            "nifty 50 hybrid composite debt 65:35 index": "Balanced Advantage Fund",
            "crisil hybrid 50+50 - moderate index":       "Balanced Advantage Fund",
            # Multi Asset
            "nifty 200":                       "Multi Asset Allocation Fund",
        }

        benchmark_agent = self._get_benchmark_agent()

        def _infer_scheme_category(row) -> tuple[str, str]:
            """
            Resolution order (returns (scheme_category, source)):
              1. scheme_category column on row (from enrich_benchmarks / SchemeLookup)
              2. benchmark_index column → _BENCH_TO_CATEGORY lookup
              3. Parquet lookup via get_scheme_classification(scheme_code)
              4. Empty string (rank will be N/A)
            """
            sc = str(row.get("scheme_category", "") or "").strip()
            if sc:
                # Strip SEBI prefix e.g. "Equity Scheme - Large Cap Fund" → "Large Cap Fund"
                if " - " in sc:
                    sc = sc.split(" - ", 1)[1].strip()
                return sc, "row_column"

            bench = str(row.get("benchmark_index", "") or "").strip().lower()
            bench_clean = bench.replace(" total return index", "").replace(" tri", "").strip()
            inferred = (
                _BENCH_TO_CATEGORY.get(bench, "")
                or _BENCH_TO_CATEGORY.get(bench_clean, "")
            )
            if inferred:
                return inferred, "benchmark_index_inferred"

            # Parquet fallback: call get_scheme_classification (parquet-only, no live API)
            scheme_code = row.get("scheme_code")
            if scheme_code is not None:
                try:
                    resp = benchmark_agent.run(
                        "get_scheme_classification",
                        {"scheme_code": int(scheme_code)},
                    )
                    if resp.status == AgentStatus.SUCCESS:
                        cat   = str(resp.output.get("scheme_category", "") or "").strip()
                        stype = str(resp.output.get("scheme_type",     "") or "").strip()
                        if cat:
                            if " - " in cat:
                                cat = cat.split(" - ", 1)[1].strip()
                            logger.debug(
                                "[enrich_fund_ranks]   scheme_classification parquet → "
                                "scheme_code=%s category=%r scheme_type=%r",
                                scheme_code, cat, stype,
                            )
                            row["scheme_category"] = cat
                            if stype:
                                row["scheme_type"] = stype
                            return cat, "parquet"
                except Exception as exc:
                    logger.debug(
                        "[enrich_fund_ranks]   scheme_classification parquet failed for %s: %s",
                        scheme_code, exc,
                    )

            return "", "missing"

        try:
            ranking_agent = self._get_ranking_agent()
        except Exception as exc:
            import traceback as _tb
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"FundRankingAgent setup failed: {type(exc).__name__}: {exc}",
                metadata={"traceback": _tb.format_exc()},
            )

        enriched_rows = []
        summary: Dict[str, Any] = {}

        logger.debug(
            "[enrich_fund_ranks] customer_df columns: %s | rows: %d",
            list(customer_df.columns), len(customer_df),
        )

        for _, row in customer_df.iterrows():
            row = row.to_dict()  # make mutable so live API can cache back scheme_category
            fund_name = str(row.get("s_name", "")).strip()

            scheme_category, sc_source = _infer_scheme_category(row)
            scheme_category = scheme_category or default_scheme_category

            scheme_type = str(row.get("scheme_type", "") or default_scheme_type).strip()

            logger.debug(
                "[enrich_fund_ranks] %-55s | "
                "raw scheme_category=%r | raw benchmark_index=%r | "
                "resolved scheme_type=%r | resolved scheme_category=%r | source=%s",
                fund_name,
                row.get("scheme_category"),
                row.get("benchmark_index"),
                scheme_type,
                scheme_category,
                sc_source,
            )

            outcome = {
                "fund":                    fund_name,
                "scheme_type":             scheme_type,
                "scheme_category":         scheme_category,
                "scheme_category_source":  sc_source,
                "rank_label":              "N/A",
                "rank":                    None,
                "max_rank":                None,
                "source":                  "N/A",
                "error":                   None,
            }

            winrich_rank = "N/A"

            if not fund_name:
                outcome["error"] = "empty fund_name"
                logger.debug("[enrich_fund_ranks]   → SKIP: empty fund_name")
            elif not scheme_type:
                outcome["error"] = "empty scheme_type"
                logger.debug("[enrich_fund_ranks]   → SKIP: empty scheme_type")
            elif not scheme_category:
                outcome["error"] = "scheme_category could not be resolved"
                logger.debug(
                    "[enrich_fund_ranks]   → SKIP: scheme_category is empty — "
                    "add benchmark_index to _BENCH_TO_CATEGORY or pass default_scheme_category"
                )
            else:
                # Attempt 1: raw fund name
                resp = ranking_agent.run("get_fund_rank", {
                    "fund_name":       fund_name,
                    "scheme_type":     scheme_type,
                    "scheme_category": scheme_category,
                })
                logger.debug(
                    "[enrich_fund_ranks]   attempt-1 raw name → status=%s error=%s",
                    resp.status.value, resp.error,
                )

                # Attempt 2: cleaned fund name (strip plan/growth suffixes)
                if resp.status != AgentStatus.SUCCESS:
                    cleaned = _clean_fund_name(fund_name)
                    logger.debug("[enrich_fund_ranks]   attempt-2 cleaned=%r", cleaned)
                    if cleaned != fund_name:
                        resp = ranking_agent.run("get_fund_rank", {
                            "fund_name":       cleaned,
                            "scheme_type":     scheme_type,
                            "scheme_category": scheme_category,
                        })
                        logger.debug(
                            "[enrich_fund_ranks]   attempt-2 cleaned name → status=%s error=%s",
                            resp.status.value, resp.error,
                        )

                if resp.status == AgentStatus.SUCCESS:
                    winrich_rank           = resp.output.get("rank_label", "N/A")
                    outcome["rank_label"]  = winrich_rank
                    outcome["rank"]        = resp.output.get("rank")
                    outcome["max_rank"]    = resp.output.get("max_rank")
                    outcome["source"]      = resp.output.get("source", "ranking_file")
                    logger.debug(
                        "[enrich_fund_ranks]   → MATCHED: rank=%s matched_csv_name=%r",
                        winrich_rank,
                        resp.output.get("fund_name"),
                    )
                else:
                    outcome["error"] = resp.error
                    logger.debug("[enrich_fund_ranks]   → NO MATCH: %s", resp.error)

            enriched_row = row  # already a dict
            # Only write winrich_rank when a real rank was resolved.
            # Preserve any pre-existing valid value rather than overwriting with "N/A".
            existing_rank = str(enriched_row.get("winrich_rank") or "").strip()
            if winrich_rank != "N/A" or not existing_rank or existing_rank == "N/A":
                enriched_row["winrich_rank"] = winrich_rank
            enriched_rows.append(enriched_row)
            summary[fund_name] = outcome

        enriched_df = pd.DataFrame(enriched_rows)

        ranked   = sum(1 for o in summary.values() if o["rank"] is not None)
        unranked = len(summary) - ranked

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "customer_df":     enriched_df,
                "ranking_summary": summary,
            },
            metadata={
                "total_funds": len(enriched_rows),
                "ranked":      ranked,
                "unranked":    unranked,
            },
        )

    # ── Skill 4: calculate_metrics ────────────────────────────────────────────

    def _calculate_metrics(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Derive allocation %, equity_funds, hybrid_funds, amc_concentration.

        Required params: customer_df : pd.DataFrame
          (should be the enriched df from enrich_benchmarks + enrich_fund_ranks
           so that equity_funds already carry benchmark_index, return columns,
           and winrich_rank)
        Output keys: metrics : dict
        """
        customer_df: pd.DataFrame = params.get("customer_df")
        if customer_df is None:
            return AgentResponse(AgentStatus.FAILED, error="'customer_df' is required")

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

        def _scalar(v) -> Optional[float]:
            if v is None:
                return None
            try:
                import math
                f = float(v)
                return None if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return None

        equity_funds = []
        for _, row in customer_df[customer_df["Nature"] == "Equity"].iterrows():
            equity_funds.append({
                "name":                 str(row["s_name"]),
                "xirr":                 _scalar(row.get("FolioXIRR")),
                "benchmark":            _scalar(row.get("NatureXIRR")) or 0.0,
                "winrich_rank":         str(row.get("winrich_rank") or "N/A"),
                "benchmark_index":      str(row.get("benchmark_index") or ""),
                "benchmark_return_1m":  _scalar(row.get("benchmark_return_1m")),
                "benchmark_return_3m":  _scalar(row.get("benchmark_return_3m")),
                "benchmark_return_1yr": _scalar(row.get("benchmark_return_1yr")),
                "benchmark_return_3yr": _scalar(row.get("benchmark_return_3yr")),
                "benchmark_return_5yr": _scalar(row.get("benchmark_return_5yr")),
            })

        hybrid_funds = []
        for _, row in customer_df[customer_df["Nature"] == "Balance"].iterrows():
            hybrid_funds.append({
                "name":                 str(row["s_name"]),
                "xirr":                 _scalar(row.get("FolioXIRR")),
                "winrich_rank":         str(row.get("winrich_rank") or "N/A"),
                "benchmark_index":      str(row.get("benchmark_index") or ""),
                "benchmark_return_1m":  _scalar(row.get("benchmark_return_1m")),
                "benchmark_return_3m":  _scalar(row.get("benchmark_return_3m")),
                "benchmark_return_1yr": _scalar(row.get("benchmark_return_1yr")),
                "benchmark_return_3yr": _scalar(row.get("benchmark_return_3yr")),
                "benchmark_return_5yr": _scalar(row.get("benchmark_return_5yr")),
            })

        # AMC concentration
        amc_value_map: Dict[str, float] = {}
        has_amc_col = "AMC" in customer_df.columns
        for _, row in customer_df.iterrows():
            cur_val = float(row.get("CurValue") or 0)
            if cur_val <= 0:
                continue
            amc = _get_amc(row["s_name"])
            if amc == "Unknown" and has_amc_col:
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

        known_natures = {"Equity", "Balance", "Debt"}
        other_value   = float(customer_df[~customer_df["Nature"].isin(known_natures)]["CurValue"].fillna(0).sum())

        metrics = {
            "total_value":  total_value,
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

    # ── Skill 4b: store_portfolio_summary ─────────────────────────────────────

    def _store_portfolio_summary(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Persist a flat summary of the customer's metrics to the shared parquet
        in GCS (winrich_shared/data/mf_portfolio_summary/mf_portfolio_summary.parquet).

        The parquet is indexed by customer_name — one row per customer.
        An existing row for the same customer is replaced (upsert semantics).

        Required params
        ---------------
        customer_name : str        – customer identifier (matches selected_customer)
        metrics       : dict       – output from calculate_metrics skill

        Optional params
        ---------------
        commentary    : list[dict] – AI commentary sections [{heading, body}, ...]
                                     stored as a JSON string in the parquet
        as_of_date    : str        – ISO date string (e.g. "2026-03-14"); defaults to today

        Output keys
        -----------
        gcs_uri   : str
        blob_name : str
        row_count : int
        """
        import json
        from datetime import timezone

        customer_name: str = params.get("customer_name", "").strip()
        metrics: Optional[Dict[str, Any]] = params.get("metrics")

        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")
        if not metrics:
            return AgentResponse(AgentStatus.FAILED, error="'metrics' is required")

        as_of_date_str: str = str(
            params.get("as_of_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        alloc = metrics.get("allocation", {})
        commentary = params.get("commentary") or []

        summary_row: Dict[str, Any] = {
            "as_of_date":       as_of_date_str,
            "total_value":      metrics.get("total_value", 0.0),
            "equity_pct":       alloc.get("Equity", 0.0),
            "hybrid_pct":       alloc.get("Hybrid", 0.0),
            "debt_pct":         alloc.get("Debt", 0.0),
            "other_pct":        alloc.get("Other", 0.0),
            "num_funds":        metrics.get("num_funds", 0),
            "num_equity_funds": len(metrics.get("equity_funds", [])),
            "num_hybrid_funds": len(metrics.get("hybrid_funds", [])),
            "commentary_json":  json.dumps(commentary),
            "updated_at":       datetime.now(timezone.utc).isoformat(),
        }

        resp = self._get_gcs_agent().run(
            "store_portfolio_summary",
            {
                "customer_name": customer_name,
                "summary_row":   summary_row,
            },
        )

        if resp.status != AgentStatus.SUCCESS:
            return AgentResponse(
                resp.status,
                error=f"store_portfolio_summary (GCS): {resp.error}",
                metadata=resp.metadata,
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output=resp.output,
            metadata={"customer_name": customer_name, "as_of_date": as_of_date_str},
        )

    # ── Skill 4c: load_portfolio_summary ──────────────────────────────────────

    def _load_portfolio_summary(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Load the shared portfolio-summary parquet from GCS.

        Proxy to GCSStorageAgent.load_portfolio_summary.

        Optional params
        ---------------
        customer_name : str  – if provided, returns only that customer's row(s)

        Output keys
        -----------
        dataframe     : pd.DataFrame  – columns: customer_name, as_of_date,
                                        total_value, equity_pct, hybrid_pct,
                                        debt_pct, other_pct, num_funds,
                                        num_equity_funds, num_hybrid_funds,
                                        commentary_json, updated_at
        row_count     : int
        columns       : list[str]
        gcs_uri       : str
        """
        resp = self._get_gcs_agent().run("load_portfolio_summary", params)
        if resp.status == AgentStatus.FAILED:
            resp.error = f"[GCSStorageAgent] {resp.error}"
        return resp

    # ── Skill 5: generate_ai_commentary ───────────────────────────────────────

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

    # ── Skill 6: generate_pdf_report ──────────────────────────────────────────

    def _generate_pdf_report(self, params: Dict[str, Any]) -> AgentResponse:
        customer_df       = params.get("customer_df")
        metrics           = params.get("metrics")
        selected_customer = params.get("selected_customer", "Customer")
        company_name      = params.get("company_name", "WinRich Professional Services")
        output_dir        = params.get("output_dir", ".")

        logger.info("Inside generate_pdf_report") 
        logger.info(customer_df)
        missing = [k for k, v in {"customer_df": customer_df, "metrics": metrics}.items()
                   if v is None]
        if missing:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"Missing required params: {missing}")

        commentary = params.get("commentary") or []
        if isinstance(commentary, str):
            commentary = [{"heading": "Performance Commentary", "body": commentary}] if commentary else []

        customer_df = customer_df.copy()
        for col in ("CurValue", "TotalInvAmt", "FolioXIRR", "NatureXIRR", "benchmark_xirr"):
            if col in customer_df.columns:
                customer_df[col] = pd.to_numeric(
                    customer_df[col].astype(str).str.replace(",", "").str.strip(),
                    errors="coerce",
                )

        total_current_value = float(customer_df["CurValue"].fillna(0).sum())
        total_invested      = float(customer_df["TotalInvAmt"].fillna(0).sum())
        total_gain          = total_current_value - total_invested
        try:
            cur_vals = customer_df["CurValue"].fillna(0)
            xirr_weighted = (
                (customer_df["FolioXIRR"].fillna(0) * cur_vals).sum() / cur_vals.sum()
            ) if cur_vals.sum() > 0 else None
            portfolio_xirr = float(xirr_weighted) if xirr_weighted is not None else None
        except Exception:
            portfolio_xirr = None

        _known_natures  = {"Equity", "Balance", "Debt"}
        _other_df       = customer_df[~customer_df["Nature"].isin(_known_natures)]
        alloc           = metrics["allocation"]
        allocation_rows = []
        for nature_key, display_name in [
            ("Equity",  "Equity"),
            ("Balance", "Hybrid"),
            (None,      "Other"),
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

        _as_of = params.get("as_of_date", datetime.now())
        if isinstance(_as_of, str):
            try:
                _as_of = datetime.strptime(_as_of, "%Y-%m-%d")
            except Exception:
                _as_of = datetime.now()

        all_funds = []
        for _, row in customer_df.iterrows():
            bench_idx = str(row.get("benchmark_index") or "").strip() or "—"

            def _safe_float(col: str) -> Optional[float]:
                val = row.get(col)
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return None
                try:
                    f = float(val)
                    return f if f != 0.0 else None
                except (TypeError, ValueError):
                    return None

            folio_age_days = None
            raw_date = row.get("FolioStartDate") or row.get("folio_start_date") or ""
            if raw_date:
                try:
                    parsed = pd.to_datetime(str(raw_date), errors="coerce")
                    if not pd.isnull(parsed):
                        folio_age_days = (_as_of - parsed.to_pydatetime().replace(tzinfo=None)).days
                except Exception:
                    pass

            b3m  = _safe_float("benchmark_return_3m")  if (folio_age_days is None or folio_age_days >= 90)   else None
            b1yr = _safe_float("benchmark_return_1yr") if (folio_age_days is None or folio_age_days >= 365)  else None
            b3yr = _safe_float("benchmark_return_3yr") if (folio_age_days is None or folio_age_days >= 1095) else None
            b5yr = _safe_float("benchmark_return_5yr") if (folio_age_days is None or folio_age_days >= 1825) else None

            folio_xirr = _safe_float("FolioXIRR")

            winrich_rank = "N/A"
            rank_val = row.get("winrich_rank")
            if rank_val is not None and str(rank_val).strip() not in ("", "nan", "None", "N/A"):
                winrich_rank = str(rank_val).strip()

            all_funds.append({
                "name":                 row["s_name"],
                "benchmark_index":      bench_idx,
                "winrich_rank":         winrich_rank,
                "xirr":                 folio_xirr,
                "benchmark_return_3m":  b3m,
                "benchmark_return_1yr": b1yr,
                "benchmark_return_3yr": b3yr,
                "benchmark_return_5yr": b5yr,
            })

        fund_gains = []
        for _, row in customer_df.iterrows():
            inv  = float(row.get("TotalInvAmt") or 0)
            cur  = float(row.get("CurValue")    or 0)
            gain = cur - inv
            abs_return = (gain / inv * 100) if inv > 0 else 0
            raw_date = row.get("FolioStartDate") or row.get("folio_start_date") or ""
            try:
                parsed_date     = pd.to_datetime(str(raw_date), errors="coerce")
                folio_start_fmt = parsed_date.strftime("%d-%b-%Y") if not pd.isnull(parsed_date) else "—"
            except Exception:
                folio_start_fmt = str(raw_date).split("T")[0] if raw_date else "—"
            fund_gains.append({
                "name":             row["s_name"],
                "folio_start_date": folio_start_fmt,
                "amount_invested":  inv,
                "current_value":    cur,
                "gain":             gain,
                "abs_return":       abs_return,
                "xirr":             float(row["FolioXIRR"]) if pd.notna(row.get("FolioXIRR")) else None,
            })

        try:
            start_dates      = pd.to_datetime(customer_df["FolioStartDate"], errors="coerce").dropna()
            investment_start = start_dates.min().strftime("%B %d, %Y") if not start_dates.empty else ""
        except Exception:
            investment_start = ""

        resolved_path = params.get("resolved_path", "")
        data_as_on    = ""
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
            "company_name":        company_name,
            "client_name":         selected_customer,
            "report_date":         datetime.now().strftime("%B %d, %Y"),
            "investment_start":    investment_start,
            "prepared_by":         params.get("prepared_by", "WinRich Research Desk"),
            "data_as_on":          data_as_on,
            "risk_profile":        params.get("risk_profile", ""),
            "logo_path":           params.get("logo_path", ""),
            "website":             params.get("website", "www.winrich.in"),
            "email":               params.get("email", "support@winrich.in"),
            "n_funds":             len(customer_df),
            "n_amcs":              len(metrics["amc_concentration"]),
            "total_current_value": total_current_value,
            "total_invested":      total_invested,
            "total_gain":          total_gain,
            "portfolio_xirr":      portfolio_xirr,
            "allocation_rows":     allocation_rows,
            "all_funds":           all_funds,
            "fund_gains":          fund_gains,
            "amc_concentration":   metrics["amc_concentration"],
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
            metadata={
                "client":  selected_customer,
                "company": company_name,
                "n_funds": len(customer_df),
                "n_amcs":  len(metrics["amc_concentration"]),
            },
        )

    # ── Skill 7: generate_quarterly_report (orchestrator) ─────────────────────

    def _generate_quarterly_report(self, params: Dict[str, Any]) -> AgentResponse:
        """
        End-to-end orchestrator.

        Pipeline
        --------
          1. load_portfolio_data    → WinrichMFDataAgent
          2. enrich_benchmarks      → MFBenchmarkAgent + IndexAgent
          3. enrich_fund_ranks      → FundRankingAgent (populates winrich_rank)
          4. calculate_metrics      → allocation, equity_funds (with rank + benchmark)
          5. generate_ai_commentary → Claude narrative (non-fatal)
          5b. store_portfolio_summary → persist metrics + commentary to GCS parquet (non-fatal)
          6. generate_pdf_report    → PDF render (winrich_rank printed in Section 2)

        Required params: customer_name : str
        Optional params: bucket_name, max_lookback_days, parquet_dir,
                         as_of_date, company_name, output_dir,
                         skip_commentary, risk_profile, logo_path,
                         default_scheme_type, default_scheme_category
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

        # ── Step 1: load raw portfolio data ───────────────────────────────────
        r1 = self.run("load_portfolio_data", {**base, "customer_name": customer_name})
        if r1.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[load_portfolio_data] {r1.error}",
                                 metadata=r1.metadata)

        customer_df       = r1.output["customer_df"]
        selected_customer = r1.output["selected_customer"]
        resolved_path     = r1.output.get("resolved_path", "")

        # ── Step 2: benchmark enrichment ──────────────────────────────────────
        _bm_col = "benchmark_index"
        _already_enriched = (
            _bm_col in customer_df.columns
            and customer_df[_bm_col].notna().any()
            and customer_df[_bm_col].astype(str).str.strip().ne("").any()
        )
        logger.debug(
            "[generate_quarterly_report] Step 2 — _already_enriched=%s | sample: %s",
            _already_enriched,
            customer_df[_bm_col].dropna().unique().tolist()[:5] if _bm_col in customer_df.columns else "N/A",
        )
        if _already_enriched:
            enrichment_meta = {"source": "SchemeLookup (WinrichMFDataAgent)"}
        else:
            r2 = self.run("enrich_benchmarks", {**base, "customer_df": customer_df})
            if r2.status == AgentStatus.SUCCESS:
                customer_df     = r2.output["customer_df"]
                enrichment_meta = r2.metadata
            else:
                enrichment_meta = {"warning": f"enrich_benchmarks failed: {r2.error}"}
            logger.debug("[generate_quarterly_report] Step 2 — result: %s", enrichment_meta)

        # ── Step 3: fund rank enrichment ──────────────────────────────────────
        ranking_meta: Dict[str, Any] = {}
        _already_ranked = (
            "winrich_rank" in customer_df.columns
            and customer_df["winrich_rank"].notna().any()
            and customer_df["winrich_rank"].astype(str).str.strip().ne("").any()
            and customer_df["winrich_rank"].astype(str).str.strip().ne("N/A").any()
        )
        if _already_ranked:
            ranking_meta = {"source": "pre-populated (WinrichMFDataAgent)"}
            logger.debug("[generate_quarterly_report] Step 3 — SKIPPED (already ranked)")
        else:
            r3 = self.run("enrich_fund_ranks", {
                "customer_df":             customer_df,
                "default_scheme_type":     params.get("default_scheme_type",     "Equity Scheme"),
                "default_scheme_category": params.get("default_scheme_category", ""),
            })
            if r3.status == AgentStatus.SUCCESS:
                customer_df  = r3.output["customer_df"]
                ranking_meta = r3.metadata
                logger.debug(
                    "[generate_quarterly_report] Step 3 — complete: %s | winrich_rank sample: %s",
                    ranking_meta,
                    customer_df["winrich_rank"].tolist() if "winrich_rank" in customer_df.columns else "col missing",
                )
            else:
                ranking_meta = {"warning": f"enrich_fund_ranks failed: {r3.error}"}
                logger.warning("[generate_quarterly_report] Step 3 — FAILED: %s", r3.error)

        # ── Step 4: calculate metrics ──────────────────────────────────────────
        r4 = self.run("calculate_metrics", {"customer_df": customer_df})
        if r4.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[calculate_metrics] {r4.error}")
        metrics = r4.output["metrics"]

        # ── Step 5: AI commentary (non-fatal) ─────────────────────────────────
        import math as _math

        def _clean_float(v) -> Optional[float]:
            if v is None:
                return None
            try:
                f = float(v)
                return None if (_math.isnan(f) or _math.isinf(f)) else f
            except (TypeError, ValueError):
                return None

        _cur_vals  = customer_df["CurValue"].fillna(0)
        _inv_vals  = customer_df["TotalInvAmt"].fillna(0) if "TotalInvAmt" in customer_df.columns else _cur_vals * 0
        _total_cur = float(_cur_vals.sum())
        _total_inv = float(_inv_vals.sum())
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

        _all_funds_for_commentary = []
        for _, _row in customer_df.iterrows():
            _all_funds_for_commentary.append({
                "name":                 str(_row.get("s_name", "")),
                "nature":               str(_row.get("Nature", "")),
                "xirr":                 _clean_float(_row.get("FolioXIRR")),
                "winrich_rank":         str(_row.get("winrich_rank", "N/A") or "N/A"),
                "benchmark_index":      str(_row.get("benchmark_index") or ""),
                "benchmark_xirr":       (
                    _clean_float(_row.get("benchmark_return_1yr"))
                    or _clean_float(_row.get("benchmark_return_3yr"))
                    or _clean_float(_row.get("benchmark_return_5yr"))
                ),
                "benchmark_return_3m":  _clean_float(_row.get("benchmark_return_3m")),
                "benchmark_return_1yr": _clean_float(_row.get("benchmark_return_1yr")),
                "benchmark_return_3yr": _clean_float(_row.get("benchmark_return_3yr")),
                "benchmark_return_5yr": _clean_float(_row.get("benchmark_return_5yr")),
            })

        _fund_gains_for_commentary = []
        for _, _row in customer_df.iterrows():
            inv = _clean_float(_row.get("TotalInvAmt")) or 0.0
            cur = _clean_float(_row.get("CurValue"))    or 0.0
            _fund_gains_for_commentary.append({
                "name":     str(_row.get("s_name", "")),
                "nature":   str(_row.get("Nature", "")),
                "invested": inv,
                "current":  cur,
                "gain":     round(cur - inv, 2),
                "xirr":     _clean_float(_row.get("FolioXIRR")),
            })

        commentary         = []
        commentary_warning = None
        if not params.get("skip_commentary", False):
            r5_com = self.run("generate_ai_commentary", {
                "portfolio_data": {
                    "client_name":         selected_customer,
                    "report_date":         datetime.now().strftime("%B %d, %Y"),
                    "investment_start":    _investment_start,
                    "total_invested":      _clean_float(_total_inv),
                    "total_current_value": _clean_float(_total_cur),
                    "total_gain":          round(_total_cur - _total_inv, 2),
                    "portfolio_xirr":      _clean_float(_port_xirr),
                    "n_funds":             int(metrics["num_funds"]),
                    "n_amcs":              len(metrics["amc_concentration"]),
                    "client_allocation":   {k: _clean_float(v) for k, v in metrics["allocation"].items()},
                    "allocation_rows": [
                        {
                            "asset_class":        ac,
                            "your_allocation":    f"{pct:.2f}%",
                            "funds_in_portfolio": " | ".join(
                                str(row["s_name"])
                                for _, row in customer_df[
                                    customer_df["Nature"] == (
                                        "Balance" if ac == "Hybrid" else ac
                                    )
                                ].iterrows()
                            ) or "—",
                        }
                        for ac, pct in metrics["allocation"].items()
                        if (pct or 0) > 0
                    ],
                    "all_funds":         _all_funds_for_commentary,
                    "equity_funds":      [f for f in _all_funds_for_commentary if f["name"] in _equity_names],
                    "hybrid_funds":      [f for f in _all_funds_for_commentary if f["name"] in _hybrid_names],
                    "fund_gains":        _fund_gains_for_commentary,
                    "amc_concentration": {
                        amc: {"value": _clean_float(v["value"]), "pct": _clean_float(v["pct"])}
                        for amc, v in metrics["amc_concentration"].items()
                    },
                }
            })
            if r5_com.status == AgentStatus.SUCCESS:
                commentary = r5_com.output.get("commentary", [])
            else:
                commentary_warning = r5_com.error

        # ── Step 5b: store portfolio summary (non-fatal) ──────────────────────
        _r5b = self.run("store_portfolio_summary", {
            "customer_name": selected_customer,
            "metrics":       metrics,
            "commentary":    commentary,
            "as_of_date":    params.get("as_of_date", ""),
        })
        if _r5b.status != AgentStatus.SUCCESS:
            logger.warning(
                "[generate_quarterly_report] store_portfolio_summary failed (non-fatal): %s",
                _r5b.error,
            )
        else:
            logger.info(
                "[generate_quarterly_report] Portfolio summary stored: %s (%d rows)",
                _r5b.output.get("gcs_uri"),
                _r5b.output.get("row_count", 0),
            )

        # ── Step 6: render PDF ────────────────────────────────────────────────
        r6 = self.run("generate_pdf_report", {
            **base,
            "customer_df":       customer_df,
            "metrics":           metrics,
            "selected_customer": selected_customer,
            "resolved_path":     resolved_path,
            "commentary":        commentary,
            "risk_profile":      params.get("risk_profile", ""),
            "logo_path":         params.get("logo_path", ""),
        })

        if r6.status == AgentStatus.FAILED:
            return AgentResponse(AgentStatus.FAILED,
                                 error=f"[generate_pdf_report] {r6.error}",
                                 metadata=r6.metadata)

        warnings = [w for w in [commentary_warning] if w]
        return AgentResponse(
            AgentStatus.SUCCESS,
            output={"pdf_path": r6.output["pdf_path"], "filename": r6.output["filename"]},
            metadata={
                **r6.metadata,
                "enrichment":      enrichment_meta,
                "ranking":         ranking_meta,
                "steps_completed": [
                    "load_portfolio_data (→ WinrichMFDataAgent)",
                    "enrich_benchmarks (→ MFBenchmarkAgent + IndexAgent)",
                    "enrich_fund_ranks (→ FundRankingAgent)",
                    "calculate_metrics",
                    *([] if params.get("skip_commentary") else ["generate_ai_commentary"]),
                    "generate_pdf_report",
                ],
                "warnings": warnings,
            },
        )


# ── HOW TO SEE DEBUG OUTPUT ───────────────────────────────────────────────────
# Add ONE of the following before calling generate_quarterly_report:
#
#   # Option A — whole module (recommended for debugging ranks)
#   import logging
#   logging.basicConfig(level=logging.DEBUG)
#
#   # Option B — only this agent (quieter)
#   import logging
#   logging.getLogger("agents.mf_portfolio_agent").setLevel(logging.DEBUG)
#   logging.basicConfig()  # ensure a handler exists
#
# The debug output will show, per fund:
#   - raw scheme_category / benchmark_index column values
#   - resolved scheme_type and scheme_category passed to FundRankingAgent
#   - attempt-1 / attempt-2 status and errors
#   - matched CSV fund name and final rank_label
#   - whether Step 2/3 were skipped due to _already_enriched / _already_ranked
# ─────────────────────────────────────────────────────────────────────────────