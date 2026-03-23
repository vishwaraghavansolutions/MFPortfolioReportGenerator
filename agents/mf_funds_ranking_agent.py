"""
agents/fund_ranking_agent.py

FundRankingAgent
----------------  
Reads fund ranking data from:
  1. GCP bucket  : gs://winrich_shared/ranking/fund_ranking.csv
  2. Local file  : fund_ranking.csv  (fallback if GCP is unavailable)

Exposed skill
-------------
  skill_id : "get_fund_rank"

  params : {
      "fund_name"       : str   – exact or partial fund name (case-insensitive)
      "scheme_type"     : str   – e.g. "Equity Scheme"
      "scheme_category" : str   – e.g. "Large Cap Fund"
  }

  output : {
      "fund_name"       : str
      "scheme_type"     : str
      "scheme_category" : str
      "rank"            : int
      "max_rank"        : int
      "rank_label"      : str   – e.g. "3 / 79"
      "source"          : str   – "gcp" | "local"
      "matches"         : list  – all matching rows (fund may appear under multiple plans)
  }
"""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agents.base import Agent, AgentResponse, AgentStatus
from agents.gcs_storage_agent import GCSStorageAgent

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# basicConfig is a no-op if the root logger already has handlers (e.g. set up by another
# module or by Streamlit).  Force the root level down so our DEBUG records are not dropped.
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.root.setLevel(logging.DEBUG)

# ── GCS settings ───────────────────────────────────────────────────────────────
# FundRankingAgent delegates all GCS I/O to GCSStorageAgent (load_ranking_csv).
# Override these defaults by passing bucket/prefix to get_fund_rank params,
# or set them here as module-level defaults.
_DEFAULT_RANKING_BUCKET   = "winrich_shared"
_DEFAULT_RANKING_PREFIX   = "ranking"
_DEFAULT_RANKING_FILENAME = "fund_ranking.csv"

# ── Local fallback ─────────────────────────────────────────────────────────────
LOCAL_FILE = "fund_ranking.csv"

# ── Lazy GCSStorageAgent accessor ─────────────────────────────────────────────
# Instantiated on first use so Streamlit secrets are available by then.
_gcs_agent_instance: Optional[GCSStorageAgent] = None

