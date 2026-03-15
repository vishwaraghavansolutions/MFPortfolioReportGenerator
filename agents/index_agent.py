"""
agents/index_agent.py

NSE Index Agent
───────────────
Responsible for loading NSE Index Dashboard parquet files and answering
benchmark-value lookups against them.

Skills
──────
  load_index_files      – scan a directory of parquet files and load them into
                          the shared IndexStore
  get_benchmark_values  – retrieve performance / risk / valuation metrics for
                          a named benchmark index from the loaded store

Data source : NSE Index Dashboard PDFs parsed by app.py → *.parquet files.
State       : shared via agents._store.index_store singleton so both agents
              can access the same loaded data within a process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .base   import Agent, AgentResponse, AgentStatus
from ._store import (
    index_store,
    INDEX_ALL_NUM_COLS,
    INDEX_RETURN_COLS,
    INDEX_RISK_COLS,
    INDEX_VAL_COLS,
)

import pandas as _pd


# ══════════════════════════════════════════════════════════════════════════════
# Skill 1 – load_index_files
# ══════════════════════════════════════════════════════════════════════════════

def skill_load_index_files(params: Dict[str, Any]) -> AgentResponse:
    """
    Load NSE Index Dashboard parquet files into the shared index store.

    Must be called once per session (or whenever new files arrive) before
    skill_get_benchmark_values can return data.

    params
    ------
      parquet_dir  : str   – directory containing *.parquet files produced by
                             app.py  (default: "data")
      force_reload : bool  – re-read files already in the store (default: False)

    output
    ------
      {
        "loaded"      : ["Index_Dashboard_JAN2026.parquet", ...],
        "skipped"     : [...],
        "total_files" : 6,
        "total_rows"  : 1248,
        "index_count" : 208,
        "periods"     : [{"year": 2026, "month": 1}, ...]
      }
    """
    parquet_dir  = (params.get("parquet_dir") or "data").strip()
    force_reload = bool(params.get("force_reload", False))

    if not Path(parquet_dir).exists():
        return AgentResponse(
            status=AgentStatus.FAILED,
            error=f"Directory '{parquet_dir}' does not exist.",
        )

    summary = index_store.load(parquet_dir, force_reload=force_reload)

    if index_store.is_empty():
        return AgentResponse(
            status=AgentStatus.FAILED,
            error=(
                f"No index parquet files found in '{parquet_dir}'. "
                "Run app.py to parse NSE Index Dashboard PDFs first."
            ),
        )

    df = index_store.combined

    return AgentResponse(
        status=AgentStatus.SUCCESS,
        output={
            "loaded"      : summary["loaded"],
            "skipped"     : summary["skipped"],
            "total_files" : len(summary["files"]),
            "total_rows"  : len(df),
            "index_count" : len(index_store.index_names()),
            "dataframe"   : df,                    # full combined DataFrame
            "periods"     : index_store.periods(),
        },
        metadata={"parquet_dir": parquet_dir, "force_reload": force_reload},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Skill 2 – get_benchmark_values
# ══════════════════════════════════════════════════════════════════════════════

def skill_get_benchmark_values(params: Dict[str, Any]) -> AgentResponse:
    """
    Retrieve performance, risk, and valuation metrics for a benchmark index.

    Requires skill_load_index_files to have been called first.

    params (required)
    -----------------
      benchmark  : str   – benchmark index name (flexible — see matching notes)
                           e.g. "NIFTY 100 Total Return Index"
                                "NIFTY 100 TRI"
                                "Nifty 100"

    params (optional)
    -----------------
      year       : int   – restrict to a specific year  (default: latest)
      month      : int   – restrict to a specific month (default: latest)
      metrics    : list  – subset of columns to return, e.g.:
                             ["return_1yr", "return_3yr", "pe"]
                           Omit to get all available numeric columns.

    output
    ------
      {
        "benchmark"  : "NIFTY 100 Total Return Index",
        "matched_as" : "Nifty 100",
        "period"     : {"year": 2026, "month": 1},
        "section"    : "Broad Market Indices",
        "metrics": {
          "return_1m"       : 2.45,
          "return_3m"       : 5.12,
          "return_1yr"      : 18.30,
          "return_3yr"      : 12.50,
          "return_5yr"      : 15.20,
          "volatility_1yr"  : 13.40,
          "beta_1yr"        : 1.00,
          "correlation_1yr" : 1.00,
          "r2_1yr"          : 1.00,
          "pe"              : 22.10,
          "pb"              : 3.45,
          "dividend_yield"  : 1.20
        }
      }

    Matching strategy (applied in order, stops at first hit)
    -----------------
      1. Exact match                  "Nifty 100"
      2. TRI-normalised match         "NIFTY 100 Total Return Index" → "nifty 100"
      3. Substring match              "midcap 150" found in "Nifty Midcap 150"
      4. Reverse substring match      "nifty bank" found inside query string
    """
    benchmark = (params.get("benchmark") or "").strip()
    if not benchmark:
        return AgentResponse(
            status=AgentStatus.FAILED,
            error="'benchmark' param is required.",
        )

    if index_store.is_empty():
        return AgentResponse(
            status=AgentStatus.FAILED,
            error=(
                "Index store is empty. "
                "Call skill 'load_index_files' before 'get_benchmark_values'."
            ),
        )

    year    = params.get("year")
    month   = params.get("month")
    metrics = params.get("metrics")

    matched = index_store.query(
        benchmark,
        year   = int(year)  if year  is not None else None,
        month  = int(month) if month is not None else None,
        latest = True,
    )

    if matched.empty:
        available = index_store.index_names()
        return AgentResponse(
            status=AgentStatus.FAILED,
            error=(
                f"No data found for benchmark '{benchmark}'. "
                f"{len(available)} indices are currently loaded — "
                "check 'sample_index_names' in metadata for examples."
            ),
            metadata={"sample_index_names": available[:20]},
        )

    row = matched.iloc[0]

    # Resolve which numeric columns to return
    wanted_cols = [c for c in INDEX_ALL_NUM_COLS if c in matched.columns]
    if metrics:
        wanted_cols = [c for c in metrics if c in matched.columns]

    metric_dict: Dict[str, Any] = {}
    for col in wanted_cols:
        val = row.get(col)
        metric_dict[col] = (
            None if _pd.isna(val) else round(float(val), 4)
        )

    period: Dict[str, int] = {}
    if "year"  in row.index and not _pd.isna(row.get("year")):
        period["year"]  = int(row["year"])
    if "month" in row.index and not _pd.isna(row.get("month")):
        period["month"] = int(row["month"])

    return AgentResponse(
        status=AgentStatus.SUCCESS,
        output={
            "benchmark"  : benchmark,
            "matched_as" : str(row.get("index_name", "")),
            "period"     : period,
            "section"    : str(row.get("section", "")),
            "metrics"    : metric_dict,
        },
        metadata={
            "source_file"                    : str(row.get("source_file", "")),
            "rows_matched_before_period_filter": len(matched),
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# Agent class
# ══════════════════════════════════════════════════════════════════════════════

class IndexAgent(Agent):
    """
    NSE Index Agent — loads index dashboard data and serves benchmark lookups.

    Skills
    ──────
    load_index_files      – populate the index store from a parquet directory
    get_benchmark_values  – look up metrics for a named benchmark index

    Typical usage
    -------------
        agent = IndexAgent()
        agent.run("load_index_files",     {"parquet_dir": "data"})
        agent.run("get_benchmark_values", {"benchmark": "NIFTY 100 TRI"})
    """

    name = "IndexAgent"

    skills = {
        "load_index_files"    : skill_load_index_files,
        "get_benchmark_values": skill_get_benchmark_values,
    }

    @property
    def store(self):
        """Return the IndexStore singleton this agent reads from and writes to."""
        return index_store