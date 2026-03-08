"""
utils/datawarehouse_loader.py
------------------------------
Loads the MutualFunds datawarehouse CSV from GCS using GCPCSVReader.
Authentication is handled entirely by GCPCSVReader via st.secrets["gcp"].

GCS path pattern:
    gs://<bucket>/Datawarehouse/MutualFunds/<YYYY>/<MM>/<DD>/mutualfunds.csv

The path resolves to the nearest business day on or before as_of_date,
stepping back up to max_lookback_days business days if the file is missing.

Public API
----------
    load_datawarehouse_csv(bucket_name, as_of_date, max_lookback_days) -> pd.DataFrame
    get_datawarehouse_path(as_of_date, bucket_name, max_lookback_days) -> str
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


# ── Business-day helpers ──────────────────────────────────────────────────────

def _is_business_day(d: datetime) -> bool:
    return d.weekday() < 5  # Mon=0 … Fri=4


def _nearest_business_day(d: datetime) -> datetime:
    while not _is_business_day(d):
        d -= timedelta(days=1)
    return d


def _candidate_dates(as_of_date: datetime, max_lookback_days: int):
    """Yield up to max_lookback_days business days stepping back from as_of_date."""
    d = _nearest_business_day(as_of_date)
    for _ in range(max_lookback_days):
        yield d
        d -= timedelta(days=1)
        while not _is_business_day(d):
            d -= timedelta(days=1)


def _gcs_path(date: datetime) -> str:
    return f"Datawarehouse/MutualFunds/{date.year:04d}/{date.month:02d}/{date.day:02d}/mutualfunds.csv"


# ── Public functions ──────────────────────────────────────────────────────────

def load_datawarehouse_csv(
    bucket_name:       str                = "winrich",
    as_of_date:        Optional[datetime] = None,
    max_lookback_days: int                = 10,
) -> pd.DataFrame:
    """
    Download the MutualFunds datawarehouse CSV from GCS.

    Uses GCPCSVReader (authenticated via st.secrets["gcp"]) to walk back from
    as_of_date until a file is found or the lookback window is exhausted.

    Returns
    -------
    pd.DataFrame  with column names stripped of whitespace.

    Raises
    ------
    FileNotFoundError  if no CSV is found within the lookback window.
    """
    from utils.gcs_csv_reader import GCPCSVReader

    if as_of_date is None:
        as_of_date = datetime.now()

    reader = GCPCSVReader(bucket_name=bucket_name)

    for candidate in _candidate_dates(as_of_date, max_lookback_days):
        blob_path = _gcs_path(candidate)
        log.debug("GCS: trying %s", blob_path)

        df = reader.read_csv(blob_path)  # returns empty DataFrame on any error

        if not df.empty:
            df.columns = df.columns.str.strip()
            log.info("GCS: loaded %d rows from gs://%s/%s", len(df), bucket_name, blob_path)
            return df

        log.debug("GCS: not found — %s", blob_path)

    start = _gcs_path(_nearest_business_day(as_of_date))
    raise FileNotFoundError(
        f"No mutualfunds.csv found in gs://{bucket_name} starting at '{start}', "
        f"looking back {max_lookback_days} business days.\n"
        f"Expected: Datawarehouse/MutualFunds/<YYYY>/<MM>/<DD>/mutualfunds.csv"
    )


def get_datawarehouse_path(
    as_of_date:        Optional[datetime] = None,
    bucket_name:       str                = "winrich",
    max_lookback_days: int                = 10,
) -> str:
    """
    Return the resolved GCS URI of the most recent datawarehouse CSV
    without downloading it.

    Returns
    -------
    str  e.g. "gs://winrich/Datawarehouse/MutualFunds/2026/03/mutualfunds.csv"

    Raises
    ------
    FileNotFoundError  if nothing found within the lookback window.
    """
    from utils.gcs_csv_reader import GCPCSVReader

    if as_of_date is None:
        as_of_date = datetime.now()

    reader = GCPCSVReader(bucket_name=bucket_name)

    for candidate in _candidate_dates(as_of_date, max_lookback_days):
        blob_path = _gcs_path(candidate)
        # Probe with an empty read — GCPCSVReader returns empty df if blob missing
        df = reader.read_csv(blob_path)
        if not df.empty:
            return f"gs://{bucket_name}/{blob_path}"

    raise FileNotFoundError(
        f"No mutualfunds.csv found in gs://{bucket_name} "
        f"looking back {max_lookback_days} business days from {as_of_date.date()}."
    )