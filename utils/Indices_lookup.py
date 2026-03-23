"""
scheme_lookup.py 
----------------
A simple class that:
  1. Takes a string → fuzzy-matches Scheme Name in SchemeData CSV
  2. Reads Scheme Category from the matched row
  3. Looks up category_index_mapping.json → gets the first mapped index
  4. Reads Index_Dashboard parquet → returns index return values

Usage
-----
    from scheme_lookup import SchemeLookup

    lookup = SchemeLookup()
    result = lookup.find("axis midcap")
    print(result)

    # Or with strict / closest-match control
    result = lookup.find("hdfc small cap", top_match_only=True)
"""

import json
import re
import glob
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ── Default file locations ─────────────────────────────────────────────────────
_ROOT        = Path(__file__).resolve().parent.parent
SCHEME_CSV   = _ROOT / "data" / "SchemeData2301262313SS.csv"
MAPPING_JSON = _ROOT / "data" / "category_index_mapping.json"
PARQUET_DIR  = _ROOT / "data"


# ── Result dataclass ───────────────────────────────────────────────────────────
@dataclass
class LookupResult:
    query:           str
    scheme_name:     str
    scheme_type:     str
    scheme_category: str
    index_name:      str
    return_1m:       Optional[float]
    return_3m:       Optional[float]
    return_1yr:      Optional[float]
    return_3yr:      Optional[float]
    return_5yr:      Optional[float]

    def as_dict(self) -> dict:
        return {
            "query":           self.query,
            "scheme_name":     self.scheme_name,
            "scheme_type":     self.scheme_type,
            "scheme_category": self.scheme_category,
            "index_name":      self.index_name,
            "return_1m":       self.return_1m,
            "return_3m":       self.return_3m,
            "return_1yr":      self.return_1yr,
            "return_3yr":      self.return_3yr,
            "return_5yr":      self.return_5yr,
        }

    def __str__(self) -> str:
        returns = (
            f"1M={self.return_1m}%  "
            f"3M={self.return_3m}%  "
            f"1Y={self.return_1yr}%  "
            f"3Y={self.return_3yr}%  "
            f"5Y={self.return_5yr}%"
        )
        return (
            f"Query          : {self.query}\n"
            f"Scheme Name    : {self.scheme_name}\n"
            f"Scheme Type    : {self.scheme_type}\n"
            f"Scheme Category: {self.scheme_category}\n"
            f"Benchmark Index: {self.index_name}\n"
            f"Returns        : {returns}"
        )


