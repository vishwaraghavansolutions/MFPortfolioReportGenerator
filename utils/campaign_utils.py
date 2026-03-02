"""
utils/campaign_utils.py
=======================
Shared utilities for the throttled bulk campaign in PortfolioReportOrchestrator.

Contents
--------
  TokenBucketRateLimiter  – thread-safe token-bucket algorithm for per-second
                             or per-minute rate limiting
  CampaignCheckpoint      – JSON-backed checkpoint so an interrupted 750-customer
                             run can resume without reprocessing completed customers
  RateConfig              – dataclass holding all tunable rate/concurrency limits
  chunk_list              – split a flat list into batches of size n
  with_retry              – call a fn with exponential back-off
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


# ──────────────────────────────────────────────────────────────────────────────
# TokenBucketRateLimiter
# ──────────────────────────────────────────────────────────────────────────────

class TokenBucketRateLimiter:
    """
    Thread-safe token-bucket rate limiter.

    Callers call .acquire() before each operation. The call blocks (sleeps)
    until a token is available, giving smooth sustained throughput rather than
    bursty behaviour.

    Parameters
    ----------
    rate     : float   tokens per second  (e.g. 2.0 = 2 ops/sec)
    capacity : float   burst capacity     (e.g. 10  = allow burst of 10 then throttle)
    """

    def __init__(self, rate: float, capacity: float):
        self._rate     = rate
        self._capacity = capacity
        self._tokens   = capacity
        self._last     = time.monotonic()
        self._lock     = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until *tokens* tokens are available, then consume them."""
        while True:
            with self._lock:
                now          = time.monotonic()
                delta        = now - self._last
                self._last   = now
                self._tokens = min(self._capacity, self._tokens + delta * self._rate)

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

                wait = (tokens - self._tokens) / self._rate

            # Sleep outside the lock so other threads aren't starved
            time.sleep(wait)

    @property
    def rate(self) -> float:
        return self._rate


