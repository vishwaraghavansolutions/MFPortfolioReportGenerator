"""
GCSStorageAgent
===============
Handles uploading files to Google Cloud Storage.

All uploads for the MF portfolio pipeline go to:
  Bucket : winrich_customer_reports   (configurable)
  Prefix : quarterly/mf_portfolio_reports/   (configurable)

Full GCS path per report:
  gs://winrich_customer_reports/quarterly/mf_portfolio_reports/<customer_folder>/<filename>

Authentication
--------------
Resolved automatically by google-auth in this priority order:
  1. GOOGLE_APPLICATION_CREDENTIALS env-var  → path to a service-account JSON key
  2. Workload Identity (GKE / Cloud Run)
  3. gcloud Application Default Credentials  (`gcloud auth application-default login`)

Skills (public)
---------------
  upload_report       – Upload a single PDF to GCS
  upload_bulk_reports – Upload a list of PDFs (one per customer) in one call
  list_reports        – List all objects under the configured prefix
  get_signed_url      – Generate a time-limited signed download URL for a GCS object
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from agents.base import Agent, AgentResponse, AgentStatus
import streamlit as st
import logging

logging.basicConfig(level=logging.INFO, format="%(threadName)s: %(message)s")


# ── Default GCS coordinates ────────────────────────────────────────────────────
_DEFAULT_BUCKET = "winrich_customer_reports"
_DEFAULT_PREFIX = "Quarterly/mf_portfolio_reports"


# ── Private helpers ─────────────────────────────────────────────────────────────

def _get_gcs_client():
    """Return an authenticated google.cloud.storage.Client. Raises on failure."""
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage is not installed — "
            "run `pip install google-cloud-storage`"
        ) from exc
    credentials_info = st.secrets.get("gcp")
    client = storage.Client.from_service_account_info(credentials_info) if credentials_info else storage.Client()
    return client

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
    # Normalise prefix — strip trailing slash then re-add exactly one
    clean_prefix = prefix.rstrip("/")
    return f"{clean_prefix}/{customer_folder}/{filename}"


# ═════════════════════════════════════════════════════════════════════════════
class GCSStorageAgent(Agent):
    """
    Agent that stores MF portfolio PDF reports in Google Cloud Storage.

    Stateless — safe to instantiate once and share across requests.
    The GCS client is created fresh per skill call so credentials are always
    resolved from the current environment (supports credential rotation).
    """

    name = "GCSStorageAgent"

    # ── Skill map ─────────────────────────────────────────────────────────────
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "upload_report":       self._upload_report,
            "upload_bulk_reports": self._upload_bulk_reports,
            "list_reports":        self._list_reports,
            "get_signed_url":      self._get_signed_url,
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

        # Object metadata stored alongside the file in GCS
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
            public_url = (
                f"https://storage.googleapis.com/{bucket_name}/{blob_name}"
            )

        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=f"GCS upload failed: {exc}",
                metadata={
                    "bucket_name":  bucket_name,
                    "blob_name":    blob_name,
                    "customer_name": customer_name,
                },
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "gcs_uri":    f"gs://{bucket_name}/{blob_name}",
                "blob_name":  blob_name,
                "bucket_name": bucket_name,
                "public_url": public_url,
                "size_bytes": size_bytes,
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

        Designed to receive the 'succeeded' list directly from
        PortfolioReportOrchestrator.run_bulk_campaign output.

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
        uploaded   : list[dict]   – {customer_name, gcs_uri, blob_name, size_bytes}
        failed     : list[dict]   – {customer_name, pdf_path, error}
        total      : int
        success_count : int
        failure_count : int

        AgentStatus
        -----------
        SUCCESS  – all reports uploaded
        RETRY    – partial success
        FAILED   – nothing uploaded / reports list empty
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
                "uploaded":     uploaded,
                "failed":       failed,
                "total":        len(reports),
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
        objects   : list[dict]   – {blob_name, gcs_uri, size_bytes, updated_at}
        total     : int
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
            client  = _get_gcs_client()
            bucket  = client.bucket(bucket_name)
            blobs   = list(bucket.list_blobs(prefix=list_prefix, max_results=max_results))
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
        Generate a time-limited signed HTTPS URL for a GCS object so the
        report can be shared with a customer without making the bucket public.

        Requires that credentials are a service account key (not ADC / WIF).
        If using Workload Identity, pass the service_account_email and ensure
        the SA has roles/iam.serviceAccountTokenCreator on itself.

        Required params
        ---------------
        blob_name     : str   – full object key (from upload_report output)
          OR
        customer_name : str + filename : str  – auto-builds the blob_name

        Optional params
        ---------------
        bucket_name         : str   – default "winrich_customer_reports"
        prefix              : str   – default "quarterly/mf_portfolio_reports"
        expiry_hours        : int   – link valid for N hours (default 24)
        service_account_email : str – required only for Workload Identity setups

        Output keys
        -----------
        signed_url    : str   – HTTPS download link
        blob_name     : str
        expires_at    : str   – ISO-8601 UTC timestamp
        expiry_hours  : int
        """
        bucket_name  = params.get("bucket_name",  _DEFAULT_BUCKET)
        prefix       = params.get("prefix",       _DEFAULT_PREFIX)
        expiry_hours = int(params.get("expiry_hours", 24))

        # Resolve blob_name
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

        expiration  = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
        sa_email    = params.get("service_account_email")

        try:
            from google.cloud import storage  # type: ignore

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