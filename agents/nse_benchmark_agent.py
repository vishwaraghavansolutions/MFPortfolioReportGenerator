"""
agents/nse_benchmark_agent.py   

NSE Benchmark Agent  (NSEBenchmarkAgent)  
─────────────────────────────────────────
Downloads historical index data from niftyindices.com for a list of NSE
Broad Market Indices over a full calendar year, then uploads the combined
CSV to GCS winrich_shared/Master/{year}_Index_Master.csv.

The agent replicates the manual workflow on
  https://www.niftyindices.com/reports/historical-data
    Index Type     : Equity
    Sub-Index Type : Broad Market Indices
    Index          : <one per request>
    Time Period    : 01 Jan {year} → 31 Dec {year}

Uses Playwright browser automation to fill the form and intercept the
underlying network response (or fall back to clicking the CSV download
button).  Requires:  pip install playwright && playwright install chromium

Skills
──────
  download_index_data       – fetch + combine + upload for a list of indices
  list_broad_market_indices – return the built-in Broad Market Indices list

GCS output
──────────
  Bucket : winrich_shared
  Folder : Master
  File   : {year}_Index_Master.csv
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Agent, AgentResponse, AgentStatus
from .gcs_storage_agent import GCSStorageAgent

logger = logging.getLogger(__name__)

# ── Site constants ────────────────────────────────────────────────────────────
_NIFTY_BASE      = "https://www.niftyindices.com"
_HISTORICAL_PAGE = f"{_NIFTY_BASE}/reports/historical-data"

# ── Playwright timing ─────────────────────────────────────────────────────────
_PW_TIMEOUT        = 120_000  # ms – navigation / wait_for timeouts (site is slow)
_INTER_INDEX_DELAY = 2        # seconds between index fetches
_MAX_RETRIES       = 3
_RETRY_DELAY       = 4        # seconds base delay between retries (× attempt)

# ── GCS coordinates ───────────────────────────────────────────────────────────
_GCS_BUCKET = "winrich_shared"
_GCS_FOLDER = "Master"

# ── NSE Broad Market Indices (Equity → Broad Market Indices) ──────────────────
BROAD_MARKET_INDICES: List[str] = [
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY 100",
    "NIFTY 200",
    "NIFTY 500",
    "NIFTY MIDCAP 50",
    "NIFTY MIDCAP 100",
    "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 50",
    "NIFTY SMALLCAP 100",
    "NIFTY SMALLCAP 250",
    "NIFTY MIDSMALLCAP 400",
    "NIFTY LARGEMIDCAP 250",
    "NIFTY MICROCAP 250",
    "NIFTY TOTAL MARKET",
]


def _fmt_date(d: date) -> str:
    """Format a date as '01 Jan 2024' (the format niftyindices expects)."""
    return d.strftime("%d %b %Y")


# ── Playwright helpers ────────────────────────────────────────────────────────

class _NoDataFound(Exception):
    """Raised when the site returns 'No data found' for an index + date range."""


def _playwright_fetch_index(
    page: Any,
    index_name: str,
    start_date: date,
    end_date: date,
    download_dir: Path,
) -> Any:
    """
    Fill the niftyindices.com historical-data form for one index, click Submit,
    wait for the data table to populate, then click the CSV Download button.

    Workflow mirrors the manual steps:
      Index Type     → Equity
      Sub-Index Type → Broad Market Indices
      Index          → <index_name>
      Time Period    → 01 Jan {year} – 31 Dec {year}
      [Submit]
      [Download → CSV]
    """
    import pandas as pd

    # ── Navigate ──────────────────────────────────────────────────────────────
    page.goto(_HISTORICAL_PAGE, wait_until="load", timeout=_PW_TIMEOUT)
    # Wait for network to settle so JS-rendered dropdowns are available
    page.wait_for_load_state("networkidle", timeout=_PW_TIMEOUT)
    page.wait_for_timeout(2_000)

    # ── Index Type = Equity ───────────────────────────────────────────────────
    logger.info(f"    step 1/5: selecting Index Type = Equity")
    page.select_option("select#ddlHistoricaltypee", label="Equity")
    page.wait_for_timeout(1_000)

    # ── Sub-Index Type = Broad Market Indices ─────────────────────────────────
    logger.info(f"    step 2/5: waiting for Sub-Index options …")
    page.wait_for_function(
        "document.querySelector('select#ddlHistoricaltypeeSubindex').options.length > 1",
        timeout=30_000,
    )
    # Log the available sub-index labels so we can verify the exact label text
    sub_options = page.evaluate(
        "Array.from(document.querySelector('select#ddlHistoricaltypeeSubindex').options)"
        ".map(o => o.text.trim())"
    )
    logger.info(f"    sub-index options: {sub_options}")
    page.select_option("select#ddlHistoricaltypeeSubindex", label="Broad Market Indices")
    page.wait_for_timeout(1_000)

    # ── Index ─────────────────────────────────────────────────────────────────
    logger.info(f"    step 3/5: waiting for Index options …")
    page.wait_for_function(
        "document.querySelector('select#ddlHistoricaltypeeindex').options.length > 1",
        timeout=30_000,
    )
    # Log first few index labels to verify the exact label text
    idx_options = page.evaluate(
        "Array.from(document.querySelector('select#ddlHistoricaltypeeindex').options)"
        ".map(o => o.text.trim()).slice(0, 10)"
    )
    logger.info(f"    index options (first 10): {idx_options}")
    page.select_option("select#ddlHistoricaltypeeindex", label=index_name)
    page.wait_for_timeout(500)

    # ── Date range (jQuery UI datepicker — set via Date object, format-safe) ───
    page.evaluate(f"""
        // Use Date objects so we are completely independent of the datepicker
        // format string configured on the site.
        var sd = new Date({start_date.year}, {start_date.month - 1}, {start_date.day});
        var ed = new Date({end_date.year}, {end_date.month - 1}, {end_date.day});

        $('#datepickerFrom').datepicker('setDate', sd);
        $('#datepickerFrom').trigger('change');

        $('#datepickerTo').datepicker('setDate', ed);
        $('#datepickerTo').trigger('change');
    """)
    page.wait_for_timeout(500)

    # ── Submit ────────────────────────────────────────────────────────────────
    logger.info(f"    step 4/5: clicking Submit …")
    page.click("a#submit_button")

    # Wait for either the data table OR a "No data found" message — whichever
    # appears first.  networkidle fires once the AJAX response has settled.
    page.wait_for_load_state("networkidle", timeout=_PW_TIMEOUT)
    page.wait_for_timeout(1_000)

    # ── Check for "No data found" before attempting download ──────────────────
    page_text = page.inner_text("body").lower()
    if "no data found" in page_text or "no records found" in page_text:
        raise _NoDataFound(
            f"Site returned 'No data found' for '{index_name}' — "
            "index may not exist for this date range."
        )

    # ── Download CSV ──────────────────────────────────────────────────────────
    logger.info(f"    step 5/5: downloading CSV …")
    csv_file = download_dir / f"{index_name.replace(' ', '_')}_raw.csv"
    with page.expect_download(timeout=30_000) as dl_info:
        page.click("a#exporthistorical")
    download = dl_info.value
    download.save_as(str(csv_file))

    df = pd.read_csv(csv_file)
    df.columns = df.columns.str.strip()
    df["index_name"] = index_name
    logger.info(f"  {index_name}: {len(df)} rows")
    return df


# ── Lazy GCSStorageAgent accessor ─────────────────────────────────────────────
_gcs_agent_instance: Optional[GCSStorageAgent] = None

def _get_gcs_agent() -> GCSStorageAgent:
    global _gcs_agent_instance
    if _gcs_agent_instance is None:
        _gcs_agent_instance = GCSStorageAgent()
    return _gcs_agent_instance


def _upload_csv_to_gcs(
    local_path: Path,
    filename: str,
    bucket: str = _GCS_BUCKET,
    folder: str = _GCS_FOLDER,
) -> str:
    """Upload a local CSV to GCS {bucket}/{folder}/{filename}. Returns gcs_uri."""
    resp = _get_gcs_agent().run("upload_csv", {
        "file_path":   str(local_path),
        "filename":    filename,
        "bucket_name": bucket,
        "prefix":      folder,
    })
    if resp.status != AgentStatus.SUCCESS:
        raise RuntimeError(f"GCS upload failed: {resp.error}")
    return resp.output["gcs_uri"]


def _load_existing_csv_from_gcs(filename: str, bucket: str, folder: str) -> Optional["pd.DataFrame"]:
    """
    Download an existing CSV from GCS and return it as a DataFrame.
    Returns None if the file does not exist or cannot be read.
    """
    import io
    import pandas as pd
    from agents.gcs_storage_agent import _get_gcs_client
    try:
        client = _get_gcs_client()
        blob   = client.bucket(bucket).blob(f"{folder}/{filename}")
        if not blob.exists():
            logger.info(f"  No existing file at gs://{bucket}/{folder}/{filename} — will download all indices.")
            return None
        data = blob.download_as_bytes()
        df   = pd.read_csv(io.BytesIO(data))
        logger.info(f"  Loaded existing {filename}: {len(df)} rows, indices already present: {sorted(df['index_name'].unique())}")
        return df
    except Exception as exc:
        logger.warning(f"  Could not load existing CSV from GCS ({exc}) — will download all indices.")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Skill functions
# ══════════════════════════════════════════════════════════════════════════════

def skill_download_index_data(params: Dict[str, Any]) -> AgentResponse:
    """
    Download historical daily data for a list of NSE Broad Market Indices for a
    full calendar year, combine into one DataFrame, and upload to GCS.

    Required params
    ───────────────
    year        : int         – calendar year (e.g. 2024)

    Optional params
    ───────────────
    indices     : List[str]   – index names to download
                                (default: all BROAD_MARKET_INDICES)
    output_dir  : str         – local directory for temp CSVs (default: "data")
    headless    : bool        – run browser headlessly (default: True)
                                Set False to watch the browser for debugging.

    GCS output
    ──────────
    gs://winrich_shared/Master/{year}_Index_Master.csv

    Output keys
    ───────────
    gcs_uri          : str
    filename         : str
    indices_fetched  : List[str]
    indices_failed   : List[dict]   – [{index, error}, ...]
    total_rows       : int
    year             : int
    """
    import pandas as pd
    from playwright.sync_api import sync_playwright

    year = params.get("year")
    if year is None and not (params.get("start_date") and params.get("end_date")):
        return AgentResponse(status=AgentStatus.FAILED, error="'year' param is required (or provide both 'start_date' and 'end_date').")
    year = int(year) if year is not None else date.today().year

    indices: List[str] = params.get("indices") or BROAD_MARKET_INDICES
    if not isinstance(indices, list) or not indices:
        return AgentResponse(status=AgentStatus.FAILED, error="'indices' must be a non-empty list.")

    output_dir    = Path(params.get("output_dir") or "data")
    output_dir.mkdir(parents=True, exist_ok=True)
    headless:       bool = bool(params.get("headless", True))
    force_download: bool = bool(params.get("force_download", False))
    gcs_bucket:     str  = params.get("bucket") or _GCS_BUCKET
    gcs_folder:     str  = params.get("folder") or _GCS_FOLDER

    start_date = date(year, 1, 1)
    today = date.today()
    current_year = today.year
    current_month = today.month
    current_day = today.day

    if year == current_year:
        end_date = date(year, current_month, current_day)
    else:
        end_date = date(year, 12, 31)

    # ── Check existing GCS file and skip already-downloaded indices ───────────
    filename     = f"{year}_Index_Master.csv"
    existing_df: Optional["pd.DataFrame"] = None
    skipped: List[str] = []

    if not force_download:
        existing_df = _load_existing_csv_from_gcs(filename, gcs_bucket, gcs_folder)
        if existing_df is not None and "index_name" in existing_df.columns:
            already_have = set(existing_df["index_name"].unique())
            skipped  = [i for i in indices if i in already_have]
            indices  = [i for i in indices if i not in already_have]
            if skipped:
                logger.info(f"  Skipping {len(skipped)} already-present indices: {skipped}")
            if not indices:
                logger.info("  All requested indices already present — nothing to download.")
                return AgentResponse(
                    status=AgentStatus.SUCCESS,
                    output={
                        "gcs_uri":         f"gs://{gcs_bucket}/{gcs_folder}/{filename}",
                        "filename":        filename,
                        "indices_fetched": [],
                        "indices_skipped": skipped,
                        "indices_failed":  [],
                        "total_rows":      len(existing_df),
                        "year":            year,
                    },
                )

    logger.info(
        f"NSEBenchmarkAgent: downloading {len(indices)} indices "
        f"({_fmt_date(start_date)} → {_fmt_date(end_date)})  "
        f"[headless={headless}, force_download={force_download}]"
    )

    all_dfs: List["pd.DataFrame"] = []
    fetched: List[str]            = []
    errors:  List[Dict[str, str]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--start-maximized",
            ],
            # Remove the "Chrome is controlled by automated software" flag
            ignore_default_args=["--enable-automation"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation={"latitude": 19.0760, "longitude": 72.8777},  # Mumbai
            permissions=["geolocation"],
            accept_downloads=True,
            ignore_https_errors=True,
            java_script_enabled=True,
            extra_http_headers={
                "Accept-Language":          "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept":                   "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding":          "gzip, deflate, br",
                "Cache-Control":            "max-age=0",
                "Upgrade-Insecure-Requests":"1",
                "Sec-Ch-Ua":                '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile":         "?0",
                "Sec-Ch-Ua-Platform":       '"Windows"',
            },
        )
        # Comprehensive stealth: hide all automation indicators
        context.add_init_script("""
            // Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // Realistic plugins list
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin',     filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer',     filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client',         filename: 'internal-nacl-plugin' },
                ],
            });

            // Realistic languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-IN', 'en-GB', 'en'],
            });

            // Expose chrome runtime (headless Chromium omits this)
            window.chrome = {
                runtime:    {},
                loadTimes:  function() {},
                csi:        function() {},
                app:        {},
            };

            // Fix permissions query (headless returns 'denied' for notifications)
            const _origPermQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (p) =>
                p.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : _origPermQuery(p);
        """)
        page = context.new_page()

        for i, index_name in enumerate(indices):
            if i > 0:
                time.sleep(_INTER_INDEX_DELAY)

            logger.info(f"  [{i + 1}/{len(indices)}] {index_name}")
            last_exc: Optional[Exception] = None

            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    df = _playwright_fetch_index(
                        page, index_name, start_date, end_date, output_dir
                    )
                    all_dfs.append(df)
                    fetched.append(index_name)
                    last_exc = None
                    break
                except _NoDataFound as exc:
                    # Site says no data — not a transient error, skip immediately
                    logger.warning(f"  SKIPPED {index_name}: {exc}")
                    skipped.append(index_name)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        f"  Attempt {attempt}/{_MAX_RETRIES} failed for "
                        f"'{index_name}': {exc}"
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(_RETRY_DELAY * attempt)

            if last_exc is not None:
                logger.error(f"  FAILED {index_name}: {last_exc}")
                errors.append({"index": index_name, "error": str(last_exc)})

        browser.close()

    if not all_dfs:
        return AgentResponse(
            status=AgentStatus.FAILED,
            error="No index data could be fetched from niftyindices.com.",
            output={"indices_failed": errors, "year": year},
        )

    # Combine newly downloaded data with any existing data from GCS
    frames = ([existing_df] if existing_df is not None else []) + all_dfs
    combined  = pd.concat(frames, ignore_index=True)
    local_csv = output_dir / filename
    combined.to_csv(local_csv, index=False)
    logger.info(f"Saved {len(combined)} rows to {local_csv}")

    # Upload to GCS
    try:
        gcs_uri = _upload_csv_to_gcs(local_csv, filename, gcs_bucket, gcs_folder)
        logger.info(f"Uploaded to {gcs_uri}")
    except Exception as exc:
        return AgentResponse(
            status=AgentStatus.FAILED,
            error=f"Data fetched but GCS upload failed: {exc}",
            output={
                "local_file":      str(local_csv),
                "indices_fetched": fetched,
                "indices_failed":  errors,
                "total_rows":      len(combined),
                "year":            year,
            },
        )

    overall_status = AgentStatus.SUCCESS if not errors else AgentStatus.RETRY

    return AgentResponse(
        status=overall_status,
        output={
            "gcs_uri":         gcs_uri,
            "filename":        filename,
            "indices_fetched": fetched,
            "indices_skipped": skipped,
            "indices_failed":  errors,
            "total_rows":      len(combined),
            "year":            year,
        },
        metadata={
            "local_file":  str(local_csv),
            "start_date":  _fmt_date(start_date),
            "end_date":    _fmt_date(end_date),
            "bucket":      gcs_bucket,
            "folder":      gcs_folder,
        },
        error=f"{len(errors)} index fetch(es) failed" if errors else None,
    )


def skill_list_broad_market_indices(params: Dict[str, Any]) -> AgentResponse:
    """
    Return the built-in list of NSE Equity → Broad Market Indices.

    No params required.

    Output keys
    ───────────
    indices : List[str]
    count   : int
    """
    return AgentResponse(
        status=AgentStatus.SUCCESS,
        output={"indices": BROAD_MARKET_INDICES, "count": len(BROAD_MARKET_INDICES)},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Agent class
# ══════════════════════════════════════════════════════════════════════════════

class NSEBenchmarkAgent(Agent):
    """
    Downloads NSE Broad Market Index historical data from niftyindices.com
    and uploads the combined year CSV to GCS winrich_shared/Master.
    """

    name = "NSEBenchmarkAgent"

    skills = {
        "download_index_data":        skill_download_index_data,
        "list_broad_market_indices":  skill_list_broad_market_indices,
    }