# ──────────────────────────────────────────────────────────────────────────────
# RateConfig
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RateConfig:
    """
    All tunable concurrency and rate parameters for a bulk campaign run.

    Sensible defaults for 750 customers balanced against real-world limits:

    PDF generation
    --------------
    pdf_workers : 4      CPU + I/O bound; 4 workers ≈ 4 cores, ~2–4 MB RAM each

    GCS upload
    ----------
    gcs_workers       : 10    GCS allows 1000 writes/s per bucket; 10 concurrent
                              streams is comfortable without triggering throttling
    gcs_rate_per_sec  : 5.0   Hard cap as an extra safety net

    Email (SMTP / SendGrid)
    -----------------------
    email_workers      : 3     Keep SMTP connections low; most servers limit
                               simultaneous auth sessions
    email_rate_per_sec : 2.0   2/sec = 120/min = 7 200/hr — fits within
                               Google Workspace (2 000/day) across a few hours.
                               Raise to 20 for SendGrid Pro (100/s limit).
    email_batch_size   : 50    Number of emails per burst window
    email_batch_pause_s: 30    Cool-down between batches to avoid spam filters

    Retry
    -----
    max_retries      : 3
    retry_backoff_s  : 5    Doubles on each attempt (exponential back-off)
    """

    # PDF phase
    pdf_workers:            int   = 4

    # GCS phase
    gcs_workers:            int   = 10
    gcs_rate_per_sec:       float = 5.0

    # Email phase
    email_workers:          int   = 3
    email_rate_per_sec:     float = 2.0
    email_batch_size:       int   = 50
    email_batch_pause_s:    float = 30.0

    # Retry
    max_retries:            int   = 3
    retry_backoff_s:        float = 5.0

    @classmethod
    def for_sendgrid(cls) -> "RateConfig":
        """Relaxed limits for SendGrid Pro (100 req/s, no daily cap)."""
        return cls(
            pdf_workers=6,
            gcs_workers=15,
            gcs_rate_per_sec=10.0,
            email_workers=10,
            email_rate_per_sec=20.0,
            email_batch_size=100,
            email_batch_pause_s=10.0,
        )

    @classmethod
    def for_smtp_free(cls) -> "RateConfig":
        """Conservative limits for free Gmail / personal SMTP (~100/day cap)."""
        return cls(
            pdf_workers=2,
            gcs_workers=5,
            gcs_rate_per_sec=2.0,
            email_workers=1,
            email_rate_per_sec=0.3,    # ~18/min → safely under 100/day over 6 hrs
            email_batch_size=20,
            email_batch_pause_s=120.0,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# CampaignCheckpoint
# ──────────────────────────────────────────────────────────────────────────────

class CampaignCheckpoint:
    """
    JSON-backed checkpoint that tracks campaign progress per customer.

    Persists four independent state sets so a partial failure in any phase
    (PDF / GCS / Email) can be resumed at exactly the right step:

      succeeded : set   – customers whose full pipeline completed
      pdf_done  : dict  – {name: local_pdf_path}   (PDF generated, awaiting GCS+email)
      gcs_done  : dict  – {name: gcs_uri}           (uploaded, awaiting email)
      failed    : dict  – {name: error_message}
      skipped   : dict  – {name: reason}

    On restart the orchestrator loads the checkpoint and routes each customer
    to the correct phase rather than restarting from scratch.

    Usage
    -----
        cp = CampaignCheckpoint("/tmp/campaign_Q1_2025.json")
        cp.load()

        if cp.is_done("Ramesh Kumar"):
            continue
        if cp.has_pdf("Ramesh Kumar"):
            pdf_path = cp.pdf_done["Ramesh Kumar"]   # skip PDF generation

        cp.mark_pdf_done("Ramesh Kumar", pdf_path)
        cp.mark_gcs_done("Ramesh Kumar", gcs_uri)
        cp.mark_succeeded("Ramesh Kumar")
        cp.save()   # call after every customer or small batch
    """

    def __init__(self, path: str):
        self._path      = path
        self._lock      = threading.Lock()
        self.succeeded: Set[str]        = set()
        self.pdf_done:  Dict[str, str]  = {}   # name → local pdf_path
        self.gcs_done:  Dict[str, str]  = {}   # name → gcs_uri
        self.failed:    Dict[str, str]  = {}   # name → error
        self.skipped:   Dict[str, str]  = {}   # name → reason
        self.started_at: Optional[str]  = None
        self.updated_at: Optional[str]  = None

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> bool:
        """Load state from disk. Returns True if a previous checkpoint was found."""
        if not os.path.exists(self._path):
            self.started_at = datetime.now(timezone.utc).isoformat()
            return False
        try:
            with open(self._path) as f:
                data = json.load(f)
            self.succeeded  = set(data.get("succeeded", []))
            self.pdf_done   = data.get("pdf_done",  {})
            self.gcs_done   = data.get("gcs_done",  {})
            self.failed     = data.get("failed",    {})
            self.skipped    = data.get("skipped",   {})
            self.started_at = data.get("started_at")
            return True
        except (json.JSONDecodeError, KeyError):
            return False

    def save(self) -> None:
        """Persist current state atomically (temp file + os.replace)."""
        self.updated_at = datetime.now(timezone.utc).isoformat()
        data = {
            "succeeded":  list(self.succeeded),
            "pdf_done":   self.pdf_done,
            "gcs_done":   self.gcs_done,
            "failed":     self.failed,
            "skipped":    self.skipped,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._path)

    def clear(self) -> None:
        """Reset all state and delete the checkpoint file."""
        with self._lock:
            self.succeeded.clear()
            self.pdf_done.clear()
            self.gcs_done.clear()
            self.failed.clear()
            self.skipped.clear()
            self.started_at = datetime.now(timezone.utc).isoformat()
        if os.path.exists(self._path):
            os.remove(self._path)

    # ── State queries ─────────────────────────────────────────────────────────

    def is_done(self, name: str) -> bool:
        return name in self.succeeded

    def has_pdf(self, name: str) -> bool:
        return name in self.pdf_done

    def has_gcs(self, name: str) -> bool:
        return name in self.gcs_done

    # ── Thread-safe mutations ─────────────────────────────────────────────────

    def mark_pdf_done(self, name: str, pdf_path: str) -> None:
        with self._lock:
            self.pdf_done[name] = pdf_path
            self.failed.pop(name, None)

    def mark_gcs_done(self, name: str, gcs_uri: str) -> None:
        with self._lock:
            self.gcs_done[name] = gcs_uri

    def mark_succeeded(self, name: str) -> None:
        with self._lock:
            self.succeeded.add(name)
            self.failed.pop(name, None)
            self.skipped.pop(name, None)

    def mark_failed(self, name: str, error: str) -> None:
        with self._lock:
            self.failed[name] = error

    def mark_skipped(self, name: str, reason: str) -> None:
        with self._lock:
            self.skipped[name] = reason
            self.failed.pop(name, None)

    def summary(self) -> Dict[str, Any]:
        return {
            "succeeded":      len(self.succeeded),
            "pdf_done":       len(self.pdf_done),
            "gcs_done":       len(self.gcs_done),
            "failed":         len(self.failed),
            "skipped":        len(self.skipped),
            "started_at":     self.started_at,
            "updated_at":     self.updated_at,
            "checkpoint_path": self._path,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def chunk_list(lst: list, size: int) -> List[list]:
    """Split *lst* into sublists of at most *size* items."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def with_retry(
    fn,
    max_retries: int = 3,
    backoff_s:   float = 5.0,
    label:       str = "",
) -> Any:
    """
    Call fn() up to max_retries times with exponential back-off.
    Returns the result on success or re-raises the last exception.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            time.sleep(backoff_s * (2 ** attempt))
    raise RuntimeError(
        f"{label} failed after {max_retries} retries: {last_exc}"
    ) from last_exc