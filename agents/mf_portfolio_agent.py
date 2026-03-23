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
        ├── chMFPortfolioPDFGenerator     (PDF rendering)
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
import calendar
from datetime import datetime, date as date_type
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from agents.base import Agent, AgentResponse, AgentStatus
from agents.winrich_mf_data_agent import WinrichMFDataAgent
from agents.mf_benchmark_agent import MutualFundBenchmarkAgent
from agents.index_agent import IndexAgent
from agents.mf_funds_ranking_agent import FundRankingAgent
from agents.gcs_storage_agent import GCSStorageAgent
from agents.nse_benchmark_agent import NSEBenchmarkAgent, BROAD_MARKET_INDICES

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

# Always write full untruncated debug output to a log file regardless of
# whether Streamlit has already configured the root logger.
_log_file = "mf_portfolio_agent.log"
_fh = logging.FileHandler(_log_file, mode="w", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.root.addHandler(_fh)

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

# Calendar-day lookback for each return column
_LOOKBACK_DAYS: Dict[str, int] = {
    "benchmark_return_1m":  30,
    "benchmark_return_3m":  90,
    "benchmark_return_1yr": 365,
    "benchmark_return_3yr": 1095,
    "benchmark_return_5yr": 1825,
}

# Possible column names for date / closing value in the NSE index CSV
_NSE_DATE_COLS  = ["Index Date", "Date", "date"]
_NSE_CLOSE_COLS = ["Closing Index Value", "Close", "Closing Value", "close"]

# If the nearest available past-date is more than this many days before the
# expected lookback date, the data has a gap (e.g. an entire year is missing
# from GCS) and the return is misleading — return None instead.
_MAX_PAST_DATE_GAP = 45  # calendar days

# Suffixes that SEBI appends to benchmark names but NSE index names don't have
_SEBI_SUFFIXES = (
    " TOTAL RETURN INDEX", " TRI", " TOTAL RETURN",
    " PRICE RETURN INDEX", " PRI", " INDEX",
)


def _normalize_to_nse_index(benchmark: str) -> Optional[str]:
    """
    Map a SEBI benchmark name to the nearest entry in BROAD_MARKET_INDICES.
    Returns None if no match is found.

    Examples
    --------
    "Nifty 50 TRI"                   → "NIFTY 50"
    "NIFTY MIDCAP 150 Total Return"  → "NIFTY MIDCAP 150"
    "S&P BSE SENSEX"                 → None
    """
    if not benchmark:
        return None
    name = benchmark.upper().strip()
    for suffix in _SEBI_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    # Normalise spacing variants: "LARGE MIDCAP" → "LARGEMIDCAP", "SMALL CAP" → "SMALLCAP" etc.
    name = re.sub(r"\b(LARGE|SMALL|MULTI|FLEXI)\s+(MIDCAP|CAP)\b", r"\1\2", name)
    # 1. Exact match
    if name in BROAD_MARKET_INDICES:
        return name
    # 2. One contains the other (handles minor spacing/wording differences)
    for idx in BROAD_MARKET_INDICES:
        if idx in name or name in idx:
            return idx
    return None


def _find_col(df: "pd.DataFrame", candidates: List[str]) -> Optional[str]:
    """Return the first candidate column name that exists in df."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _calc_return(
    nse_df: "pd.DataFrame",
    index_name: str,
    as_of_date: "date_type",
    lookback_days: int,
) -> Optional[float]:
    """
    Return the % return for *index_name* over *lookback_days* calendar days
    ending on *as_of_date*. Nearest prior trading day used for both endpoints.

    return = (close_as_of_date − close_lookback) / close_lookback × 100
    """
    import pandas as pd

    sub = nse_df[nse_df["index_name"] == index_name]
    if sub.empty:
        logger.warning(
            "_calc_return: index %r not found in nse_df. "
            "Available indices: %s",
            index_name,
            list(nse_df["index_name"].unique()) if "index_name" in nse_df.columns else "no index_name col",
        )
        return None
    sub = sub.copy()

    date_col  = _find_col(sub, _NSE_DATE_COLS)
    close_col = _find_col(sub, _NSE_CLOSE_COLS)
    logger.debug("_calc_return(%r, %dd): date_col=%r  close_col=%r", index_name, lookback_days, date_col, close_col)
    if date_col is None or close_col is None:
        logger.warning(
            "_calc_return: could not find date/close columns. cols=%s",
            list(sub.columns),
        )
        return None

    sub[date_col]  = pd.to_datetime(sub[date_col], dayfirst=True, errors="coerce")
    sub[close_col] = pd.to_numeric(sub[close_col].astype(str).str.replace(",", ""), errors="coerce")
    sub = sub.dropna(subset=[date_col, close_col]).sort_values(date_col)

    as_of_ts    = pd.Timestamp(as_of_date)
    lookback_ts = as_of_ts - pd.Timedelta(days=lookback_days)

    data_min = sub[date_col].min()
    data_max = sub[date_col].max()

    recent = sub[sub[date_col] <= as_of_ts]
    past   = sub[sub[date_col] <= lookback_ts]

    if recent.empty:
        logger.warning(
            "_calc_return(%r, lookback=%dd): no rows ≤ as_of %s. data range %s → %s",
            index_name, lookback_days, as_of_ts.date(), data_min.date() if pd.notna(data_min) else "?", data_max.date() if pd.notna(data_max) else "?",
        )
        return None

    if past.empty:
        logger.warning(
            "_calc_return(%r, lookback=%dd): no rows ≤ %s (need data from ~%s). "
            "Earliest available: %s. Download that year's index data to GCS.",
            index_name, lookback_days, lookback_ts.date(),
            lookback_ts.date(), data_min.date() if pd.notna(data_min) else "?",
        )
        return None

    close_now  = float(recent.iloc[-1][close_col])
    close_past = float(past.iloc[-1][close_col])
    recent_date = recent.iloc[-1][date_col]
    past_date   = past.iloc[-1][date_col]

    # Guard against data gaps: if the nearest available past date is far
    # before the expected lookback date, an entire year is missing from GCS
    # and the result would silently reuse a stale value (e.g. both 3M and
    # 1YR landing on the same 2024-12-31 row when 2025 data is absent).
    gap_days = (lookback_ts - pd.Timestamp(past_date)).days
    if gap_days > _MAX_PAST_DATE_GAP:
        logger.warning(
            "_calc_return(%r, lookback=%dd): past date %s is %d days before "
            "expected %s — data gap detected. "
            "Download %d index data to GCS and retry.",
            index_name, lookback_days,
            pd.Timestamp(past_date).date(), gap_days, lookback_ts.date(),
            lookback_ts.year,
        )
        return None

    logger.debug(
        "_calc_return(%r, lookback=%dd): close_now=%.2f on %s, close_past=%.2f on %s",
        index_name, lookback_days,
        close_now, pd.Timestamp(recent_date).date(),
        close_past, pd.Timestamp(past_date).date(),
    )

    if pd.isna(close_now) or pd.isna(close_past):
        return None

    if close_past == 0:
        return None

    actual_days = (pd.Timestamp(recent_date) - pd.Timestamp(past_date)).days
    if actual_days <= 0:
        return None

    if lookback_days < 365:
        # Absolute point-to-point return for sub-1-year periods (1M, 3M)
        result = (close_now - close_past) / close_past * 100
    else:
        # CAGR for 1Y, 3Y, 5Y — annualised using actual calendar days
        years  = actual_days / 365.0
        result = ((close_now / close_past) ** (1.0 / years) - 1) * 100

    logger.debug(
        "_calc_return(%r, lookback=%dd, actual=%dd): %.2f → %.2f  %s = %.2f%%",
        index_name, lookback_days, actual_days,
        close_past, close_now,
        "CAGR" if lookback_days >= 365 else "abs",
        result,
    )
    return round(result, 2)


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
        nse_agent:       Optional[NSEBenchmarkAgent]           = None,
    ):
        self._data_agent      = data_agent
        self._benchmark_agent = benchmark_agent
        self._nse_agent       = nse_agent
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

    def _get_nse_agent(self) -> NSEBenchmarkAgent:
        if self._nse_agent is None:
            self._nse_agent = NSEBenchmarkAgent()
        return self._nse_agent

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
        For every fund row:
          1. Call MFBenchmarkAgent.get_fund_benchmark(scheme_code) → benchmark name
          2. Normalize the SEBI benchmark name → NSE Broad Market Index name
          3. Load {year}_Index_Master.csv from GCS (winrich_shared/Master)
          4. Calculate return columns from the index closing values:
               return = (close_as_of_date − close_N_business_days_ago) / close_N × 100
             where N = 30 / 90 / 365 / 1095 / 1825 calendar days (nearest prior
             trading day used for both endpoints).

        Required params
        ---------------
          customer_df  : pd.DataFrame

        Optional params
        ---------------
          as_of_date   : datetime | date | str  – passed from portfolio_agent
                         (falls back to year/month, then today)
          year, month  : int   – used only when as_of_date is absent

        Output keys
        -----------
          customer_df        : pd.DataFrame  (original + new benchmark columns)
          enrichment_summary : dict
        """
        customer_df: pd.DataFrame = params.get("customer_df")
        if customer_df is None:
            return AgentResponse(AgentStatus.FAILED, error="'customer_df' is required")

        # ── Determine as-of date (prefer explicit param, then year/month, then today)
        raw_as_of = params.get("as_of_date")
        if raw_as_of is not None:
            try:
                as_of_date = pd.Timestamp(raw_as_of).date()
            except Exception:
                as_of_date = date_type.today()
        else:
            year  = params.get("year")
            month = params.get("month")
            if year and month:
                last_day   = calendar.monthrange(int(year), int(month))[1]
                as_of_date = date_type(int(year), int(month), last_day)
            else:
                as_of_date = date_type.today()

        # ── Load NSE index data from GCS (all years needed for 5yr lookback) ──
        years_needed = list(range(as_of_date.year - 5, as_of_date.year + 1))
        gcs_agent    = self._get_gcs_agent()
        nse_dfs: List[pd.DataFrame] = []
        nse_load_errors: List[str]  = []

        for yr in years_needed:
            resp = gcs_agent.run("load_ranking_csv", {
                "filename":    f"{yr}_Index_Master.csv",
                "bucket_name": "winrich_shared",
                "prefix":      "Master",
            })
            if resp.status == AgentStatus.SUCCESS:
                nse_dfs.append(resp.output["dataframe"])
            else:
                nse_load_errors.append(f"{yr}: {resp.error}")

        nse_df: Optional[pd.DataFrame] = (
            pd.concat(nse_dfs, ignore_index=True) if nse_dfs else None
        )
        if nse_load_errors:
            logger.warning(
                "[enrich_benchmarks] Missing NSE index files in GCS (%d/%d years): %s. "
                "Run NSEBenchmarkAgent.download_index_data for those years to populate GCS.",
                len(nse_load_errors), len(years_needed), nse_load_errors,
            )

        # ── Per-fund enrichment ───────────────────────────────────────────────
        benchmark_agent = self._get_benchmark_agent()
        enriched_rows: List[dict]       = []
        summary:       Dict[str, Any]   = {}

        logger.debug(
            "[enrich_benchmarks] as_of_date=%s  nse_data=%s rows=%d  funds=%d",
            as_of_date,
            "loaded" if nse_df is not None else "MISSING",
            len(nse_df) if nse_df is not None else 0,
            len(customer_df),
        )
        logger.debug(
            "[enrich_benchmarks] fund list: %s",
            [str(r.get("s_name", "")) for _, r in customer_df.iterrows()],
        )

        _FORMERLY_RE = re.compile(r"\s*\(formerly[^)]*\)", re.IGNORECASE)

        for _, row in customer_df.iterrows():
            fund_name = _FORMERLY_RE.sub("", str(row.get("s_name", ""))).strip()

            logger.debug("[enrich_benchmarks] ── fund: %r", fund_name)

            outcome: Dict[str, Any] = {
                "fund":           fund_name,
                "benchmark_name": None,
                "nse_index":      None,
                "source":         "N/A",
                "error":          None,
            }

            benchmark_name:  Optional[str]   = None
            scheme_category: Optional[str]   = None
            nse_index_name:  Optional[str]   = None
            found_in_map:    bool            = False
            return_values: Dict[str, Optional[float]] = {c: None for c in _BENCHMARK_RETURN_COLS}

            # Step 1 – look up SEBI benchmark by fund name via mf_benchmark_agent
            if fund_name:
                try:
                    bm_resp = benchmark_agent.run(
                        "lookup_benchmark_by_name",
                        {"fund_name": fund_name, "exact": False},
                    )
                    if bm_resp.status == AgentStatus.SUCCESS:
                        matches = bm_resp.output.get("matches") or []
                        if matches:
                            found_in_map = True
                            # Exclude IDCW variants; keep only Growth rows
                            growth_matches = [
                                m for m in matches
                                if "idcw" not in str(m.get("scheme_name") or "").lower()
                            ]
                            candidates = growth_matches if growth_matches else matches

                            # Among candidates, prefer the one whose scheme_name
                            # best matches fund_name by word overlap (strip plan suffixes first)
                            _SUFFIX_RE = re.compile(
                                r"\s*[-–]\s*(regular|direct)\s*(plan|growth)?\s*$"
                                r"|\s*[-–]\s*(growth|idcw|dividend)\s*(option|plan)?\s*$",
                                re.IGNORECASE,
                            )
                            fn_clean = _SUFFIX_RE.sub("", fund_name).strip()
                            fn_words = set(re.split(r"[\s\-]+", fn_clean.lower()))
                            def _similarity(m: dict) -> tuple:
                                has_bm = 0 if not str(m.get("benchmark") or "").strip() or str(m.get("benchmark") or "").lower().startswith("benchmark not mapped") else 1
                                sn = re.sub(r"[\s\-]+", " ", str(m.get("scheme_name") or "")).lower()
                                sn_words = set(sn.split())
                                return (has_bm, len(fn_words & sn_words))
                            best = max(candidates, key=_similarity)

                            raw_bm = str(best.get("benchmark") or "").strip()
                            # Treat _derive_benchmark sentinel values as unmapped
                            if raw_bm.lower().startswith("benchmark not mapped"):
                                raw_bm = ""
                            benchmark_name  = raw_bm or None
                            scheme_category = str(best.get("scheme_category") or "").strip() or None
                            outcome["benchmark_name"] = benchmark_name
                            logger.debug(
                                "[enrich_benchmarks]   step1 OK  matched=%r  benchmark=%r  category=%r",
                                best.get("scheme_name"), benchmark_name, scheme_category,
                            )
                        else:
                            outcome["error"] = f"lookup_benchmark_by_name: no matches for '{fund_name}'"
                            logger.debug("[enrich_benchmarks] %s  step1 FAIL: no matches", fund_name)
                    else:
                        outcome["error"] = f"MFBenchmarkAgent: {bm_resp.error}"
                        logger.debug("[enrich_benchmarks] %s  step1 FAIL: %s", fund_name, bm_resp.error)
                except Exception as exc:
                    outcome["error"] = f"MFBenchmarkAgent exception: {exc}"
                    logger.debug("[enrich_benchmarks] %s  step1 EXC: %s", fund_name, exc)

            # Step 2 – normalize to NSE index name
            if benchmark_name:
                nse_index_name = _normalize_to_nse_index(benchmark_name)
                outcome["nse_index"] = nse_index_name
                logger.debug(
                    "[enrich_benchmarks] %s  benchmark=%r → nse_index=%r",
                    fund_name, benchmark_name, nse_index_name,
                )
            else:
                logger.debug("[enrich_benchmarks] %s  no benchmark found", fund_name)

            # Step 3 – calculate returns from NSE index data
            # Each period is only calculated when the folio is old enough for it.
            if nse_index_name and nse_df is not None:
                try:
                    # Parse folio start date from customer_df
                    folio_raw = row.get("FolioStartDate")
                    folio_date: Optional[date_type] = None
                    if folio_raw is not None:
                        try:
                            folio_date = pd.Timestamp(folio_raw).date()
                        except Exception:
                            pass
                    for col in _BENCHMARK_RETURN_COLS:
                        lookback = _LOOKBACK_DAYS[col]
                        # Skip this period if the folio is younger than the lookback
                        if folio_date is not None:
                            min_start = (pd.Timestamp(as_of_date) - pd.Timedelta(days=lookback)).date()
                            if folio_date > min_start:
                                return_values[col] = None
                                logger.debug(
                                    "[enrich_benchmarks]   step3 %s SKIP  folio %s > min_start %s",
                                    col, folio_date, min_start,
                                )
                                continue
                        val = _calc_return(nse_df, nse_index_name, as_of_date, lookback)
                        return_values[col] = val
                        logger.debug(
                            "[enrich_benchmarks]   step3 %s = %s  (lookback=%dd)",
                            col, val, lookback,
                        )
                    outcome["source"] = "MFBenchmarkAgent+NSEBenchmarkAgent"
                except Exception as exc:
                    outcome["error"] = (
                        (outcome["error"] or "") + f" | NSE calc: {exc}"
                    )
                    logger.warning("[enrich_benchmarks] %s  step3 EXC  %s", fund_name, exc)
            else:
                logger.debug(
                    "[enrich_benchmarks] %s  step3 SKIP  nse_index=%r  nse_df=%s",
                    fund_name, nse_index_name, "present" if nse_df is not None else "MISSING",
                )

            # Build enriched row — preserve pre-populated values from
            # WinrichMFDataAgent when live lookup fails.
            enriched_row = row.to_dict()
            if benchmark_name is not None:
                enriched_row["benchmark_index"] = re.sub(
                    r"\bTotal Return Index\b", "TRI", benchmark_name, flags=re.IGNORECASE
                )
            elif found_in_map:
                # Fund exists in map but has no benchmark — clear the category-level fallback.
                enriched_row["benchmark_index"] = None
            # else: not in map → preserve SchemeLookup's category JSON value as fallback
            if scheme_category is not None:
                enriched_row["scheme_category"] = scheme_category
            elif "scheme_category" not in enriched_row:
                enriched_row["scheme_category"] = None
            enriched_row["benchmark_source"]  = outcome["source"]
            enriched_row["nse_index_name"]    = nse_index_name
            # Always overwrite with NSE-computed values (or None → '-' in PDF).
            # Never fall back to pre-populated WinrichMFDataAgent values.
            for col in _BENCHMARK_RETURN_COLS:
                enriched_row[col] = return_values[col]

            logger.debug(
                "[enrich_benchmarks]   result  source=%r  returns=%s",
                outcome["source"],
                {c: return_values[c] for c in _BENCHMARK_RETURN_COLS},
            )

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
        unresolved = len(summary) - resolved

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "customer_df":        enriched_df,
                "enrichment_summary": summary,
            },
            metadata={
                "total_funds":      len(enriched_rows),
                "resolved":         resolved,
                "unresolved":       unresolved,
                "as_of_date":       str(as_of_date),
                "nse_data_loaded":  nse_df is not None,
                "nse_rows":         len(nse_df) if nse_df is not None else 0,
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

            logger.debug("[enrich_fund_ranks] %s  category=%r", fund_name, scheme_category)

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
            elif not scheme_type:
                outcome["error"] = "empty scheme_type"
            elif not scheme_category:
                outcome["error"] = "scheme_category could not be resolved"
            else:
                # Attempt 1: raw fund name
                resp = ranking_agent.run("get_fund_rank", {
                    "fund_name":       fund_name,
                    "scheme_type":     scheme_type,
                    "scheme_category": scheme_category,
                })

                # Attempt 2: cleaned fund name (strip plan/growth suffixes)
                if resp.status != AgentStatus.SUCCESS:
                    cleaned = _clean_fund_name(fund_name)
                    if cleaned != fund_name:
                        resp = ranking_agent.run("get_fund_rank", {
                            "fund_name":       cleaned,
                            "scheme_type":     scheme_type,
                            "scheme_category": scheme_category,
                        })

                if resp.status == AgentStatus.SUCCESS:
                    winrich_rank           = resp.output.get("rank_label", "N/A")
                    outcome["rank_label"]  = winrich_rank
                    outcome["rank"]        = resp.output.get("rank")
                    outcome["max_rank"]    = resp.output.get("max_rank")
                    outcome["source"]      = resp.output.get("source", "ranking_file")
                    logger.debug("[enrich_fund_ranks] %s  → rank=%s", fund_name, winrich_rank)
                else:
                    outcome["error"] = resp.error
                    logger.debug("[enrich_fund_ranks] %s  → NO MATCH: %s", fund_name, resp.error)

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
            _abs = row.get("absReturn")
            abs_return = float(_abs) if pd.notna(_abs) and _abs is not None else ((gain / inv * 100) if inv > 0 else 0)
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