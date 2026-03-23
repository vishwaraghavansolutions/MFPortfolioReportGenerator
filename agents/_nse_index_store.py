"""
agents/_store.py  

Shared IndexStore — in-process registry of NSE Index Dashboard data.

Imported by both:
  IndexAgent   (owns load_index_files + get_benchmark_values skills)
  MutualFundAgent  (uses it only to cross-reference benchmark names)

The module-level singleton `index_store` is the single source of truth
for all parquet data loaded at runtime.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# ── Column definitions (mirrors app.py) ──────────────────────────────────────
INDEX_RETURN_COLS  = ["return_1m", "return_3m", "return_1yr", "return_3yr", "return_5yr"]
INDEX_RISK_COLS    = ["volatility_1yr", "beta_1yr", "correlation_1yr", "r2_1yr"]
INDEX_VAL_COLS     = ["pe", "pb", "dividend_yield"]
INDEX_ALL_NUM_COLS = INDEX_RETURN_COLS + INDEX_RISK_COLS + INDEX_VAL_COLS

# Suffixes the NSE dashboard appends to index names that we normalise away
_TRI_SUFFIXES = (
    " total return index",
    " tri",
    " price return index",
    " pri",
    " net total return index",
    " net tri",
)


def strip_tri(s: str) -> str:
    """Remove known TRI / PRI suffixes for fuzzy name matching."""
    sl = s.strip().lower()
    for suffix in _TRI_SUFFIXES:
        if sl.endswith(suffix):
            return sl[: -len(suffix)].strip()
    return sl


# ── IndexStore ────────────────────────────────────────────────────────────────

class IndexStore:
    """
    In-process registry of NSE Index Dashboard parquet data.

    Usage
    -----
        from agents._store import index_store          # singleton

        index_store.load("data/")                      # once per session
        df = index_store.query("NIFTY 100 TRI")        # many times
    """

    def __init__(self) -> None:
        # { parquet_path_str: pd.DataFrame }
        self._frames:   Dict[str, pd.DataFrame] = {}
        self._combined: Optional[pd.DataFrame]  = None

    # ── loading ───────────────────────────────────────────────────────────────

    def load(self, parquet_dir: str, force_reload: bool = False) -> Dict[str, Any]:
        """
        Scan *parquet_dir* for *.parquet files and load each into memory.
        Skips the MF benchmark mapping file produced by main.py.

        Returns
        -------
        {
            "loaded"  : ["file1.parquet", ...],   # newly read
            "skipped" : ["file2.parquet", ...],   # already loaded or errored
            "files"   : [all tracked paths]
        }
        """
        pdir    = Path(parquet_dir)
        loaded  : List[str] = []
        skipped : List[str] = []

        for path in sorted(pdir.glob("*.parquet")):
            if "mf_benchmark_map" in path.name:
                continue                            # belongs to MF agent, not us
            key = str(path)
            if key in self._frames and not force_reload:
                skipped.append(path.name)
                continue
            try:
                df = pd.read_parquet(path)
                for col in INDEX_ALL_NUM_COLS:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                self._frames[key] = df
                loaded.append(path.name)
            except Exception as exc:
                skipped.append(f"{path.name} (ERROR: {exc})")

        self._combined = None                       # invalidate merged cache
        return {
            "loaded" : loaded,
            "skipped": skipped,
            "files"  : list(self._frames),
        }

    # ── accessors ─────────────────────────────────────────────────────────────

    @property
    def combined(self) -> pd.DataFrame:
        """All loaded frames concatenated (lazily cached)."""
        if self._combined is None:
            self._combined = (
                pd.concat(list(self._frames.values()), ignore_index=True)
                if self._frames else pd.DataFrame()
            )
        return self._combined

    def is_empty(self) -> bool:
        return not bool(self._frames)

    def index_names(self) -> List[str]:
        df = self.combined
        if df.empty or "index_name" not in df.columns:
            return []
        return sorted(df["index_name"].dropna().unique().tolist())

    def periods(self) -> List[Dict[str, int]]:
        """Return [{year, month}, …] for every period present in the store."""
        df = self.combined
        if df.empty or "year" not in df.columns:
            return []
        return (
            df[["year", "month"]]
            .dropna()
            .drop_duplicates()
            .sort_values(["year", "month"])
            .astype(int)
            .to_dict(orient="records")
        )

    # ── querying ──────────────────────────────────────────────────────────────

    def query(
        self,
        benchmark_name: str,
        *,
        year:   Optional[int] = None,
        month:  Optional[int] = None,
        latest: bool          = True,
    ) -> pd.DataFrame:
        """
        Find rows matching *benchmark_name* using a 4-tier fuzzy strategy:

          1. Exact match          (case-insensitive)
          2. TRI-normalised match (strips Total Return Index / TRI suffix)
          3. Substring match      (benchmark_name inside index_name)
          4. Reverse substring    (index_name inside benchmark_name)

        *year* / *month* narrow the result set when supplied.
        *latest* (default True) returns only the most recent period when
        the same index appears across multiple months.
        """
        df = self.combined
        if df.empty or "index_name" not in df.columns:
            return pd.DataFrame()

        bname_lower = benchmark_name.strip().lower()
        bname_norm  = strip_tri(bname_lower)

        def _norm(s: str) -> str:
            return strip_tri(str(s).strip().lower())

        masks = [
            df["index_name"].str.strip().str.lower() == bname_lower,
            df["index_name"].apply(lambda s: _norm(s) == bname_norm),
            df["index_name"].str.lower().str.contains(bname_lower, na=False, regex=False),
            df["index_name"].apply(lambda s: str(s).strip().lower() in bname_lower),
        ]

        result = pd.DataFrame()
        for mask in masks:
            result = df[mask]
            if not result.empty:
                break

        if result.empty:
            return result

        if year  is not None and "year"  in result.columns:
            result = result[result["year"]  == year]
        if month is not None and "month" in result.columns:
            result = result[result["month"] == month]

        if result.empty:
            return result

        if latest and "year" in result.columns and "month" in result.columns:
            max_year  = result["year"].max()
            max_month = result[result["year"] == max_year]["month"].max()
            result    = result[
                (result["year"] == max_year) & (result["month"] == max_month)
            ]

        return result.reset_index(drop=True)


# ── Module-level singleton ────────────────────────────────────────────────────
# Both agents import this object; whoever calls .load() first populates it.
index_store = IndexStore()