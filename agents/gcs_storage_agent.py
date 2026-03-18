"""
GCSStorageAgent
===============
Handles uploading files to Google Cloud Storage.

All uploads for the MF portfolio pipeline go to:
  Bucket : winrich_customer_reports   (configurable)
  Prefix : quarterly/mf_portfolio_reports/   (configurable)

Full GCS path per report:
  gs://winrich_customer_reports/quarterly/mf_portfolio_reports/<customer_folder>/<filename>

Ranking CSV data lives in a separate bucket/folder:
  Bucket : winrich_shared             (configurable)
  Prefix : ranking/                   (configurable)

Portfolio summary parquet (one consolidated file, indexed by customer_name):
  Bucket : winrich_shared
  Path   : data/mf_portfolio_summary/mf_portfolio_summary.parquet

Authentication
--------------
Resolved automatically by google-auth in this priority order:
  1. GOOGLE_APPLICATION_CREDENTIALS env-var  → path to a service-account JSON key
  2. Workload Identity (GKE / Cloud Run)
  3. gcloud Application Default Credentials  (`gcloud auth application-default login`)
  4. st.secrets["gcp"]                       → service account info dict in Streamlit

Skills (public)
---------------
  upload_report            – Upload a single PDF to GCS
  upload_bulk_reports      – Upload a list of PDFs (one per customer) in one call
  list_reports             – List all objects under the configured prefix
  get_signed_url           – Generate a time-limited signed download URL for a GCS object
  load_ranking_csv         – Load a fund_ranking CSV from GCS into a pandas DataFrame
  list_ranking_files       – List all CSV files available under the ranking prefix
  store_portfolio_summary  – Upsert a customer row into the shared portfolio-summary parquet
  load_portfolio_summary   – Read the portfolio-summary parquet (optionally filtered by customer)
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from agents.base import Agent, AgentResponse, AgentStatus
import streamlit as st
import logging

logging.basicConfig(level=logging.INFO, format="%(threadName)s: %(message)s")


# ── Default GCS coordinates ────────────────────────────────────────────────────
_DEFAULT_BUCKET         = "winrich_customer_reports"
_DEFAULT_PREFIX         = "Quarterly/mf_portfolio_reports"
_DEFAULT_RANKING_BUCKET = "winrich_shared"
_DEFAULT_RANKING_PREFIX = "ranking"

_DEFAULT_SUMMARY_BUCKET = "winrich_shared"
_DEFAULT_SUMMARY_PREFIX = "data/mf_portfolio_summary"
_DEFAULT_SUMMARY_FILE   = "mf_portfolio_summary.parquet"

# ── Required .streamlit/secrets.toml format ───────────────────────────────────
# [gcp]
# type = "service_account"
# project_id = "your-project-id"
# private_key_id = "key-id"
# private_key = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
# client_email = "your-sa@your-project.iam.gserviceaccount.com"
# client_id = "123456789"
# auth_uri = "https://accounts.google.com/o/oauth2/auth"
# token_uri = "https://oauth2.googleapis.com/token"
#
# Download from: GCP Console > IAM & Admin > Service Accounts > [your SA] > Keys > Add Key > JSON
# The downloaded JSON maps directly to this section (copy all fields).
# ─────────────────────────────────────────────────────────────────────────────


# ── Private helpers ─────────────────────────────────────────────────────────────

def _get_gcs_client():
    """
    Return an authenticated google.cloud.storage.Client.

    Credential resolution order:
      1. st.secrets["gcp"]  — service account info dict (Streamlit Cloud / local secrets.toml)
      2. GOOGLE_APPLICATION_CREDENTIALS env-var / Workload Identity / gcloud ADC

    Raises RuntimeError with a clear message if credentials are invalid.
    """
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage is not installed — "
            "run `pip install google-cloud-storage`"
        ) from exc

    # Try st.secrets first (safe even outside Streamlit context)
    credentials_info = None
    try:
        credentials_info = st.secrets.get("gcp")
    except Exception:
        pass  # st.secrets unavailable outside Streamlit

    if credentials_info:
        # Validate it is a service account, not an OAuth client secret
        cred_type = (
            credentials_info.get("type")
            if isinstance(credentials_info, dict)
            else getattr(credentials_info, "type", None)
        )
        if cred_type != "service_account":
            raise RuntimeError(
                f"st.secrets['gcp'] has type='{cred_type}' — expected 'service_account'. "
                "Download a service account key from GCP Console > IAM > Service Accounts > Keys."
            )
        return storage.Client.from_service_account_info(dict(credentials_info))

    # Fall through to ADC / GOOGLE_APPLICATION_CREDENTIALS env-var
    return storage.Client()


def _build_blob_name(
    customer_name: str,
    filename: str,
    prefix: str,
) -> str:
    """
    Build the full GCS object key.

    Pattern:
      <prefix>/<customer_folder>/<filename>

    customer_folder is the customer name with spaces replaced by underscores
    and lowercased so it is URL-safe and consistent across runs.

    Example:
      quarterly/mf_portfolio_reports/ramesh_kumar/portfolio_report_Ramesh_Kumar_20260302.pdf
    """
    customer_folder = customer_name.strip().lower().replace(" ", "_")
    clean_prefix = prefix.rstrip("/")
    return f"{clean_prefix}/{customer_folder}/{filename}"


# ═════════════════════════════════════════════════════════════════════════════
class GCSStorageAgent(Agent):
    """
    Agent that stores MF portfolio PDF reports in Google Cloud Storage
    and reads fund ranking CSVs from a separate data bucket.

    Stateless — safe to instantiate once and share across requests.
    The GCS client is created fresh per skill call so credentials are always
    resolved from the current environment (supports credential rotation).
    """

    name = "GCSStorageAgent"

    # ── Skill map ─────────────────────────────────────────────────────────────
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "upload_report":           self._upload_report,
            "upload_bulk_reports":     self._upload_bulk_reports,
            "list_reports":            self._list_reports,
            "get_signed_url":          self._get_signed_url,
            "load_ranking_csv":        self._load_ranking_csv,
            "list_ranking_files":      self._list_ranking_files,
            "store_portfolio_summary": self._store_portfolio_summary,
            "load_portfolio_summary":  self._load_portfolio_summary,
            "upload_csv":              self._upload_csv,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 1 — upload_report
    # ──────────────────────────────────────────────────────────────────────────
    def _upload_report(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Upload a single PDF report to GCS.

        Required params
        ---------------
        pdf_path      : str   – local filesystem path to the PDF
        customer_name : str   – used to build the GCS folder name

        Optional params
        ---------------
        filename      : str   – GCS object filename; defaults to os.path.basename(pdf_path)
        bucket_name   : str   – default "winrich_customer_reports"
        prefix        : str   – default "quarterly/mf_portfolio_reports"
        content_type  : str   – default "application/pdf"
        metadata      : dict  – custom GCS object metadata {key: value}

        Output keys
        -----------
        gcs_uri       : str   – gs://bucket/blob  (for internal references)
        blob_name     : str   – full object key within the bucket
        bucket_name   : str
        public_url    : str   – https://storage.googleapis.com/... (not signed)
        size_bytes    : int   – bytes uploaded
        """
        logging.info(f"Received upload_report request with params: {params}")
        pdf_path      = params.get("pdf_path", "").strip()
        customer_name = params.get("customer_name", "").strip()

        if not pdf_path:
            return AgentResponse(AgentStatus.FAILED, error="'pdf_path' is required")
        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")
        if not os.path.exists(pdf_path):
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"File not found: {pdf_path}",
            )

        bucket_name  = params.get("bucket_name",  _DEFAULT_BUCKET)
        prefix       = params.get("prefix",       _DEFAULT_PREFIX)
        filename     = params.get("filename",     os.path.basename(pdf_path))
        content_type = params.get("content_type", "application/pdf")
        extra_meta   = params.get("metadata",     {})

        blob_name = _build_blob_name(customer_name, filename, prefix)

        gcs_metadata = {
            "customer_name": customer_name,
            "uploaded_at":   datetime.now(timezone.utc).isoformat(),
            "source_path":   pdf_path,
            **extra_meta,
        }

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_name)
            blob.metadata = gcs_metadata

            logging.info(f"Uploading {pdf_path} to gs://{bucket_name}/{blob_name}...")
            blob.upload_from_filename(pdf_path, content_type=content_type)

            size_bytes = os.path.getsize(pdf_path)
            public_url = f"https://storage.googleapis.com/{bucket_name}/{blob_name}"

        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"GCS upload failed: {exc}",
                metadata={
                    "bucket_name":   bucket_name,
                    "blob_name":     blob_name,
                    "customer_name": customer_name,
                },
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "gcs_uri":     f"gs://{bucket_name}/{blob_name}",
                "blob_name":   blob_name,
                "bucket_name": bucket_name,
                "public_url":  public_url,
                "size_bytes":  size_bytes,
            },
            metadata={
                "customer_name": customer_name,
                "filename":      filename,
                "prefix":        prefix,
                "uploaded_at":   gcs_metadata["uploaded_at"],
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 2 — upload_bulk_reports
    # ──────────────────────────────────────────────────────────────────────────
    def _upload_bulk_reports(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Upload a list of PDF reports (one per customer) to GCS in one call.

        Required params
        ---------------
        reports : list[dict]   – each dict must contain:
                                   pdf_path      : str
                                   customer_name : str
                                 optionally:
                                   filename      : str

        Optional params (applied as defaults to every report)
        ------------------------------------------------------
        bucket_name  : str   – default "winrich_customer_reports"
        prefix       : str   – default "quarterly/mf_portfolio_reports"

        Output keys
        -----------
        uploaded      : list[dict]   – {customer_name, gcs_uri, blob_name, size_bytes}
        failed        : list[dict]   – {customer_name, pdf_path, error}
        total         : int
        success_count : int
        failure_count : int
        """
        reports: List[Dict[str, Any]] = params.get("reports", [])
        if not reports:
            return AgentResponse(AgentStatus.FAILED, error="'reports' list is required")

        defaults = {k: params[k] for k in ("bucket_name", "prefix") if k in params}
        uploaded, failed = [], []

        for entry in reports:
            result = self._upload_report({**defaults, **entry})
            if result.status == AgentStatus.SUCCESS:
                uploaded.append({
                    "customer_name": entry.get("customer_name"),
                    "gcs_uri":       result.output["gcs_uri"],
                    "blob_name":     result.output["blob_name"],
                    "size_bytes":    result.output["size_bytes"],
                })
            else:
                failed.append({
                    "customer_name": entry.get("customer_name"),
                    "pdf_path":      entry.get("pdf_path"),
                    "error":         result.error,
                })

        overall_status = (
            AgentStatus.SUCCESS if not failed
            else AgentStatus.RETRY  if uploaded
            else AgentStatus.FAILED
        )

        return AgentResponse(
            overall_status,
            output={
                "uploaded":      uploaded,
                "failed":        failed,
                "total":         len(reports),
                "success_count": len(uploaded),
                "failure_count": len(failed),
            },
            metadata={"partial": bool(uploaded and failed)},
            error=f"{len(failed)} upload(s) failed" if failed else None,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 3 — list_reports
    # ──────────────────────────────────────────────────────────────────────────
    def _list_reports(self, params: Dict[str, Any]) -> AgentResponse:
        """
        List all PDF objects stored under the configured GCS prefix.

        Optional params
        ---------------
        bucket_name    : str   – default "winrich_customer_reports"
        prefix         : str   – default "quarterly/mf_portfolio_reports"
        customer_name  : str   – if set, narrow listing to that customer's folder
        max_results    : int   – cap results (default 1000)

        Output keys
        -----------
        objects     : list[dict]   – {blob_name, gcs_uri, size_bytes, updated_at}
        total       : int
        prefix_used : str
        """
        bucket_name   = params.get("bucket_name",  _DEFAULT_BUCKET)
        prefix        = params.get("prefix",       _DEFAULT_PREFIX).rstrip("/")
        customer_name = params.get("customer_name", "").strip()
        max_results   = params.get("max_results",  1000)

        list_prefix = (
            f"{prefix}/{customer_name.lower().replace(' ', '_')}/"
            if customer_name else f"{prefix}/"
        )

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blobs  = list(bucket.list_blobs(prefix=list_prefix, max_results=max_results))
        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"GCS list failed: {exc}",
                metadata={"bucket_name": bucket_name, "prefix": list_prefix},
            )

        objects = [
            {
                "blob_name":  b.name,
                "gcs_uri":    f"gs://{bucket_name}/{b.name}",
                "size_bytes": b.size,
                "updated_at": b.updated.isoformat() if b.updated else None,
            }
            for b in blobs
        ]

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "objects":     objects,
                "total":       len(objects),
                "prefix_used": list_prefix,
            },
            metadata={"bucket_name": bucket_name},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 4 — get_signed_url
    # ──────────────────────────────────────────────────────────────────────────
    def _get_signed_url(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Generate a time-limited signed HTTPS URL for a GCS object.

        Required params
        ---------------
        blob_name     : str   – full object key (from upload_report output)
          OR
        customer_name : str + filename : str  – auto-builds the blob_name

        Optional params
        ---------------
        bucket_name           : str   – default "winrich_customer_reports"
        prefix                : str   – default "quarterly/mf_portfolio_reports"
        expiry_hours          : int   – link valid for N hours (default 24)
        service_account_email : str   – required only for Workload Identity setups

        Output keys
        -----------
        signed_url   : str   – HTTPS download link
        blob_name    : str
        expires_at   : str   – ISO-8601 UTC timestamp
        expiry_hours : int
        """
        bucket_name  = params.get("bucket_name",  _DEFAULT_BUCKET)
        prefix       = params.get("prefix",       _DEFAULT_PREFIX)
        expiry_hours = int(params.get("expiry_hours", 24))

        blob_name = params.get("blob_name", "").strip()
        if not blob_name:
            customer_name = params.get("customer_name", "").strip()
            filename      = params.get("filename", "").strip()
            if not customer_name or not filename:
                return AgentResponse(
                    AgentStatus.FAILED,
                    error="Provide either 'blob_name' or both 'customer_name' and 'filename'",
                )
            blob_name = _build_blob_name(customer_name, filename, prefix)

        expiration = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
        sa_email   = params.get("service_account_email")

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_name)

            kwargs: Dict[str, Any] = {
                "expiration": expiration,
                "method":     "GET",
                "version":    "v4",
            }
            if sa_email:
                kwargs["service_account_email"] = sa_email

            signed_url = blob.generate_signed_url(**kwargs)

        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"Signed URL generation failed: {exc}",
                metadata={"blob_name": blob_name, "bucket_name": bucket_name},
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "signed_url":  signed_url,
                "blob_name":   blob_name,
                "expires_at":  expiration.isoformat(),
                "expiry_hours": expiry_hours,
            },
            metadata={"bucket_name": bucket_name},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 5 — load_ranking_csv  (NEW)
    # ──────────────────────────────────────────────────────────────────────────
    def _load_ranking_csv(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Load a fund ranking CSV from GCS into a pandas DataFrame.

        The ranking data lives in a separate bucket from the report PDFs,
        but uses the same GCS credentials (st.secrets["gcp"]).

        Required params
        ---------------
        filename      : str   – CSV filename, e.g. "fund_ranking.csv"
                                or a specific category file like
                                "Large_Cap_Fund_ranking.csv"

        Optional params
        ---------------
        bucket_name   : str   – default "winrich_shared"
        prefix        : str   – default "ranking"
                                Set to "" to read from the bucket root.

        Output keys
        -----------
        dataframe     : pd.DataFrame  – the loaded CSV as a DataFrame
        row_count     : int
        columns       : list[str]
        gcs_uri       : str           – gs://bucket/blob_name used
        blob_name     : str

        Usage example
        -------------
          resp = gcs_agent.run("load_ranking_csv", {"filename": "fund_ranking.csv"})
          if resp.status == AgentStatus.SUCCESS:
              df = resp.output["dataframe"]
        """
        try:
            import pandas as pd
        except ImportError:
            return AgentResponse(AgentStatus.FAILED, error="pandas is not installed")

        filename = params.get("filename", "").strip()
        if not filename:
            return AgentResponse(AgentStatus.FAILED, error="'filename' is required")

        bucket_name = params.get("bucket_name", _DEFAULT_RANKING_BUCKET)
        prefix      = params.get("prefix",      _DEFAULT_RANKING_PREFIX).rstrip("/")

        blob_name = f"{prefix}/{filename}" if prefix else filename
        gcs_uri   = f"gs://{bucket_name}/{blob_name}"

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_name)

            logging.info(f"Loading ranking CSV from {gcs_uri}")
            raw_bytes = blob.download_as_bytes()
            df        = pd.read_csv(io.BytesIO(raw_bytes))

            # Normalise column names — strip whitespace
            df.columns = df.columns.str.strip()

            # Verify we actually have a DataFrame (not a dict or other type)
            if not isinstance(df, pd.DataFrame):
                raise RuntimeError(f"pd.read_csv returned {type(df)}, expected DataFrame")

        except Exception as exc:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"Could not load ranking CSV from {gcs_uri}: {exc}",
                metadata={"bucket_name": bucket_name, "blob_name": blob_name},
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "dataframe": df,
                "row_count": len(df),
                "columns":   list(df.columns),
                "gcs_uri":   gcs_uri,
                "blob_name": blob_name,
            },
            metadata={
                "bucket_name": bucket_name,
                "filename":    filename,
                "prefix":      prefix,
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 6 — list_ranking_files  (NEW)
    # ──────────────────────────────────────────────────────────────────────────
    def _list_ranking_files(self, params: Dict[str, Any]) -> AgentResponse:
        """
        List all CSV files available under the ranking prefix in GCS.

        Useful for discovering which category ranking files have been uploaded
        (e.g. Large_Cap_Fund_ranking.csv, Mid_Cap_Fund_ranking.csv).

        Optional params
        ---------------
        bucket_name   : str   – default "winrich_shared"
        prefix        : str   – default "ranking"

        Output keys
        -----------
        files         : list[dict]  – {filename, blob_name, gcs_uri, size_bytes, updated_at}
        total         : int
        """
        bucket_name = params.get("bucket_name", _DEFAULT_RANKING_BUCKET)
        prefix      = params.get("prefix",      _DEFAULT_RANKING_PREFIX).rstrip("/") + "/"

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blobs  = list(bucket.list_blobs(prefix=prefix))
        except Exception as exc:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"Could not list ranking files from gs://{bucket_name}/{prefix}: {exc}",
                metadata={"bucket_name": bucket_name, "prefix": prefix},
            )

        # Filter to CSV files only
        files = [
            {
                "filename":   os.path.basename(b.name),
                "blob_name":  b.name,
                "gcs_uri":    f"gs://{bucket_name}/{b.name}",
                "size_bytes": b.size,
                "updated_at": b.updated.isoformat() if b.updated else None,
            }
            for b in blobs
            if b.name.endswith(".csv")
        ]

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "files": files,
                "total": len(files),
            },
            metadata={"bucket_name": bucket_name, "prefix": prefix},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 7 — store_portfolio_summary
    # ──────────────────────────────────────────────────────────────────────────
    def _store_portfolio_summary(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Upsert a customer's metrics row into the shared portfolio-summary parquet.

        The parquet file holds one row per customer (indexed by customer_name).
        If the file does not yet exist it is created from scratch.
        If a row for this customer already exists it is replaced.

        Required params
        ---------------
        customer_name : str   – primary key (case-sensitive)
        summary_row   : dict  – flat dict of metric columns to store

        Optional params
        ---------------
        bucket_name   : str   – default "winrich_shared"
        prefix        : str   – default "data/mf_portfolio_summary"
        filename      : str   – default "mf_portfolio_summary.parquet"

        Output keys
        -----------
        gcs_uri       : str   – gs://bucket/blob_name
        blob_name     : str
        row_count     : int   – total rows in the file after upsert
        """
        try:
            import pandas as pd
        except ImportError:
            return AgentResponse(AgentStatus.FAILED, error="pandas is not installed")

        customer_name: str = params.get("customer_name", "").strip()
        summary_row: Dict[str, Any] = params.get("summary_row", {})

        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")
        if not summary_row:
            return AgentResponse(AgentStatus.FAILED, error="'summary_row' is required")

        bucket_name = params.get("bucket_name", _DEFAULT_SUMMARY_BUCKET)
        prefix      = params.get("prefix",      _DEFAULT_SUMMARY_PREFIX).rstrip("/")
        filename    = params.get("filename",     _DEFAULT_SUMMARY_FILE)
        blob_name   = f"{prefix}/{filename}"
        gcs_uri     = f"gs://{bucket_name}/{blob_name}"

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_name)

            # ── Load existing data (if any) ────────────────────────────────
            existing_df: pd.DataFrame
            if blob.exists():
                logging.info(f"Downloading existing portfolio summary from {gcs_uri}")
                raw = blob.download_as_bytes()
                existing_df = pd.read_parquet(io.BytesIO(raw))
                # Drop the old row for this customer so we can upsert
                existing_df = existing_df[existing_df["customer_name"] != customer_name]
            else:
                existing_df = pd.DataFrame()

            # ── Build new row ──────────────────────────────────────────────
            new_row = {"customer_name": customer_name, **summary_row}
            new_df  = pd.DataFrame([new_row])

            # ── Merge and upload ───────────────────────────────────────────
            merged_df = pd.concat([existing_df, new_df], ignore_index=True)

            buf = io.BytesIO()
            merged_df.to_parquet(buf, index=False, engine="pyarrow")
            buf.seek(0)

            logging.info(f"Uploading portfolio summary ({len(merged_df)} rows) to {gcs_uri}")
            blob.upload_from_file(buf, content_type="application/octet-stream")

        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"store_portfolio_summary failed: {exc}",
                metadata={"bucket_name": bucket_name, "blob_name": blob_name,
                          "customer_name": customer_name},
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "gcs_uri":   gcs_uri,
                "blob_name": blob_name,
                "row_count": len(merged_df),
            },
            metadata={"bucket_name": bucket_name, "customer_name": customer_name},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 8 — load_portfolio_summary
    # ──────────────────────────────────────────────────────────────────────────
    def _load_portfolio_summary(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Read the shared portfolio-summary parquet from GCS.

        Optional params
        ---------------
        customer_name : str   – if provided, returns only that customer's row(s)
        bucket_name   : str   – default "winrich_shared"
        prefix        : str   – default "data/mf_portfolio_summary"
        filename      : str   – default "mf_portfolio_summary.parquet"

        Output keys
        -----------
        dataframe     : pd.DataFrame
        row_count     : int
        columns       : list[str]
        gcs_uri       : str
        """
        try:
            import pandas as pd
        except ImportError:
            return AgentResponse(AgentStatus.FAILED, error="pandas is not installed")

        bucket_name   = params.get("bucket_name",   _DEFAULT_SUMMARY_BUCKET)
        prefix        = params.get("prefix",        _DEFAULT_SUMMARY_PREFIX).rstrip("/")
        filename      = params.get("filename",      _DEFAULT_SUMMARY_FILE)
        customer_name = params.get("customer_name", "").strip()

        blob_name = f"{prefix}/{filename}"
        gcs_uri   = f"gs://{bucket_name}/{blob_name}"

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_name)

            if not blob.exists():
                return AgentResponse(
                    AgentStatus.SUCCESS,
                    output={
                        "dataframe": pd.DataFrame(),
                        "row_count": 0,
                        "columns":   [],
                        "gcs_uri":   gcs_uri,
                    },
                    metadata={"bucket_name": bucket_name, "blob_name": blob_name,
                              "note": "file does not exist yet"},
                )

            logging.info(f"Loading portfolio summary from {gcs_uri}")
            raw = blob.download_as_bytes()
            df  = pd.read_parquet(io.BytesIO(raw))

        except Exception as exc:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"load_portfolio_summary failed: {exc}",
                metadata={"bucket_name": bucket_name, "blob_name": blob_name},
            )

        if customer_name:
            df = df[df["customer_name"] == customer_name].reset_index(drop=True)

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "dataframe": df,
                "row_count": len(df),
                "columns":   list(df.columns),
                "gcs_uri":   gcs_uri,
            },
            metadata={"bucket_name": bucket_name, "blob_name": blob_name,
                      "customer_filter": customer_name or None},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 9 — upload_csv
    # ──────────────────────────────────────────────────────────────────────────
    def _upload_csv(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Upload a local CSV file to GCS at {prefix}/{filename}.

        Unlike upload_report, this skill does NOT add a customer subfolder —
        the blob path is simply <prefix>/<filename>.  Use it for shared master
        data files (e.g. mf_benchmark_map.csv).

        Required params
        ---------------
        file_path     : str   – local filesystem path to the CSV
        filename      : str   – GCS object filename (e.g. "mf_benchmark_map.csv")

        Optional params
        ---------------
        bucket_name   : str   – default "winrich_shared"
        prefix        : str   – default "master"

        Output keys
        -----------
        gcs_uri       : str   – gs://bucket/blob_name
        blob_name     : str
        bucket_name   : str
        public_url    : str
        size_bytes    : int
        """
        file_path = params.get("file_path", "").strip()
        filename  = params.get("filename",  "").strip()

        if not file_path:
            return AgentResponse(AgentStatus.FAILED, error="'file_path' is required")
        if not filename:
            return AgentResponse(AgentStatus.FAILED, error="'filename' is required")
        if not os.path.exists(file_path):
            return AgentResponse(AgentStatus.FAILED, error=f"File not found: {file_path}")

        bucket_name = params.get("bucket_name", _DEFAULT_RANKING_BUCKET)
        prefix      = params.get("prefix",      "master").rstrip("/")
        blob_name   = f"{prefix}/{filename}" if prefix else filename
        gcs_uri     = f"gs://{bucket_name}/{blob_name}"

        try:
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_name)
            blob.metadata = {"uploaded_at": datetime.now(timezone.utc).isoformat(),
                             "source_path": file_path}

            logging.info(f"Uploading {file_path} to {gcs_uri}...")
            blob.upload_from_filename(file_path, content_type="text/csv")

            size_bytes = os.path.getsize(file_path)
            public_url = f"https://storage.googleapis.com/{bucket_name}/{blob_name}"

        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"GCS upload_csv failed: {exc}",
                metadata={"bucket_name": bucket_name, "blob_name": blob_name},
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "gcs_uri":    gcs_uri,
                "blob_name":  blob_name,
                "bucket_name": bucket_name,
                "public_url": public_url,
                "size_bytes": size_bytes,
            },
            metadata={"filename": filename, "prefix": prefix},
        )