def _get_gcs_agent() -> GCSStorageAgent:
    global _gcs_agent_instance
    if _gcs_agent_instance is None:
        _gcs_agent_instance = GCSStorageAgent()
    return _gcs_agent_instance


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_from_gcs(
    filename:    str = _DEFAULT_RANKING_FILENAME,
    bucket_name: str = _DEFAULT_RANKING_BUCKET,
    prefix:      str = _DEFAULT_RANKING_PREFIX,
) -> tuple[List[Dict], str]:
    """
    Load ranking CSV rows via GCSStorageAgent.load_ranking_csv.
    Returns (rows, source_label).
    Raises on any failure so the caller can fall back to local.
    """
    import pandas as pd

    resp = _get_gcs_agent().run("load_ranking_csv", {
        "filename":    filename,
        "bucket_name": bucket_name,
        "prefix":      prefix,
    })
    if resp.status != AgentStatus.SUCCESS:
        raise RuntimeError(resp.error)

    raw = resp.output["dataframe"]

    # Guard: GCSStorageAgent returns a DataFrame, but defensively handle dict
    if isinstance(raw, pd.DataFrame):
        df = raw
    elif isinstance(raw, dict):
        df = pd.DataFrame.from_dict(raw)
    else:
        raise RuntimeError(
            f"load_ranking_csv returned unexpected type for 'dataframe': {type(raw)}"
        )

    rows   = df.to_dict(orient="records")
    # Normalise: strip whitespace from all string values
    rows   = [
        {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in rows
    ]
    logger.info(
        "FundRankingAgent: loaded %d rows from GCS %s",
        len(rows), resp.output["gcs_uri"],
    )
    return rows, "gcs"


def _load_from_local(filename: str = LOCAL_FILE) -> tuple[List[Dict], str]:
    """Load CSV rows from the local fallback file."""
    with open(filename, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows   = [row for row in reader]
    logger.info(
        "FundRankingAgent: loaded %d rows from local file '%s'", len(rows), filename
    )
    return rows, "local"


def _load_data(
    filename:    str = _DEFAULT_RANKING_FILENAME,
    bucket_name: str = _DEFAULT_RANKING_BUCKET,
    prefix:      str = _DEFAULT_RANKING_PREFIX,
) -> tuple[List[Dict], str]:
    """Try GCS via GCSStorageAgent first; fall back to local on any exception."""
    try:
        return _load_from_gcs(filename, bucket_name, prefix)
    except Exception as exc:
        logger.warning(
            "FundRankingAgent: GCS load failed (%s). Falling back to local file '%s'.",
            exc, filename,
        )
        return _load_from_local(filename)


import json
import os
import re as _re

# ── Ranking config (AMC aliases + plan filter) ─────────────────────────────────
# Loaded from data/ranking_config.json at import time.
# To add a new AMC alias edit that file — no code change needed.
_RANKING_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "ranking_config.json"
)

def _load_ranking_config() -> dict:
    try:
        with open(_RANKING_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        logger.debug("FundRankingAgent: loaded ranking config from %s", _RANKING_CONFIG_PATH)
        return cfg
    except Exception as exc:
        logger.warning(
            "FundRankingAgent: could not load ranking config (%s); using defaults.", exc
        )
        return {}

_RANKING_CONFIG = _load_ranking_config()

# AMC aliases: list of (full_form, abbreviated_form) tuples — both lowercased.
# E.g. "canara robeco" in portfolio name → "canara rob" matches CSV.
_AMC_ALIASES: List[tuple] = [
    (full.lower(), abbrev.lower())
    for full, abbrev in _RANKING_CONFIG.get("amc_aliases", {}).items()
]

# Plan value that ranking rows must match (e.g. "Growth").
# Set to None/empty in config to disable the plan filter.
_PLAN_FILTER: str = (_RANKING_CONFIG.get("plan_filter") or "").strip()

# ── Word / spacing normalisations ─────────────────────────────────────────────
# Handles "Largecap" vs "Large Cap", "Large & Mid Cap" vs "LargeMidcap", etc.
_WORD_NORMS: List[tuple] = [
    (_re.compile(r'\blargecap\b',                    _re.IGNORECASE), 'large cap'),
    (_re.compile(r'\blargemidcap\b',                 _re.IGNORECASE), 'large mid cap'),
    (_re.compile(r'\blarge\s*&\s*mid\s*cap\b',       _re.IGNORECASE), 'large mid cap'),
    (_re.compile(r'\blarge\s+and\s+mid\s*cap\b',     _re.IGNORECASE), 'large mid cap'),
    (_re.compile(r'\bmid\s*cap\b',                   _re.IGNORECASE), 'midcap'),
    (_re.compile(r'\bflexi\s*cap\b',                 _re.IGNORECASE), 'flexi cap'),
    (_re.compile(r'\bmulti\s*cap\b',                 _re.IGNORECASE), 'multicap'),
    (_re.compile(r'\bsmall\s*cap\b',                 _re.IGNORECASE), 'small cap'),
    (_re.compile(r'\belss\b',                        _re.IGNORECASE), 'elss'),
]

# ── Suffixes stripped before matching ─────────────────────────────────────────
# Removes plan/option/erstwhile suffixes that appear in portfolio s_name values
# but are never present in ranking CSV fund names.
_STRIP_PATTERNS: List[_re.Pattern] = [
    _re.compile(r'\s*\(erstwhile[^)]*\)',            _re.IGNORECASE),
    _re.compile(r'\s*-\s*regular.*$',                _re.IGNORECASE),
    _re.compile(r'\s*-\s*direct.*$',                 _re.IGNORECASE),
    _re.compile(r'\s*-\s*growth.*$',                 _re.IGNORECASE),
    _re.compile(r'\s*-\s*idcw.*$',                   _re.IGNORECASE),
    _re.compile(r'\s*-\s*dividend.*$',               _re.IGNORECASE),
    _re.compile(r'\s*\([^)]*\)$',                    _re.IGNORECASE),
    _re.compile(r'\s+regular\s*$',                   _re.IGNORECASE),
    _re.compile(r'\s+growth\s*$',                    _re.IGNORECASE),
]


def _normalise(value) -> str:
    """Basic lowercase + strip. Safely handles NaN / None / non-string values."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _normalise_category(value) -> str:
    """
    Normalise scheme_category for matching.
    Strips a trailing ' fund' so that 'Large Cap' == 'Large Cap Fund',
    'Mid Cap' == 'Mid Cap Fund', etc.  The ranking CSV uses the short form
    ('Large Cap') while mfapi / SEBI returns the long form ('Large Cap Fund').
    Safely handles NaN / None / non-string values.
    """
    n = value.strip().lower()
    if n.endswith(" fund"):
        n = n[:-5].strip()
    return n


def _clean_name(name) -> str:
    """Strip plan/option/erstwhile suffixes from a portfolio fund name."""
    if not isinstance(name, str):
        return ""
    for pat in _STRIP_PATTERNS:
        name = pat.sub("", name)
    return name.strip()


def _deep_normalise(name) -> str:
    """
    Full normalisation pipeline for fund name matching:
      1. Strip plan/option/erstwhile suffixes
      2. Lowercase + strip
      3. Apply word normalisations  (e.g. "Largecap" → "large cap")
      4. Apply AMC abbreviations    (e.g. "canara robeco" → "canara rob")
    Safely handles NaN / None / non-string values.
    """
    if not isinstance(name, str):
        return ""
    name = _clean_name(name)
    n = name.strip().lower()
    for pat, rep in _WORD_NORMS:
        n = pat.sub(rep, n)
    for full, abbrev in _AMC_ALIASES:
        n = n.replace(full, abbrev)
    return n.strip()


def _filter_rows(
    rows: List[Dict],
    fund_name: str,
    scheme_type: str,
    scheme_category: str,
) -> List[Dict]:
    """
    Return all rows whose Fund Name, Scheme Type, and Scheme Category match.

    Matching strategy (applied in order; first match wins per row):
      1. Deep-normalised exact match
      2. Bidirectional substring after deep normalisation

    Deep normalisation resolves three classes of mismatch:
      a) AMC abbreviations   — "Canara Robeco" in portfolio vs "Canara Rob" in CSV
      b) Spacing variants    — "Largecap" vs "Large Cap", "Large & Mid Cap" vs "large mid cap"
      c) Long plan suffixes  — "- Regular Plan - Growth", "(Erstwhile …)" stripped before compare

    scheme_type and scheme_category are still matched exactly (normalised) so
    a fund name token like "large cap" in a Multi Cap fund name cannot
    accidentally match a row from the Large Cap category.
    """
    fn_norm = _deep_normalise(fund_name)
    st      = _normalise(scheme_type)
    sc      = _normalise_category(scheme_category)

    logger.debug(
        "[_filter_rows] INPUT  fund_name=%r  →  fn_norm=%r",
        fund_name, fn_norm,
    )
    logger.debug(
        "[_filter_rows] FILTER scheme_type=%r (norm=%r)  scheme_category=%r (norm_cat=%r)",
        scheme_type, st, scheme_category, sc,
    )

    _plan_filter_norm = _normalise(_PLAN_FILTER)
    category_rows = [
        r for r in rows
        if _normalise_category(r.get("Scheme Category", "")) == sc
        and (
            not _PLAN_FILTER
            or _plan_filter_norm in _normalise(r.get("Plan", ""))
        )
    ]
    logger.debug(
        "[_filter_rows] %d / %d CSV rows pass category+plan filter (plan_filter=%r, mode=contains)",
        len(category_rows), len(rows), _PLAN_FILTER or "disabled",
    )
    if not category_rows:
        # Show what scheme_type / scheme_category / plan values exist in the CSV
        csv_types = sorted({_normalise(r.get("Scheme Type", "")) for r in rows})
        csv_cats  = sorted({_normalise(r.get("Scheme Category", "")) for r in rows})
        csv_plans = sorted({_normalise(r.get("Plan", "")) for r in rows})
        logger.debug("[_filter_rows] CSV scheme_types       available: %s", csv_types)
        logger.debug("[_filter_rows] CSV scheme_categories  available: %s", csv_cats)
        logger.debug("[_filter_rows] CSV plan values        available: %s", csv_plans)

    matched = []
    for row in category_rows:
        csv_norm = _deep_normalise(row.get("Fund Name", ""))
        exact    = fn_norm == csv_norm
        sub_fwd  = fn_norm in csv_norm
        sub_rev  = csv_norm in fn_norm
        hit      = exact or sub_fwd or sub_rev
        logger.debug(
            "[_filter_rows]   csv=%r  →  csv_norm=%r  | exact=%s fwd=%s rev=%s  → %s",
            row.get("Fund Name", ""), csv_norm,
            exact, sub_fwd, sub_rev,
            "MATCH" if hit else "no match",
        )
        if hit:
            matched.append(row)

    logger.debug("[_filter_rows] RESULT  %d match(es) for %r", len(matched), fund_name)
    return matched


def _max_rank_in_category(
    rows: List[Dict],
    scheme_type: str,
    scheme_category: str,
) -> int:
    """Highest rank value within the given scheme type + category."""
    st = _normalise(scheme_type)
    sc = _normalise_category(scheme_category)
    _plan_filter_norm = _normalise(_PLAN_FILTER)
    category_rows = [
        r for r in rows
        if _normalise_category(r.get("Scheme Category", "")) == sc
        and (
            not _PLAN_FILTER
            or _plan_filter_norm in _normalise(r.get("Plan", ""))
        )
    ]
    if not category_rows:
        return 0
    return max(int(r["Rank"]) for r in category_rows)


# ── Agent ─────────────────────────────────────────────────────────────────────

class FundRankingAgent(Agent):
    name = "FundRankingAgent"

    def __init__(self):
        self.skills = {
            "get_fund_rank": self._get_fund_rank,
        }
        # Cache: (filename, bucket_name, prefix, rows, source)
        # Populated on first load; reused for all subsequent calls with the same params.
        self._data_cache: Optional[tuple] = None

    # ── skill ──────────────────────────────────────────────────────────────────

    def _get_fund_rank(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Required params:
            fund_name, scheme_type, scheme_category

        Optional GCS override params (use defaults if omitted):
            ranking_filename : str   – e.g. "fund_ranking.csv"
            ranking_bucket   : str   – e.g. "winrich_shared"
            ranking_prefix   : str   – e.g. "ranking"
        """
        # ── validate inputs ───────────────────────────────────────────────────
        fund_name       = params.get("fund_name", "").strip()
        scheme_type     = params.get("scheme_type", "").strip()
        scheme_category = params.get("scheme_category", "").strip()

        logger.info(
            "FundRankingAgent.get_fund_rank called with fund_name='%s', scheme_type='%s', scheme_category='%s'",
            fund_name, scheme_type, scheme_category,
        )
        missing = [
            k for k, v in {
                "fund_name":       fund_name,
                "scheme_type":     scheme_type,
                "scheme_category": scheme_category,
            }.items() if not v
        ]
        logger.info("FundRankingAgent.get_fund_rank missing params: %s", missing)

        if missing:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"Missing required params: {missing}",
            )

        # ── load data (GCS via GCSStorageAgent, fallback to local) ────────────
        filename    = params.get("ranking_filename", _DEFAULT_RANKING_FILENAME)
        bucket_name = params.get("ranking_bucket",   _DEFAULT_RANKING_BUCKET)
        prefix      = params.get("ranking_prefix",   _DEFAULT_RANKING_PREFIX)
        logger.info(
            "FundRankingAgent.get_fund_rank loading data with filename='%s', bucket_name='%s', prefix='%s'",
            filename, bucket_name, prefix,  
        )
        try:
            cache_key = (filename, bucket_name, prefix)
            if self._data_cache is not None and self._data_cache[:3] == cache_key:
                rows, source = self._data_cache[3], self._data_cache[4]
                logger.debug("FundRankingAgent: using cached ranking data (%d rows)", len(rows))
            else:
                rows, source = _load_data(filename, bucket_name, prefix)
                self._data_cache = (*cache_key, rows, source)
        except Exception as exc:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"Could not load ranking data: {exc}",
                metadata={"agent": self.name, "skill": "get_fund_rank"},
            )

        logger.debug(
            "[get_fund_rank] loaded %d rows (source=%s) | looking up fund_name=%r "
            "scheme_type=%r scheme_category=%r",
            len(rows), source, fund_name, scheme_type, scheme_category,
        )
        # ── filter ────────────────────────────────────────────────────────────
        matches = _filter_rows(rows, fund_name, scheme_type, scheme_category)

        if not matches:
            logger.debug(
                "[get_fund_rank] NO MATCH for %r (scheme_type=%r, scheme_category=%r)",
                fund_name, scheme_type, scheme_category,
            )
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=(
                    f"No fund matching '{fund_name}' found under "
                    f"scheme_type='{scheme_type}', scheme_category='{scheme_category}'."
                ),
                metadata={"source": source},
            )

        # ── build result ──────────────────────────────────────────────────────
        max_rank = _max_rank_in_category(rows, scheme_type, scheme_category)

        # Use the best (lowest-numbered) rank among all matching rows
        best_row  = min(matches, key=lambda r: int(r["Rank"]))
        best_rank = int(best_row["Rank"])
        logger.debug(
            "[get_fund_rank] MATCHED %r → csv_name=%r rank=%d/%d (source=%s)",
            fund_name, best_row["Fund Name"], best_rank, max_rank, source,
        )

        match_details = [
            {
                "fund_name":       r["Fund Name"],
                "plan":            r.get("Plan", ""),
                "rank":            int(r["Rank"]),
                "scheme_type":     r["Scheme Type"],
                "scheme_category": r["Scheme Category"],
            }
            for r in matches
        ]

        return AgentResponse(
            status=AgentStatus.SUCCESS,
            output={
                "fund_name":       best_row["Fund Name"],
                "scheme_type":     best_row["Scheme Type"],
                "scheme_category": best_row["Scheme Category"],
                "rank":            best_rank,
                "max_rank":        max_rank,
                "rank_label":      f"{best_rank} / {max_rank}",
                "source":          source,
                "matches":         match_details,
            },
            metadata={"agent": self.name, "skill": "get_fund_rank"},
        )