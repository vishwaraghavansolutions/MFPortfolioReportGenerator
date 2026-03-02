"""
PortfolioReportOrchestrator
============================
Top-level orchestrator that wires MFPortfolioAgent, GCSStorageAgent, and
EmailAgent together into two high-level workflows:

  1. generate_and_email_report  – generate PDF → upload to GCS → email customer
  2. run_bulk_campaign          – throttled three-phase pipeline for 750+ customers

The agent intentionally re-exposes MFPortfolioAgent.list_customers as its own
skill so callers and UI sidebars only need to interact with a single agent.

Skills (public)
---------------
  list_customers            – Thin delegation to MFPortfolioAgent._list_customers
  generate_and_email_report – Full pipeline for a single customer
  run_bulk_campaign         – Throttled bulk pipeline with concurrency + rate limits

Bulk pipeline architecture (run_bulk_campaign)
----------------------------------------------
  Three independent thread-pool phases run sequentially to avoid overloading
  any downstream system.  Each phase has its own concurrency cap and a
  token-bucket rate limiter.

  Phase 1 — PDF Generation  (ThreadPoolExecutor, pdf_workers=4)
    CPU + I/O bound.  4 workers saturates ~4 cores without exhausting RAM.

  Phase 2 — GCS Upload      (ThreadPoolExecutor, gcs_workers=10)
    Network I/O bound.  10 concurrent streams well within GCS 1000 writes/s.
    Rate capped at gcs_rate_per_sec (default 5/s).

  Phase 3 — Email Delivery  (ThreadPoolExecutor, email_workers=3)
    SMTP-sensitive.  Chunked into batches of email_batch_size (default 50)
    with a email_batch_pause_s (default 30s) cool-down between batches.
    Rate capped at email_rate_per_sec (default 2/s → 7 200/hr).
    Use RateConfig.for_sendgrid() to raise limits for SendGrid Pro.

Resumable checkpoint
--------------------
  A JSON checkpoint file (campaign_checkpoint_<date>.json by default) is
  written after each customer completes.  If the run is interrupted, restart
  with the same params and the orchestrator will skip already-completed
  customers and resume each remaining customer at the correct phase.

GCS coordinates
---------------
  Bucket : winrich_customer_reports
  Prefix : quarterly/mf_portfolio_reports
  Object : quarterly/mf_portfolio_reports/<customer_folder>/<filename>
"""

from __future__ import annotations

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from agents.base import Agent, AgentResponse, AgentStatus
from agents.mf_portfolio_agent import MFPortfolioAgent
from agents.email_agent import EmailAgent
from agents.gcs_storage_agent import GCSStorageAgent
from utils.campaign_utils import (
    TokenBucketRateLimiter,
    CampaignCheckpoint,
    RateConfig,
    chunk_list,
    with_retry,
)


# ── Module-level agent instances (shared, stateless) ─────────────────────────
_mf_agent    = MFPortfolioAgent()
_email_agent = EmailAgent()
_gcs_agent   = GCSStorageAgent()

# GCS coordinates
_GCS_BUCKET = "winrich_customer_reports"
_GCS_PREFIX = "quarterly/mf_portfolio_reports"


# ── Path helper ──────────────────────────────────────────────────────────────

def _normalise_output_dir(raw: str) -> str:
    """
    Convert any output_dir value to a clean, absolute, OS-native path and
    ensure the directory exists.

    Handles all of:
      '.'          → cwd
      './reports'  → cwd/reports  (fixes ./reports\\file.pdf on Windows)
      'reports'    → cwd/reports
      '/abs/path'  → unchanged
    """
    path = os.path.normpath(os.path.abspath(raw or "."))
    os.makedirs(path, exist_ok=True)
    return path