# ── Main class ─────────────────────────────────────────────────────────────────
class SchemeLookup:
    """
    Looks up a mutual fund by name and returns its benchmark index returns.

    Parameters
    ----------
    scheme_csv   : Path to SchemeData CSV  (default: data/SchemeData2301262313SS.csv)
    mapping_json : Path to category→index mapping JSON  (default: data/category_index_mapping.json)
    parquet_dir  : Directory containing NSE index parquet files  (default: data/)
    """

    def __init__(
        self,
        scheme_csv:   Path = SCHEME_CSV,
        mapping_json: Path = MAPPING_JSON,
        parquet_dir:  Path = PARQUET_DIR,
    ):
        self._scheme_df   = self._load_scheme(Path(scheme_csv))
        self._mapping     = self._load_mapping(Path(mapping_json))
        self._idx_df      = self._load_indices(Path(parquet_dir))

    # ── Loaders ───────────────────────────────────────────────────────────────

    @staticmethod
    def _load_scheme(path: Path) -> pd.DataFrame:
        print(f"[INFO] Loading scheme data from: {path}")
        if not path.exists():
            raise FileNotFoundError(f"Scheme CSV not found: {path}")
        df = pd.read_csv(path)
        # Deduplicate Scheme Name → keep one row per unique name
        return df.drop_duplicates(subset=["Scheme Name"]).reset_index(drop=True)

    @staticmethod
    def _load_mapping(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(
                f"Mapping JSON not found: {path}\n"
                "Run category_mapping.py to create it."
            )
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _load_indices(parquet_dir: Path) -> pd.DataFrame:
        files = sorted(
            p for p in parquet_dir.glob("*.parquet")
            if "mapping" not in p.name
        )
        if not files:
            raise FileNotFoundError(
                f"No index parquet files found in {parquet_dir}\n"
                "Run app.py to generate them from the NSE PDF."
            )
        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames, ignore_index=True)

        for col in ["return_1m", "return_3m", "return_1yr", "return_3yr", "return_5yr"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # If multiple months present, keep the most recent per index
        if {"year", "month"}.issubset(df.columns):
            df = (
                df.sort_values(["year", "month"], ascending=False)
                  .drop_duplicates("index_name")
            )

        return df.reset_index(drop=True)

    # ── Core lookup ───────────────────────────────────────────────────────────

    def find(self, query: str) -> LookupResult:
        """
        Look up a scheme by name string and return its benchmark index returns.

        Parameters
        ----------
        query : str
            Partial or full scheme name, case-insensitive.
            e.g. "axis midcap", "HDFC Small Cap", "SBI Large"

        Returns
        -------
        LookupResult
            Dataclass with scheme info + index return values.

        Raises
        ------
        ValueError  if no scheme, mapping, or index data is found.
        """
        # ── Step 1: match Scheme Name ─────────────────────────────────────────
        scheme_row = self._match_scheme(query)

        scheme_name     = scheme_row["Scheme Name"]
        scheme_type     = scheme_row["Scheme Type"]
        scheme_category = scheme_row["Scheme Category"]

        # ── Step 2: look up mapping ───────────────────────────────────────────
        index_name = self._resolve_index(scheme_type, scheme_category)

        # ── Step 3: fetch index returns from parquet ──────────────────────────
        returns = self._fetch_returns(index_name)

        return LookupResult(
            query           = query,
            scheme_name     = scheme_name,
            scheme_type     = scheme_type,
            scheme_category = scheme_category,
            index_name      = index_name,
            return_1m       = returns.get("return_1m"),
            return_3m       = returns.get("return_3m"),
            return_1yr      = returns.get("return_1yr"),
            return_3yr      = returns.get("return_3yr"),
            return_5yr      = returns.get("return_5yr"),
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _match_scheme(self, query: str) -> pd.Series:
        """
        Return the best-matching scheme row for the query string.
        Tries exact match first, then case-insensitive substring match.
        Raises ValueError if nothing found.
        """
        # Exact match (case-insensitive)
        exact = self._scheme_df[
            self._scheme_df["Scheme Name"].str.lower() == query.lower()
        ]
        if not exact.empty:
            return exact.iloc[0]

        # Substring match — all query words must appear
        words   = [w for w in re.split(r"\s+", query.strip()) if w]
        pattern = "".join(f"(?=.*{re.escape(w)})" for w in words)
        subset  = self._scheme_df[
            self._scheme_df["Scheme Name"].str.contains(
                pattern, case=False, na=False, regex=True
            )
        ]

        if subset.empty:
            raise ValueError(
                f"No scheme found matching '{query}'.\n"
                f"Tip: try a partial name like 'axis midcap' or 'hdfc small'."
            )

        # Return the shortest name match (most specific)
        return subset.loc[subset["Scheme Name"].str.len().idxmin()]

    def _resolve_index(self, scheme_type: str, scheme_category: str) -> str:
        """
        Look up the first mapped index for (scheme_type, scheme_category).
        Raises ValueError if no mapping exists.
        """
        key     = f"{scheme_type}::{scheme_category}"
        indices = self._mapping.get(key, [])

        if not indices:
            raise ValueError(
                f"No index mapped for category '{scheme_category}' "
                f"(scheme type: '{scheme_type}').\n"
                f"Open category_mapping.py and add a mapping for this category."
            )

        return indices[0]   # first mapped index

    def _fetch_returns(self, index_name: str) -> dict:
        """
        Fetch return columns for the given index from the parquet DataFrame.
        Raises ValueError if the index is not found.
        """
        row = self._idx_df[self._idx_df["index_name"] == index_name]

        if row.empty:
            raise ValueError(
                f"Index '{index_name}' not found in the parquet data.\n"
                f"Check that the NSE PDF has been parsed correctly."
            )

        r = row.iloc[0]

        def _val(col: str) -> Optional[float]:
            v = r.get(col)
            return None if pd.isna(v) else round(float(v), 2)

        return {
            "return_1m":  _val("return_1m"),
            "return_3m":  _val("return_3m"),
            "return_1yr": _val("return_1yr"),
            "return_3yr": _val("return_3yr"),
            "return_5yr": _val("return_5yr"),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Quick test — python scheme_lookup.py
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    lookup = SchemeLookup()

    test_queries = [
        "axis midcap",
        "HDFC Small Cap",
        "JM Large Cap",
        "Aditya Birla Large Mid Cap",
        "SBI Flexi Cap",
        "Kotak Multicap",
    ]

    print("=" * 60)
    print("SchemeLookup — test run")
    print("=" * 60)

    for q in test_queries:
        print(f"\n{'─'*60}")
        try:
            result = lookup.find(q)
            print(result)
        except ValueError as e:
            print(f"❌  {e}")