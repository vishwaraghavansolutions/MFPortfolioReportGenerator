"""
agents/mf_benchmark_agent.py

Mutual Fund Benchmark Agent  (MutualFundBenchmarkAgent)
────────────────────────────────────────────────────────
All skills are registered directly in the class body.

CSV skills (load from GCS winrich_shared/master/mf_benchmark_map.csv; FAIL if absent):
  1  get_amc_list
  2  get_funds_by_amc
  3  get_fund_benchmark
  4  get_scheme_classification    — returns scheme_type + scheme_category for ranking
  5  lookup_benchmark_by_name     — CSV first, live fallback removed
  6  get_asset_classes
  7  get_fund_types
  8  get_funds_by_filter          — includes benchmark_source per fund
  9  update_benchmark             — single or bulk edit by scheme_code
  10 get_fund_type_summary        — one row per fund type, benchmark uniformity
  11 update_benchmark_by_fund_type — set one benchmark for a whole fund type

Live mfapi skills (hit mfapi.in directly; use sparingly):
  12 get_amc_list_from_mfapi
  13 get_funds_by_amc_from_mfapi
  14 get_fund_benchmark_from_mfapi
  15 get_scheme_classification_from_mfapi

Admin / rebuild skill:
  16 rebuild_benchmark_master     — fetches all schemes from mfapi, writes
                                    data/mf_benchmark_map.csv locally and uploads
                                    to GCS winrich_shared/master (slow; run once)
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .base import Agent, AgentResponse, AgentStatus
from .gcs_storage_agent import GCSStorageAgent

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL             = "https://api.mfapi.in"
ALL_MF_URL           = f"{BASE_URL}/mf"
SCHEME_URL           = f"{BASE_URL}/mf/{{code}}"
TIMEOUT              = 15
DEFAULT_MAPPING_FILE = Path("data/mf_benchmark_map.csv")

# GCS coordinates for the benchmark master CSV
_DEFAULT_MASTER_BUCKET   = "winrich_shared"
_DEFAULT_MASTER_PREFIX   = "Master"
_DEFAULT_MASTER_FILENAME = "mf_funds_master.csv"

# ── Lazy GCSStorageAgent accessor ─────────────────────────────────────────────
_gcs_agent_instance: Optional[GCSStorageAgent] = None

def _get_gcs_agent() -> GCSStorageAgent:
    global _gcs_agent_instance
    if _gcs_agent_instance is None:
        _gcs_agent_instance = GCSStorageAgent()
    return _gcs_agent_instance

_MAP_COLS = [
    "scheme_code", "scheme_name", "fund_house", "scheme_type",
    "scheme_category", "benchmark", "latest_nav", "nav_date",
]

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, params: Optional[dict] = None) -> Any:
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()

def _all_schemes() -> List[Dict]:
    return _get(ALL_MF_URL)

# ── Mapping-file cache ────────────────────────────────────────────────────────

_mapping_df_cache: Optional[Any] = None

def _load_mapping(mapping_file: Optional[str] = None) -> Optional[Any]:
    """Load the benchmark mapping CSV from GCS (primary) or a local override path."""
    global _mapping_df_cache
    if _mapping_df_cache is not None:
        return _mapping_df_cache
    try:
        import pandas as pd
        if mapping_file:
            # Explicit local file override (e.g. for testing)
            path = Path(mapping_file)
            if not path.exists():
                return None
            df = pd.read_csv(path)
        else:
            # Primary source: GCS
            resp = _get_gcs_agent().run("load_ranking_csv", {
                "filename":    _DEFAULT_MASTER_FILENAME,
                "bucket_name": _DEFAULT_MASTER_BUCKET,
                "prefix":      _DEFAULT_MASTER_PREFIX,
            })
            if resp.status != AgentStatus.SUCCESS:
                return None
            df = resp.output["dataframe"]
        for col in ("scheme_name", "fund_house", "scheme_category", "benchmark"):
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str)
        _mapping_df_cache = df
        return df
    except Exception:
        return None

def _invalidate_mapping_cache() -> None:
    global _mapping_df_cache
    _mapping_df_cache = None

def _require_df(mapping_file: Optional[str] = None):
    df = _load_mapping(mapping_file)
    if df is None:
        raise FileNotFoundError(
            "mf_benchmark_map.csv not found in GCS. Run rebuild_benchmark_master first."
        )
    return df

def _save_and_upload_csv(df: Any, mapping_path: Path) -> str:
    """Save df to a local CSV and upload it to GCS. Returns the gcs_uri."""
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(mapping_path, index=False)
    resp = _get_gcs_agent().run("upload_csv", {
        "file_path":   str(mapping_path),
        "filename":    _DEFAULT_MASTER_FILENAME,
        "bucket_name": _DEFAULT_MASTER_BUCKET,
        "prefix":      _DEFAULT_MASTER_PREFIX,
    })
    if resp.status != AgentStatus.SUCCESS:
        raise RuntimeError(f"GCS upload failed: {resp.error}")
    return resp.output["gcs_uri"]

# ── SEBI benchmark derivation ─────────────────────────────────────────────────

_BENCHMARK_MAP: List[tuple] = [
    # Equity
    ("Large Cap Fund",                           "NIFTY 100 Total Return Index"),
    ("Large & Mid Cap Fund",                     "NIFTY Large Midcap 250 Total Return Index"),
    ("Mid Cap Fund",                             "NIFTY Midcap 150 Total Return Index"),
    ("Small Cap Fund",                           "NIFTY Smallcap 250 Total Return Index"),
    ("Multi Cap Fund",                           "NIFTY 500 Multicap 50:25:25 Total Return Index"),
    ("Flexi Cap Fund",                           "NIFTY 500 Total Return Index"),
    ("Focused Fund",                             "NIFTY 500 Total Return Index"),
    ("Value Fund",                               "NIFTY 500 Total Return Index"),
    ("Contra Fund",                              "NIFTY 500 Total Return Index"),
    ("Dividend Yield Fund",                      "NIFTY Dividend Opportunities 50 Total Return Index"),
    ("Sectoral / Thematic",                      "NIFTY 500 Total Return Index"),
    ("Sectoral/Thematic",                        "NIFTY 500 Total Return Index"),
    ("ELSS",                                     "NIFTY 500 Total Return Index"),
    # Hybrid
    ("Aggressive Hybrid Fund",                   "NIFTY 50 Hybrid Composite Debt 75:25 Index"),
    ("Conservative Hybrid Fund",                 "NIFTY 50 Hybrid Composite Debt 15:85 Index"),
    ("Balanced Hybrid Fund",                     "NIFTY 50 Hybrid Composite Debt 50:50 Index"),
    ("Dynamic Asset Allocation",                 "NIFTY 50 Hybrid Composite Debt 50:50 Index"),
    ("Balanced Advantage Fund",                  "NIFTY 50 Hybrid Composite Debt 50:50 Index"),
    ("Multi Asset Allocation",                   "NIFTY 500 Total Return Index"),
    ("Arbitrage Fund",                           "Nifty 50 Arbitrage Index"),
    ("Equity Savings Fund",                      "NIFTY Equity Savings Index"),
    # Debt
    ("Overnight Fund",                           "NIFTY 1D Rate Index"),
    ("Liquid Fund",                              "NIFTY Liquid Index A-I"),
    ("Ultra Short Duration Fund",                "NIFTY Ultra Short Duration Debt Index"),
    ("Low Duration Fund",                        "NIFTY Low Duration Debt Index"),
    ("Money Market Fund",                        "NIFTY Money Market Index"),
    ("Short Duration Fund",                      "NIFTY Short Duration Debt Index"),
    ("Medium Duration Fund",                     "NIFTY Medium Duration Debt Index"),
    ("Medium to Long Duration Fund",             "NIFTY Medium to Long Duration Debt Index"),
    ("Long Duration Fund",                       "NIFTY Long Duration Debt Index"),
    ("Dynamic Bond Fund",                        "NIFTY Composite Debt Index"),
    ("Corporate Bond Fund",                      "NIFTY Corporate Bond Index"),
    ("Credit Risk Fund",                         "NIFTY Credit Risk Bond Index"),
    ("Banking and PSU Fund",                     "NIFTY Banking & PSU Debt Index"),
    ("Gilt Fund",                                "NIFTY All Duration G-Sec Index"),
    ("Gilt Fund with 10 year constant duration", "NIFTY 10 yr Benchmark G-Sec Index"),
    ("Floater Fund",                             "NIFTY Floater Liquid Index"),
    # Index / ETF / FoF
    ("Index Fund",                               "Underlying Index"),
    ("ETF",                                      "Underlying Index"),
    ("Fund of Fund",                             "Tier-1 Benchmark of Underlying Fund"),
    ("FoF",                                      "Tier-1 Benchmark of Underlying Fund"),
]

def _derive_benchmark(category: str) -> str:
    cat_lower = category.lower()
    for keyword, benchmark in _BENCHMARK_MAP:
        if keyword.lower() in cat_lower:
            return benchmark
    return f"Benchmark not mapped (category: '{category}')"

# ── Asset class / fund type helpers ──────────────────────────────────────────

def _infer_asset_class(scheme_category: str) -> str:
    c = scheme_category.lower()
    if "equity" in c or "elss" in c:             return "Equity"
    if "debt" in c:                              return "Debt"
    if ("hybrid" in c or "arbitrage" in c
            or "asset allocation" in c
            or "savings" in c):                  return "Hybrid"
    if ("solution" in c or "retirement" in c
            or "children" in c):                 return "Solution Oriented"
    if ("index" in c or "etf" in c
            or "fund of fund" in c or "fof" in c): return "Passive / FoF"
    return "Other"

def _clean_fund_type(scheme_category: str) -> str:
    return (scheme_category.split(" - ", 1)[1].strip()
            if " - " in scheme_category else scheme_category.strip())

def _benchmark_source(scheme_category: str, actual_benchmark: str) -> str:
    derived = _derive_benchmark(scheme_category)
    if derived.startswith("Benchmark not mapped"):
        if actual_benchmark and not actual_benchmark.startswith("Benchmark not mapped"):
            return "User Override"
        return "Not Mapped"
    return "SEBI Circular" if actual_benchmark == derived else "User Override"


# ══════════════════════════════════════════════════════════════════════════════
# Skill functions  (module-level so they can be tested directly)
# ══════════════════════════════════════════════════════════════════════════════

def skill_get_amc_list(params: Dict[str, Any]) -> AgentResponse:
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))
    amc_list = sorted(df["fund_house"].dropna().unique().tolist())
    return AgentResponse(
        status=AgentStatus.SUCCESS,
        output={"amcs": amc_list, "count": len(amc_list)},
        metadata={"total_schemes": len(df)},
    )


def skill_get_funds_by_amc(params: Dict[str, Any]) -> AgentResponse:
    amc_name = (params.get("amc_name") or "").strip()
    if not amc_name:
        return AgentResponse(status=AgentStatus.FAILED, error="'amc_name' param is required.")
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))
    amc_lower = amc_name.lower()
    hits = df[df["fund_house"].str.lower().str.contains(amc_lower, regex=False, na=False)]
    if hits.empty:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No funds found for AMC '{amc_name}'.")
    matched = [
        {"schemeCode": int(r["scheme_code"]), "schemeName": str(r["scheme_name"])}
        for _, r in hits.iterrows()
    ]
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"amc_name": amc_name, "funds": matched, "count": len(matched)})


def skill_get_fund_benchmark(params: Dict[str, Any]) -> AgentResponse:
    """CSV lookup. Run rebuild_benchmark_master if mf_benchmark_map.csv is absent."""
    code = params.get("scheme_code")
    if code is None:
        return AgentResponse(status=AgentStatus.FAILED, error="'scheme_code' param is required.")
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))
    hits = df[df["scheme_code"] == int(code)]
    if hits.empty:
        return AgentResponse(
            status=AgentStatus.FAILED,
            error=f"scheme_code {code} not found in mf_benchmark_map.csv. "
                  "Run rebuild_benchmark_master to refresh.",
        )
    row      = hits.iloc[0]
    category = str(row.get("scheme_category", ""))
    return AgentResponse(status=AgentStatus.SUCCESS, output={
        "scheme_code"    : int(row.get("scheme_code", code)),
        "scheme_name"    : str(row.get("scheme_name", "")),
        "fund_house"     : str(row.get("fund_house", "")),
        "scheme_type"    : str(row.get("scheme_type", "")),
        "scheme_category": category,
        "benchmark"      : str(row.get("benchmark", "")) or _derive_benchmark(category),
        "latest_nav"     : row.get("latest_nav"),
        "nav_date"       : row.get("nav_date"),
    })


def skill_get_scheme_classification(params: Dict[str, Any]) -> AgentResponse:
    """
    Returns scheme_type and scheme_category from the CSV mapping file.
    Requires data/mf_benchmark_map.csv — run 'rebuild_benchmark_master' if absent.

    Input — provide ONE of:
      scheme_code  : int       → single CSV lookup
      scheme_codes : List[int] → bulk CSV lookup
      fund_name    : str       → name-based CSV lookup (add exact=True for exact match)

    Output (single / fund_name mode):
      scheme_code, scheme_name, fund_house, scheme_type, scheme_category, benchmark

    Output (bulk mode):
      results : List[dict]  — one entry per resolved scheme_code
      failed  : List[int]   — codes not found in CSV
    """
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))

    scheme_codes = params.get("scheme_codes")
    scheme_code  = params.get("scheme_code")
    fund_name    = (params.get("fund_name") or "").strip()

    def _row_to_output(row) -> Dict:
        category = str(row.get("scheme_category", ""))
        return {
            "scheme_code":     int(row.get("scheme_code", 0)),
            "scheme_name":     str(row.get("scheme_name", "")),
            "fund_house":      str(row.get("fund_house", "")),
            "scheme_type":     str(row.get("scheme_type", "")),
            "scheme_category": category,
            "benchmark":       str(row.get("benchmark", "")) or _derive_benchmark(category),
        }

    # ── Bulk mode ─────────────────────────────────────────────────────────────
    if scheme_codes:
        results: List[Dict] = []
        failed:  List[int]  = []
        for code in scheme_codes:
            hits = df[df["scheme_code"] == int(code)]
            if hits.empty:
                failed.append(code)
            else:
                results.append(_row_to_output(hits.iloc[0]))
        if not results:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"None of {len(scheme_codes)} scheme_codes found in CSV.",
                output={"results": [], "failed": failed},
            )
        return AgentResponse(
            status=AgentStatus.SUCCESS,
            output={"results": results, "failed": failed},
            metadata={"resolved": len(results), "failed": len(failed), "source": "csv"},
        )

    # ── Single scheme_code mode ───────────────────────────────────────────────
    if scheme_code is not None:
        hits = df[df["scheme_code"] == int(scheme_code)]
        if hits.empty:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"scheme_code {scheme_code} not found in mf_benchmark_map.csv. "
                      "Run rebuild_benchmark_master to refresh.",
            )
        return AgentResponse(
            status=AgentStatus.SUCCESS,
            output=_row_to_output(hits.iloc[0]),
            metadata={"source": "csv"},
        )

    # ── fund_name mode ────────────────────────────────────────────────────────
    if fund_name:
        name_lower = fund_name.lower()
        exact      = bool(params.get("exact", False))
        mask = (df["scheme_name"].str.lower() == name_lower if exact
                else df["scheme_name"].str.lower().str.contains(name_lower, regex=False, na=False))
        hits = df[mask]
        if hits.empty:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"No match for fund_name='{fund_name}' in mf_benchmark_map.csv.",
            )
        return AgentResponse(
            status=AgentStatus.SUCCESS,
            output=_row_to_output(hits.iloc[0]),
            metadata={"match_count": len(hits), "source": "csv"},
        )

    return AgentResponse(
        status=AgentStatus.FAILED,
        error="Provide 'scheme_code', 'scheme_codes' (list), or 'fund_name'.",
    )


def skill_lookup_benchmark_by_name(params: Dict[str, Any]) -> AgentResponse:
    fund_name = (params.get("fund_name") or "").strip()
    if not fund_name:
        return AgentResponse(status=AgentStatus.FAILED, error="'fund_name' param is required.")
    amc_filter   = (params.get("amc")       or "").strip().lower()
    type_filter  = (params.get("fund_type") or "").strip().lower()
    exact        = bool(params.get("exact", False))
    mapping_file = params.get("mapping_file")

    try:
        df = _require_df(mapping_file)
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))

    # Compare only the portion before " - " in both the query and the scheme_name.
    # e.g. "Canara Robeco Large Cap Fund - Regular Growth" → "canara robeco large cap fund"
    def _base(s: str) -> str:
        return s.split(" - ")[0].strip().lower()

    query_base   = _base(fund_name)
    scheme_bases = df["scheme_name"].fillna("").apply(_base)

    if exact:
        mask = scheme_bases == query_base
    else:
        # Bidirectional containment: handles cases where the customer fund name
        # has no dashes (e.g. "SBI Small Cap Fund Regular Growth") so query_base
        # is the full name, longer than scheme_base ("SBI Small Cap Fund").
        mask = (
            scheme_bases.str.contains(query_base, regex=False)
            | scheme_bases.apply(lambda b: bool(b) and b in query_base)
        )
    if amc_filter:  mask &= df["fund_house"].str.lower().str.contains(amc_filter, regex=False)
    if type_filter: mask &= df["scheme_category"].str.lower().str.contains(type_filter, regex=False)
    hits = df[mask]
    if hits.empty:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No matches for fund_name='{fund_name}' in mf_benchmark_map.csv.",
                             metadata={"source": "csv"})
    results = [{col: row.get(col) for col in _MAP_COLS} for _, row in hits.iterrows()]
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"query": {"fund_name": fund_name, "source": "csv"},
                                 "matches": results, "count": len(results)})


def skill_get_asset_classes(params: Dict[str, Any]) -> AgentResponse:
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))
    df = df.copy()
    df["_ac"] = df["scheme_category"].apply(_infer_asset_class)
    counts = df["_ac"].value_counts().to_dict()
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"asset_classes": sorted(counts),
                                 "counts": counts, "total_funds": len(df)})


def skill_get_fund_types(params: Dict[str, Any]) -> AgentResponse:
    asset_class = (params.get("asset_class") or "").strip()
    if not asset_class:
        return AgentResponse(status=AgentStatus.FAILED, error="'asset_class' param is required.")
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))
    df = df.copy()
    df["_ac"] = df["scheme_category"].apply(_infer_asset_class)
    df["_ft"] = df["scheme_category"].apply(_clean_fund_type)
    sub = df[df["_ac"].str.lower() == asset_class.lower()]
    if sub.empty:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No funds for asset class '{asset_class}'.")
    counts = sub["_ft"].value_counts().to_dict()
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"asset_class": asset_class,
                                 "fund_types": sorted(counts), "counts": counts})


def skill_get_funds_by_filter(params: Dict[str, Any]) -> AgentResponse:
    asset_class = (params.get("asset_class") or "").strip()
    fund_type   = (params.get("fund_type")   or "").strip()
    if not asset_class:
        return AgentResponse(status=AgentStatus.FAILED, error="'asset_class' param is required.")
    if not fund_type:
        return AgentResponse(status=AgentStatus.FAILED, error="'fund_type' param is required.")
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))

    df = df.copy()
    df["_ac"] = df["scheme_category"].apply(_infer_asset_class)
    df["_ft"] = df["scheme_category"].apply(_clean_fund_type)
    mask = ((df["_ac"].str.lower() == asset_class.lower()) &
            (df["_ft"].str.lower() == fund_type.lower()))
    amc_f    = (params.get("amc")    or "").strip().lower()
    search_f = (params.get("search") or "").strip().lower()
    if amc_f:    mask &= df["fund_house"].str.lower().str.contains(amc_f,    regex=False, na=False)
    if search_f: mask &= df["scheme_name"].str.lower().str.contains(search_f, regex=False, na=False)

    filtered = df[mask].copy()
    if filtered.empty:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No funds for '{asset_class}' / '{fund_type}'.")
    filtered["benchmark_source"] = filtered.apply(
        lambda r: _benchmark_source(r.get("scheme_category",""), r.get("benchmark","")), axis=1)

    total     = len(filtered)
    raw_size  = params.get("page_size", 50)
    page_size = total if raw_size == 0 else max(1, int(raw_size))
    page      = min(max(1, int(params.get("page", 1))),
                    (total + page_size - 1) // page_size)
    pages     = (total + page_size - 1) // page_size
    chunk     = filtered.iloc[(page - 1)*page_size : page*page_size]
    keep      = [c for c in ["scheme_code","scheme_name","fund_house","scheme_category",
                              "benchmark","benchmark_source","latest_nav","nav_date"]
                 if c in chunk.columns]
    return AgentResponse(status=AgentStatus.SUCCESS, output={
        "funds"     : chunk[keep].to_dict(orient="records"),
        "total"     : total, "page": page, "pages": pages, "page_size": page_size,
        "benchmarks": filtered["benchmark"].value_counts().to_dict(),
        "sources"   : filtered["benchmark_source"].value_counts().to_dict(),
    })


def skill_update_benchmark(params: Dict[str, Any]) -> AgentResponse:
    mapping_path = Path(params.get("mapping_file") or DEFAULT_MAPPING_FILE)
    try:
        df = _require_df(params.get("mapping_file")).copy()
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))

    updates: List[Dict] = []
    if "updates" in params and isinstance(params["updates"], list):
        updates = params["updates"]
    elif "scheme_code" in params and "new_benchmark" in params:
        updates = [{"scheme_code": params["scheme_code"], "benchmark": params["new_benchmark"]}]
    if not updates:
        return AgentResponse(status=AgentStatus.FAILED,
                             error="Provide scheme_code+new_benchmark or updates=[...].")

    updated, not_found = [], []
    for upd in updates:
        code   = int(upd.get("scheme_code", -1))
        new_bm = str(upd.get("benchmark","")).strip()
        idx    = df.index[df["scheme_code"] == code].tolist()
        if not idx: not_found.append(code); continue
        old_bm = str(df.loc[idx[0], "benchmark"])
        df.loc[idx, "benchmark"] = new_bm
        updated.append({"scheme_code": code, "scheme_name": str(df.loc[idx[0],"scheme_name"]),
                         "old_benchmark": old_bm, "benchmark": new_bm})
    try:
        gcs_uri = _save_and_upload_csv(df, mapping_path)
        _invalidate_mapping_cache()
    except Exception as e:
        return AgentResponse(status=AgentStatus.FAILED, error=f"Failed to save: {e}")
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"updated": updated, "not_found": not_found, "count": len(updated)},
                         metadata={"gcs_uri": gcs_uri})


def skill_get_fund_type_summary(params: Dict[str, Any]) -> AgentResponse:
    asset_class = (params.get("asset_class") or "").strip()
    if not asset_class:
        return AgentResponse(status=AgentStatus.FAILED, error="'asset_class' param is required.")
    try:
        df = _require_df(params.get("mapping_file"))
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))

    df = df.copy()
    df["_ac"] = df["scheme_category"].apply(_infer_asset_class)
    df["_ft"] = df["scheme_category"].apply(_clean_fund_type)
    sub = df[df["_ac"].str.lower() == asset_class.lower()]
    if sub.empty:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No funds for asset class '{asset_class}'.")

    rows: List[Dict] = []
    for ft, grp in sub.groupby("_ft", sort=True):
        bm_counts  = grp["benchmark"].value_counts()
        top_bm     = bm_counts.index[0] if not bm_counts.empty else ""
        rows.append({
            "fund_type"       : ft,
            "fund_count"      : len(grp),
            "benchmark"       : top_bm,
            "is_uniform"      : len(bm_counts) == 1,
            "benchmark_source": _benchmark_source(grp["scheme_category"].iloc[0], top_bm),
            "scheme_codes"    : grp["scheme_code"].tolist(),
            "benchmarks_used" : bm_counts.to_dict(),
        })
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"asset_class": asset_class, "fund_types": rows})


def skill_update_benchmark_by_fund_type(params: Dict[str, Any]) -> AgentResponse:
    asset_class   = (params.get("asset_class")   or "").strip()
    fund_type     = (params.get("fund_type")      or "").strip()
    new_benchmark = (params.get("new_benchmark")  or "").strip()
    if not asset_class:
        return AgentResponse(status=AgentStatus.FAILED, error="'asset_class' param is required.")
    if not fund_type:
        return AgentResponse(status=AgentStatus.FAILED, error="'fund_type' param is required.")
    if not new_benchmark:
        return AgentResponse(status=AgentStatus.FAILED, error="'new_benchmark' param is required.")

    mapping_path = Path(params.get("mapping_file") or DEFAULT_MAPPING_FILE)
    try:
        df = _require_df(params.get("mapping_file")).copy()
    except FileNotFoundError as e:
        return AgentResponse(status=AgentStatus.FAILED, error=str(e))

    df["_ac"] = df["scheme_category"].apply(_infer_asset_class)
    df["_ft"] = df["scheme_category"].apply(_clean_fund_type)
    mask = ((df["_ac"].str.lower() == asset_class.lower()) &
            (df["_ft"].str.lower() == fund_type.lower()))
    if not mask.any():
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No funds for '{asset_class}' / '{fund_type}'.")
    updated_count = int(mask.sum())
    df.loc[mask, "benchmark"] = new_benchmark
    df = df.drop(columns=["_ac","_ft"])
    try:
        gcs_uri = _save_and_upload_csv(df, mapping_path)
        _invalidate_mapping_cache()
    except Exception as e:
        return AgentResponse(status=AgentStatus.FAILED, error=f"Failed to save: {e}")
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"updated_count": updated_count,
                                 "fund_type": fund_type, "benchmark": new_benchmark},
                         metadata={"gcs_uri": gcs_uri})


# ══════════════════════════════════════════════════════════════════════════════
# Live mfapi skills  (_from_mfapi suffix)
# ══════════════════════════════════════════════════════════════════════════════

def skill_get_amc_list_from_mfapi(params: Dict[str, Any]) -> AgentResponse:
    """Fetches all schemes from mfapi and returns distinct AMC names parsed from scheme names."""
    try:
        master = _all_schemes()
    except Exception as exc:
        return AgentResponse(status=AgentStatus.FAILED, error=str(exc))
    # mfapi scheme names are typically "AMC Name - Scheme Name"
    amc_set: set = set()
    for s in master:
        name = s.get("schemeName", "")
        amc_set.add(name.split(" - ")[0].strip() if " - " in name else "")
    amc_set.discard("")
    amc_list = sorted(amc_set)
    return AgentResponse(
        status=AgentStatus.SUCCESS,
        output={"amcs": amc_list, "count": len(amc_list)},
        metadata={"total_schemes": len(master)},
    )


def skill_get_funds_by_amc_from_mfapi(params: Dict[str, Any]) -> AgentResponse:
    """Fetches all schemes from mfapi and returns those whose name starts with the given AMC."""
    amc_name = (params.get("amc_name") or "").strip()
    if not amc_name:
        return AgentResponse(status=AgentStatus.FAILED, error="'amc_name' param is required.")
    try:
        master = _all_schemes()
    except Exception as exc:
        return AgentResponse(status=AgentStatus.FAILED, error=str(exc))
    amc_lower = amc_name.lower()
    matched = [
        {"schemeCode": s["schemeCode"], "schemeName": s["schemeName"]}
        for s in master
        if amc_lower in s.get("schemeName", "").lower()
    ]
    if not matched:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"No funds found for AMC '{amc_name}' in mfapi.")
    return AgentResponse(status=AgentStatus.SUCCESS,
                         output={"amc_name": amc_name, "funds": matched, "count": len(matched)})


def skill_get_fund_benchmark_from_mfapi(params: Dict[str, Any]) -> AgentResponse:
    """Live mfapi lookup for a single scheme_code. Does NOT require the CSV file."""
    code = params.get("scheme_code")
    if code is None:
        return AgentResponse(status=AgentStatus.FAILED, error="'scheme_code' param is required.")
    try:
        data = _get(SCHEME_URL.format(code=code))
    except Exception as exc:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"mfapi request failed for scheme {code}: {exc}")
    if data.get("status") != "SUCCESS":
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"mfapi returned status '{data.get('status')}' for scheme {code}.")
    meta      = data.get("meta", {})
    nav_entry = data.get("data", [{}])[0]
    category  = meta.get("scheme_category", "")
    return AgentResponse(status=AgentStatus.SUCCESS, output={
        "scheme_code"    : meta.get("scheme_code"),
        "scheme_name"    : meta.get("scheme_name"),
        "fund_house"     : meta.get("fund_house"),
        "scheme_type"    : meta.get("scheme_type"),
        "scheme_category": category,
        "benchmark"      : _derive_benchmark(category),
        "latest_nav"     : nav_entry.get("nav"),
        "nav_date"       : nav_entry.get("date"),
    })


def skill_get_scheme_classification_from_mfapi(params: Dict[str, Any]) -> AgentResponse:
    """
    Live mfapi lookup for scheme_type and scheme_category.
    Accepts scheme_code (int) or scheme_codes (List[int]). Does NOT require the CSV file.
    """
    scheme_codes = params.get("scheme_codes")
    scheme_code  = params.get("scheme_code")

    if scheme_codes:
        results: List[Dict] = []
        failed:  List[int]  = []
        for code in scheme_codes:
            try:
                data = _get(SCHEME_URL.format(code=int(code)))
                if data.get("status") != "SUCCESS":
                    failed.append(code)
                    continue
                meta     = data.get("meta", {})
                category = meta.get("scheme_category", "")
                results.append({
                    "scheme_code":     meta.get("scheme_code", code),
                    "scheme_name":     meta.get("scheme_name", ""),
                    "fund_house":      meta.get("fund_house", ""),
                    "scheme_type":     meta.get("scheme_type", ""),
                    "scheme_category": category,
                    "benchmark":       _derive_benchmark(category),
                })
            except Exception:
                failed.append(code)
        if not results:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"All {len(scheme_codes)} scheme_code lookups failed.",
                output={"results": [], "failed": failed},
            )
        return AgentResponse(
            status=AgentStatus.SUCCESS,
            output={"results": results, "failed": failed},
            metadata={"resolved": len(results), "failed": len(failed)},
        )

    if scheme_code is not None:
        try:
            data = _get(SCHEME_URL.format(code=int(scheme_code)))
        except Exception as exc:
            return AgentResponse(status=AgentStatus.FAILED,
                                 error=f"mfapi request failed for scheme {scheme_code}: {exc}")
        if data.get("status") != "SUCCESS":
            return AgentResponse(status=AgentStatus.FAILED,
                                 error=f"mfapi returned status '{data.get('status')}' for scheme {scheme_code}.")
        meta     = data.get("meta", {})
        category = meta.get("scheme_category", "")
        return AgentResponse(
            status=AgentStatus.SUCCESS,
            output={
                "scheme_code":     meta.get("scheme_code", scheme_code),
                "scheme_name":     meta.get("scheme_name", ""),
                "fund_house":      meta.get("fund_house", ""),
                "scheme_type":     meta.get("scheme_type", ""),
                "scheme_category": category,
                "benchmark":       _derive_benchmark(category),
            },
        )

    return AgentResponse(
        status=AgentStatus.FAILED,
        error="Provide 'scheme_code' or 'scheme_codes' (list).",
    )


def skill_rebuild_benchmark_master(params: Dict[str, Any]) -> AgentResponse:
    """
    Admin skill — fetches every scheme from mfapi.in, enriches each with metadata,
    derives the SEBI benchmark, writes data/mf_benchmark_map.csv locally, and
    uploads it to GCS winrich_shared/master/mf_benchmark_map.csv.

    This is intentionally slow (one HTTP request per scheme). Run it once to
    bootstrap the CSV, then use it periodically to pick up new fund launches.

    Optional params:
      mapping_file : str   — override local output path (default: data/mf_benchmark_map.csv)
      max_schemes  : int   — limit to first N schemes (useful for testing)
    """
    import pandas as pd

    mapping_path = Path(params.get("mapping_file") or DEFAULT_MAPPING_FILE)
    max_schemes  = params.get("max_schemes")  # None means all

    # 1. Fetch master list (schemeCode + schemeName only)
    try:
        master = _all_schemes()
    except Exception as exc:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"Failed to fetch master scheme list: {exc}")

    if max_schemes:
        master = master[:int(max_schemes)]

    # 2. Enrich each scheme
    rows: List[Dict] = []
    errors: List[Dict] = []
    for s in master:
        code = s.get("schemeCode")
        try:
            data = _get(SCHEME_URL.format(code=code))
            if data.get("status") != "SUCCESS":
                errors.append({"scheme_code": code, "reason": f"status={data.get('status')}"})
                continue
            meta      = data.get("meta", {})
            nav_entry = data.get("data", [{}])[0] if data.get("data") else {}
            category  = meta.get("scheme_category", "")
            rows.append({
                "scheme_code"    : meta.get("scheme_code", code),
                "scheme_name"    : meta.get("scheme_name", s.get("schemeName", "")),
                "fund_house"     : meta.get("fund_house", ""),
                "scheme_type"    : meta.get("scheme_type", ""),
                "scheme_category": category,
                "benchmark"      : _derive_benchmark(category),
                "latest_nav"     : nav_entry.get("nav"),
                "nav_date"       : nav_entry.get("date"),
            })
        except Exception as exc:
            errors.append({"scheme_code": code, "reason": str(exc)})

    if not rows:
        return AgentResponse(
            status=AgentStatus.FAILED,
            error="No schemes could be fetched from mfapi.",
            metadata={"errors": errors[:20]},
        )

    # 3. Write CSV locally and upload to GCS
    try:
        df      = pd.DataFrame(rows)
        gcs_uri = _save_and_upload_csv(df, mapping_path)
        _invalidate_mapping_cache()
    except Exception as exc:
        return AgentResponse(status=AgentStatus.FAILED,
                             error=f"Failed to write/upload CSV: {exc}")

    return AgentResponse(
        status=AgentStatus.SUCCESS,
        output={
            "schemes_written": len(rows),
            "errors":          len(errors),
            "mapping_file":    str(mapping_path),
            "gcs_uri":         gcs_uri,
        },
        metadata={"sample_errors": errors[:10]},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Agent class — all skills registered inline
# ══════════════════════════════════════════════════════════════════════════════

class MutualFundBenchmarkAgent(Agent):
    """
    MutualFundBenchmarkAgent — all 16 skills in one place.
    Skills are defined as module-level functions above and referenced here
    so the dict is complete at class-definition time with no split registration.
    """

    name = "MutualFundBenchmarkAgent"

    skills = {
        # Parquet-only (fail if mf_benchmark_map.csv absent)
        "get_amc_list"                        : skill_get_amc_list,
        "get_funds_by_amc"                    : skill_get_funds_by_amc,
        "get_fund_benchmark"                  : skill_get_fund_benchmark,
        "get_scheme_classification"           : skill_get_scheme_classification,
        "lookup_benchmark_by_name"            : skill_lookup_benchmark_by_name,
        "get_asset_classes"                   : skill_get_asset_classes,
        "get_fund_types"                      : skill_get_fund_types,
        "get_funds_by_filter"                 : skill_get_funds_by_filter,
        "update_benchmark"                    : skill_update_benchmark,
        "get_fund_type_summary"               : skill_get_fund_type_summary,
        "update_benchmark_by_fund_type"       : skill_update_benchmark_by_fund_type,
        # Live mfapi skills
        "get_amc_list_from_mfapi"             : skill_get_amc_list_from_mfapi,
        "get_funds_by_amc_from_mfapi"         : skill_get_funds_by_amc_from_mfapi,
        "get_fund_benchmark_from_mfapi"       : skill_get_fund_benchmark_from_mfapi,
        "get_scheme_classification_from_mfapi": skill_get_scheme_classification_from_mfapi,
        # Admin / rebuild
        "rebuild_benchmark_master"            : skill_rebuild_benchmark_master,
    }