# ═════════════════════════════════════════════════════════════════════════════
class PortfolioReportOrchestrator(Agent):
    """
    Orchestrator that generates mutual-fund portfolio PDFs and emails them
    to customers in a single, declarative call.

    Stateless — safe to instantiate once and reuse across requests.
    """

    name = "PortfolioReportOrchestrator"

    # ── Skill map ─────────────────────────────────────────────────────────────
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "list_customers":            self._list_customers,
            "generate_and_email_report": self._generate_and_email_report,
            "run_bulk_campaign":         self._run_bulk_campaign,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 1 — list_customers   (delegation to MFPortfolioAgent)
    # ──────────────────────────────────────────────────────────────────────────
    def _list_customers(self, params: Dict[str, Any]) -> AgentResponse:
        """
        List all unique customer names from the data-warehouse CSV.

        Thin delegation to MFPortfolioAgent._list_customers — exposed here so
        UI sidebars only need to know about this orchestrator.

        Required / Optional params  →  see MFPortfolioAgent._list_customers
        Output keys                 →  all_customers, total_records
        """
        return _mf_agent.run("list_customers", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 2 — generate_and_email_report
    # ──────────────────────────────────────────────────────────────────────────
    def _generate_and_email_report(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Generate the quarterly PDF for one customer, upload it to GCS, then email it.

        Pipeline:
          1. MFPortfolioAgent.generate_quarterly_report  → pdf_path
          2. GCSStorageAgent.upload_report               → gcs_uri        (skip with skip_gcs=True)
          3. EmailAgent.send_email                       → email_sent     (skip with skip_email=True)

        Required params
        ---------------
        customer_name   : str   – exact c_name value from the CSV

        Optional params
        ---------------
        to_email        : str      – recipient address; if omitted read from CSV Email column
        csv_path        : str      – forwarded to MFPortfolioAgent
        bucket_name     : str      – GCS bucket (default "winrich_customer_reports")
        gcs_prefix      : str      – GCS prefix (default "quarterly/mf_portfolio_reports")
        parquet_dir     : str      – forwarded to MFPortfolioAgent
        as_of_date      : datetime
        model_allocation: dict
        company_name    : str      – default "Winrich Professional Services"
        output_dir      : str      – local PDF output dir (default ".")
        skip_commentary : bool     – skip AI commentary (default False)
        skip_gcs        : bool     – skip GCS upload (default False)
        skip_email      : bool     – skip email delivery (default False)
        email_subject   : str      – overrides default subject line
        transport       : str      – "smtp" | "sendgrid"

        Output keys
        -----------
        pdf_path    : str    – local path to the generated PDF
        filename    : str    – PDF basename
        gcs_uri     : str    – gs://bucket/blob  ("" if skipped or failed)
        email_sent  : bool   – whether email was delivered
        to_email    : str    – address attempted (or "")

        AgentStatus
        -----------
        SUCCESS  – PDF generated, uploaded to GCS, and email sent
        RETRY    – PDF generated; GCS or email step failed/skipped (non-fatal)
        FAILED   – PDF generation failed (hard error)
        """
        customer_name = params.get("customer_name") or params.get("selected_customer")
        if not customer_name:
            return AgentResponse(
                AgentStatus.FAILED,
                error="'customer_name' is required in params",
            )

        company_name = params.get("company_name", "Winrich Professional Services")
        output_dir   = _normalise_output_dir(params.get("output_dir", "."))
        csv_path     = params.get("csv_path", "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv")

        # ── Step 1: generate PDF ───────────────────────────────────────────────
        pdf_result = _mf_agent.run("generate_quarterly_report", {
            "customer_name":    customer_name,
            "csv_path":         csv_path,
            "bucket_name":      params.get("bucket_name", "winrich"),   # QoQ GCS bucket
            "parquet_dir":      params.get("parquet_dir", "data"),
            "as_of_date":       params.get("as_of_date",  datetime.now()),
            "model_allocation": params.get("model_allocation", {}),
            "company_name":     company_name,
            "output_dir":       output_dir,
            "skip_commentary":  params.get("skip_commentary", False),
        })

        if pdf_result.status == AgentStatus.FAILED:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"[generate_quarterly_report] {pdf_result.error}",
                metadata=pdf_result.metadata,
            )

        pdf_path = pdf_result.output["pdf_path"]
        filename = pdf_result.output["filename"]

        # ── Step 2: upload PDF to GCS ──────────────────────────────────────────
        gcs_uri       = ""
        gcs_warning   = None

        if not params.get("skip_gcs", False):
            gcs_result = _gcs_agent.run("upload_report", {
                "pdf_path":      pdf_path,
                "customer_name": customer_name,
                "filename":      filename,
                "bucket_name":   params.get("gcs_bucket", _GCS_BUCKET),
                "prefix":        params.get("gcs_prefix",  _GCS_PREFIX),
            })
            if gcs_result.status == AgentStatus.SUCCESS:
                gcs_uri = gcs_result.output["gcs_uri"]
            else:
                gcs_warning = f"GCS upload failed: {gcs_result.error}"
        else:
            gcs_warning = "GCS upload skipped (skip_gcs=True)"

        # ── Step 3: resolve recipient email ───────────────────────────────────
        to_email = params.get("to_email", "").strip()
        if not to_email:
            to_email = self.__resolve_customer_email(customer_name, csv_path)

        if params.get("skip_email", False):
            warnings = [w for w in [gcs_warning] if w]
            return AgentResponse(
                AgentStatus.RETRY,
                output={
                    "pdf_path":   pdf_path,
                    "filename":   filename,
                    "gcs_uri":    gcs_uri,
                    "email_sent": False,
                    "to_email":   to_email,
                },
                metadata={**pdf_result.metadata, "warnings": warnings, "skip_email": True},
                error="Email skipped as requested (skip_email=True)",
            )

        if not to_email:
            warnings = [w for w in [gcs_warning] if w]
            return AgentResponse(
                AgentStatus.RETRY,
                output={
                    "pdf_path":   pdf_path,
                    "filename":   filename,
                    "gcs_uri":    gcs_uri,
                    "email_sent": False,
                    "to_email":   "",
                },
                metadata={**pdf_result.metadata, "warnings": warnings,
                          "email_error": "No email address found for customer"},
                error=f"PDF generated but no email address found for '{customer_name}'",
            )

        # ── Step 4: send email ─────────────────────────────────────────────────
        report_period = datetime.now().strftime("%B %Y")
        subject = params.get(
            "email_subject",
            f"Your {report_period} Portfolio Report — {company_name}",
        )

        email_result = _email_agent.run("send_email", {
            "to_email":     to_email,
            "client_name":  customer_name,
            "subject":      subject,
            "pdf_path":     pdf_path,
            "company_name": company_name,
            "prepared_by":  company_name,
            "report_period": report_period,
            "transport":    params.get("transport", ""),
        })

        email_sent   = email_result.status == AgentStatus.SUCCESS
        all_warnings = [w for w in [gcs_warning] if w]
        final_status = AgentStatus.SUCCESS if (email_sent and not all_warnings) else AgentStatus.RETRY

        return AgentResponse(
            final_status,
            output={
                "pdf_path":   pdf_path,
                "filename":   filename,
                "gcs_uri":    gcs_uri,
                "email_sent": email_sent,
                "to_email":   to_email,
            },
            metadata={
                **pdf_result.metadata,
                "gcs_uri":         gcs_uri,
                "email_transport": email_result.output.get("transport") if email_sent else None,
                "email_error":     email_result.error if not email_sent else None,
                "warnings":        all_warnings,
            },
            error=None if (email_sent and not all_warnings)
                  else " | ".join(filter(None, [
                      f"Email failed: {email_result.error}" if not email_sent else None,
                      *all_warnings,
                  ])),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 3 — run_bulk_campaign  (throttled three-phase pipeline)
    # ──────────────────────────────────────────────────────────────────────────
    def _run_bulk_campaign(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Generate, upload, and email quarterly reports for up to 750 customers
        without overloading SMTP servers, GCS, or the local machine.

        Pipeline (three sequential phases, each with its own thread pool)
        ------------------------------------------------------------------
          Phase 1 — PDF Generation   pdf_workers concurrent threads
          Phase 2 — GCS Upload       gcs_workers concurrent threads + rate limiter
          Phase 3 — Email Delivery   email_workers threads + batch pauses + rate limiter

        Resumable: a JSON checkpoint is written after every customer so the run
        can be safely restarted after an interruption.

        Required params
        ---------------
        (none — processes all customers in the CSV by default)

        Optional params
        ---------------
        customer_names      : list[str]   – whitelist; omit to process all
        csv_path            : str
        bucket_name         : str         – QoQ data GCS bucket (default "winrich")
        parquet_dir         : str
        output_dir          : str         – local PDF dir (default ".")
        company_name        : str
        skip_commentary     : bool        – default False
        skip_gcs            : bool        – skip GCS upload (default False)
        skip_email          : bool        – dry-run, no emails (default False)
        gcs_bucket          : str         – report storage bucket
        gcs_prefix          : str         – report storage prefix
        transport           : str         – "smtp" | "sendgrid"

        Rate / concurrency overrides (all optional, see RateConfig defaults)
        -------------------------------------------------------------------
        pdf_workers         : int         – default 4
        gcs_workers         : int         – default 10
        gcs_rate_per_sec    : float       – default 5.0
        email_workers       : int         – default 3
        email_rate_per_sec  : float       – default 2.0
        email_batch_size    : int         – emails per batch window (default 50)
        email_batch_pause_s : float       – pause between batches in sec (default 30)
        max_retries         : int         – per-customer retry attempts (default 3)
        rate_preset         : str         – "smtp_free" | "sendgrid" shorthand

        Checkpoint
        ----------
        checkpoint_path     : str         – path for resume file
                                            default "campaign_checkpoint_<YYYYMMDD>.json"
        resume              : bool        – default True (auto-resume if checkpoint found)
        fresh_start         : bool        – default False (set True to ignore checkpoint)

        Output keys
        -----------
        succeeded     : list[dict]   {customer_name, pdf_path, filename, gcs_uri, to_email, email_sent}
        failed        : list[dict]   {customer_name, error}
        skipped       : list[dict]   {customer_name, reason, pdf_path?}
        total         : int
        success_count : int
        failure_count : int
        checkpoint_path : str
        duration_s    : float

        AgentStatus
        -----------
        SUCCESS  – all customers fully processed
        RETRY    – partial success (some sent, some failed/skipped)
        FAILED   – customer list unreadable, or every customer failed
        """
        t0 = time.monotonic()

        # ── Build RateConfig ──────────────────────────────────────────────────
        preset = params.get("rate_preset", "").lower()
        if preset == "sendgrid":
            cfg = RateConfig.for_sendgrid()
        elif preset == "smtp_free":
            cfg = RateConfig.for_smtp_free()
        else:
            cfg = RateConfig()

        # Allow individual overrides on top of preset
        for field in (
            "pdf_workers", "gcs_workers", "gcs_rate_per_sec",
            "email_workers", "email_rate_per_sec",
            "email_batch_size", "email_batch_pause_s", "max_retries",
        ):
            if field in params:
                setattr(cfg, field, params[field])

        # ── Resolve customer list ─────────────────────────────────────────────
        csv_path = params.get("csv_path", "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv")
        list_result = self._list_customers({"csv_path": csv_path})
        if list_result.status == AgentStatus.FAILED:
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"[list_customers] {list_result.error}",
                metadata=list_result.metadata,
            )

        all_customers: List[str] = list_result.output["all_customers"]
        whitelist: Optional[List[str]] = params.get("customer_names")
        customers_to_process = (
            [c for c in all_customers if c in whitelist]
            if whitelist else all_customers
        )
        if not customers_to_process:
            return AgentResponse(
                AgentStatus.FAILED,
                error="No matching customers found to process",
                metadata={"whitelist": whitelist, "total_in_csv": len(all_customers)},
            )

        # ── Checkpoint setup ──────────────────────────────────────────────────
        checkpoint_path = params.get(
            "checkpoint_path",
            f"campaign_checkpoint_{datetime.now().strftime('%Y%m%d')}.json",
        )
        cp = CampaignCheckpoint(checkpoint_path)
        if params.get("fresh_start", False):
            cp.clear()
        else:
            cp.load()

        # Partition customers: done, needs-resume-at-phase, or fresh
        fresh, resume_gcs, resume_email = [], [], []
        for name in customers_to_process:
            if cp.is_done(name):
                continue                       # fully complete — skip
            elif cp.has_gcs(name):
                resume_email.append(name)      # PDF + GCS done, email pending
            elif cp.has_pdf(name):
                resume_gcs.append(name)        # PDF done, GCS + email pending
            else:
                fresh.append(name)             # nothing done yet

        # Shared PDF-generation params
        shared_pdf = {k: params[k] for k in (
            "bucket_name", "parquet_dir",
            "company_name", "skip_commentary", "model_allocation", "as_of_date",
        ) if k in params}
        # Always normalise to an absolute, OS-native path and ensure it exists
        shared_pdf["output_dir"] = _normalise_output_dir(params.get("output_dir", "."))

        # ── Shared accumulators + thread-safety ───────────────────────────────
        succeeded: List[dict] = []
        failed:    List[dict] = []
        skipped:   List[dict] = []
        acc_lock = threading.Lock()

        def _record_succeeded(entry: dict) -> None:
            with acc_lock:
                succeeded.append(entry)
            cp.mark_succeeded(entry["customer_name"])
            cp.save()

        def _record_failed(name: str, error: str) -> None:
            with acc_lock:
                failed.append({"customer_name": name, "error": error})
            cp.mark_failed(name, error)
            cp.save()

        def _record_skipped(name: str, reason: str, pdf_path: str = "") -> None:
            with acc_lock:
                skipped.append({"customer_name": name, "reason": reason, "pdf_path": pdf_path})
            cp.mark_skipped(name, reason)
            cp.save()

        # ═════════════════════════════════════════════════════════════════════
        # PHASE 1 — PDF Generation
        # ═════════════════════════════════════════════════════════════════════
        # Returns {name: pdf_path} for all successful PDFs this run
        phase1_results: Dict[str, str] = {}   # name → local pdf_path

        # Seed with already-generated PDFs from checkpoint (resume_gcs + resume_email)
        for name in resume_gcs + resume_email:
            phase1_results[name] = cp.pdf_done.get(name, "")

        def _generate_pdf(name: str) -> tuple[str, Optional[str], Optional[str]]:
            """Returns (name, pdf_path|None, error|None)."""
            try:
                result = with_retry(
                    lambda: _mf_agent.run("generate_quarterly_report", {
                        **shared_pdf,
                        "csv_path":      csv_path,
                        "customer_name": name,
                    }),
                    max_retries=cfg.max_retries,
                    backoff_s=cfg.retry_backoff_s,
                    label=f"PDF:{name}",
                )
                if result.status == AgentStatus.FAILED:
                    return name, None, result.error
                return name, result.output["pdf_path"], None
            except Exception as exc:
                return name, None, str(exc)

        if fresh:
            with ThreadPoolExecutor(max_workers=cfg.pdf_workers, thread_name_prefix="pdf") as pool:
                futures = {pool.submit(_generate_pdf, name): name for name in fresh}
                for fut in as_completed(futures):
                    name, pdf_path, error = fut.result()
                    if error or not pdf_path:
                        _record_failed(name, error or "PDF generation returned no path")
                    else:
                        phase1_results[name] = pdf_path
                        cp.mark_pdf_done(name, pdf_path)
                        cp.save()

        # Customers to process in Phase 2 = fresh PDFs + resume_gcs PDFs
        phase2_names = [n for n in phase1_results if not cp.has_gcs(n)]

        # ═════════════════════════════════════════════════════════════════════
        # PHASE 2 — GCS Upload
        # ═════════════════════════════════════════════════════════════════════
        phase2_results: Dict[str, str] = {}   # name → gcs_uri

        # Seed with already-uploaded from checkpoint
        for name in resume_email:
            phase2_results[name] = cp.gcs_done.get(name, "")

        gcs_limiter = TokenBucketRateLimiter(
            rate=cfg.gcs_rate_per_sec,
            capacity=cfg.gcs_rate_per_sec * 2,
        )

        def _upload_to_gcs(name: str, pdf_path: str) -> tuple[str, Optional[str], Optional[str]]:
            """Returns (name, gcs_uri|None, error|None)."""
            gcs_limiter.acquire()
            try:
                result = with_retry(
                    lambda: _gcs_agent.run("upload_report", {
                        "pdf_path":      pdf_path,
                        "customer_name": name,
                        "filename":      os.path.basename(pdf_path),
                        "bucket_name":   params.get("gcs_bucket", _GCS_BUCKET),
                        "prefix":        params.get("gcs_prefix",  _GCS_PREFIX),
                    }),
                    max_retries=cfg.max_retries,
                    backoff_s=cfg.retry_backoff_s,
                    label=f"GCS:{name}",
                )
                if result.status == AgentStatus.SUCCESS:
                    return name, result.output["gcs_uri"], None
                return name, None, result.error
            except Exception as exc:
                return name, None, str(exc)

        if phase2_names and not params.get("skip_gcs", False):
            with ThreadPoolExecutor(max_workers=cfg.gcs_workers, thread_name_prefix="gcs") as pool:
                futures = {
                    pool.submit(_upload_to_gcs, name, phase1_results[name]): name
                    for name in phase2_names
                    if phase1_results.get(name)
                }
                for fut in as_completed(futures):
                    name, gcs_uri, error = fut.result()
                    if gcs_uri:
                        phase2_results[name] = gcs_uri
                        cp.mark_gcs_done(name, gcs_uri)
                        cp.save()
                    else:
                        # GCS failure is non-fatal — log warning, continue to email
                        phase2_results[name] = ""
                        cp.mark_gcs_done(name, "")   # mark attempted so we don't retry
                        cp.save()
        else:
            # skip_gcs — mark all phase2 names as gcs_done with empty uri
            for name in phase2_names:
                phase2_results[name] = ""

        # ═════════════════════════════════════════════════════════════════════
        # PHASE 3 — Email Delivery  (batched + rate-limited)
        # ═════════════════════════════════════════════════════════════════════
        # All customers that have a PDF (either fresh or resumed) go to email
        email_candidates = [
            n for n in list(phase1_results.keys()) + resume_email
            if not cp.is_done(n) and n not in [f["customer_name"] for f in failed]
        ]
        # De-duplicate (resume_email names are already in phase1_results keys if pdf was seeded)
        seen: set = set()
        email_candidates = [n for n in email_candidates if not (n in seen or seen.add(n))]

        email_limiter = TokenBucketRateLimiter(
            rate=cfg.email_rate_per_sec,
            capacity=cfg.email_rate_per_sec * 3,
        )
        report_period = datetime.now().strftime("%B %Y")
        company_name  = params.get("company_name", "Winrich Professional Services")

        def _send_one_email(name: str) -> tuple[str, bool, Optional[str]]:
            """Returns (name, sent:bool, error|None)."""
            to_email = self.__resolve_customer_email(name, csv_path)
            if not to_email:
                return name, False, "No email address found"

            email_limiter.acquire()
            try:
                result = with_retry(
                    lambda: _email_agent.run("send_email", {
                        "to_email":      to_email,
                        "client_name":   name,
                        "subject":       params.get(
                            "email_subject",
                            f"Your {report_period} Portfolio Report — {company_name}",
                        ),
                        "pdf_path":      phase1_results.get(name, cp.pdf_done.get(name, "")),
                        "company_name":  company_name,
                        "report_period": report_period,
                        "transport":     params.get("transport", ""),
                    }),
                    max_retries=cfg.max_retries,
                    backoff_s=cfg.retry_backoff_s,
                    label=f"Email:{name}",
                )
                sent = result.status == AgentStatus.SUCCESS
                return name, sent, None if sent else result.error
            except Exception as exc:
                return name, False, str(exc)

        if not params.get("skip_email", False):
            batches = chunk_list(email_candidates, cfg.email_batch_size)
            for batch_idx, batch in enumerate(batches):
                with ThreadPoolExecutor(
                    max_workers=cfg.email_workers,
                    thread_name_prefix="email",
                ) as pool:
                    futures = {pool.submit(_send_one_email, name): name for name in batch}
                    for fut in as_completed(futures):
                        name, sent, error = fut.result()
                        pdf_path = phase1_results.get(name, cp.pdf_done.get(name, ""))
                        gcs_uri  = phase2_results.get(name, cp.gcs_done.get(name, ""))
                        to_email = self.__resolve_customer_email(name, csv_path)

                        if sent:
                            _record_succeeded({
                                "customer_name": name,
                                "pdf_path":      pdf_path,
                                "filename":      os.path.basename(pdf_path),
                                "gcs_uri":       gcs_uri,
                                "to_email":      to_email,
                                "email_sent":    True,
                            })
                        elif error == "No email address found":
                            _record_skipped(name, error, pdf_path)
                        else:
                            _record_failed(name, f"Email: {error}")

                # Batch cool-down (skip after the last batch)
                if batch_idx < len(batches) - 1:
                    time.sleep(cfg.email_batch_pause_s)

        else:
            # skip_email — record every PDF-ready customer as skipped
            for name in email_candidates:
                pdf_path = phase1_results.get(name, cp.pdf_done.get(name, ""))
                gcs_uri  = phase2_results.get(name, cp.gcs_done.get(name, ""))
                _record_skipped(name, "skip_email=True", pdf_path)

        # ── Aggregate final status ─────────────────────────────────────────────
        overall_status = (
            AgentStatus.SUCCESS if not failed and not skipped
            else AgentStatus.RETRY  if succeeded
            else AgentStatus.FAILED
        )

        duration_s = round(time.monotonic() - t0, 1)

        return AgentResponse(
            overall_status,
            output={
                "succeeded":      succeeded,
                "failed":         failed,
                "skipped":        skipped,
                "total":          len(customers_to_process),
                "success_count":  len(succeeded),
                "failure_count":  len(failed),
                "checkpoint_path": checkpoint_path,
                "duration_s":     duration_s,
            },
            metadata={
                "skipped_count":      len(skipped),
                "all_customer_count": len(all_customers),
                "processed_count":    len(customers_to_process),
                "resumed_customers":  len(resume_gcs) + len(resume_email),
                "rate_config":        cfg.to_dict(),
                **cp.summary(),
            },
            error=f"{len(failed)} customer(s) failed" if failed else None,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Private helper — resolve customer email from CSV
    # ──────────────────────────────────────────────────────────────────────────
    def __resolve_customer_email(self, customer_name: str, csv_path: str) -> str:
        """
        Read the Email column for *customer_name* from the CSV.
        Returns the address string, or "" if not found / not a valid format.
        """
        try:
            df    = pd.read_csv(csv_path, usecols=["c_name", "Email"])
            match = df[df["c_name"] == customer_name]
            if match.empty:
                return ""
            email = str(match.iloc[0]["Email"]).strip()
            # Basic sanity — don't validate fully here, send_email will do that
            return email if "@" in email else ""
        except Exception:
            return ""