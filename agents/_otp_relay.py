"""
agents/_otp_relay.py
─────────────────────
Shared-file relay used to pass an OTP from EmailOTPAgent → AngelOneAgent.

The relay file is a small JSON document:
    {
        "otp":        "123456",
        "issued_at":  "2026-03-16T10:30:00.123456",   # UTC ISO-8601
        "subject":    "Your OTP for AngelOne Login",
        "sender":     "noreply@angelone.in"
    }

AngelOneAgent calls `read_fresh_otp()` in a poll loop.
EmailOTPAgent calls `write_otp(...)` as soon as it finds the email.
`clear_otp()` should be called after login succeeds to avoid stale reuse.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default relay file location – override via env var ANGELONE_OTP_RELAY_PATH
_DEFAULT_RELAY_PATH = Path.home() / ".angelone" / "otp_relay.json"
_MAX_AGE_SECONDS    = 300   # 5 minutes; older OTPs are treated as stale


def _relay_path() -> Path:
    custom = os.environ.get("ANGELONE_OTP_RELAY_PATH", "")
    p = Path(custom) if custom else _DEFAULT_RELAY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_otp(otp: str, subject: str = "", sender: str = "") -> None:
    """Write a fresh OTP to the relay file (called by EmailOTPAgent)."""
    payload = {
        "otp":       otp,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "subject":   subject,
        "sender":    sender,
    }
    path = _relay_path()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("[otp_relay] Wrote OTP=%r to %s", otp, path)


def read_fresh_otp(max_age_seconds: int = _MAX_AGE_SECONDS) -> Optional[str]:
    """
    Read the relay file and return the OTP if it was written within
    `max_age_seconds` seconds.  Returns None if missing, unreadable, or stale.
    """
    path = _relay_path()
    if not path.exists():
        return None
    try:
        payload    = json.loads(path.read_text(encoding="utf-8"))
        issued_str = payload.get("issued_at", "")
        issued_at  = datetime.fromisoformat(issued_str)
        # Make aware if naive (shouldn't happen, but be safe)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - issued_at).total_seconds()
        if age > max_age_seconds:
            logger.debug("[otp_relay] OTP is stale (age=%.0fs > %ds)", age, max_age_seconds)
            return None
        otp = payload.get("otp", "").strip()
        if not otp:
            return None
        logger.info("[otp_relay] Read fresh OTP=%r (age=%.0fs, subject=%r)", otp, age, payload.get("subject"))
        return otp
    except Exception as exc:
        logger.warning("[otp_relay] Could not read relay file: %s", exc)
        return None


def clear_otp() -> None:
    """Delete the relay file after successful login to prevent stale reuse."""
    path = _relay_path()
    if path.exists():
        path.unlink()
        logger.info("[otp_relay] Relay file cleared: %s", path)
