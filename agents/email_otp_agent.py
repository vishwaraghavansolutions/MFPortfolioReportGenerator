"""
agents/email_otp_agent.py
──────────────────────────
Monitors https://outlook.office.com/mail/ for Angel One OTP emails
using Playwright browser automation.

No API keys, no Azure app registration, no IMAP needed.
Works locally and on GCP (headless Chromium).

Configuration (env vars or skill params)
─────────────────────────────────────────
    OWA_USERNAME   full email address     knsridharan@w2pinvest.com
    OWA_PASSWORD   account password

Usage
─────
    from agents.email_otp_agent import EmailOTPAgent
    agent = EmailOTPAgent()
    resp  = agent.run("monitor_for_otp", {"timeout": 120})
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

from agents.base import Agent, AgentResponse, AgentStatus
from agents._otp_relay import write_otp

logger = logging.getLogger(__name__)

_OTP_RE = re.compile(r"\b(\d{6})\b")

_ANGELONE_SENDER_PATTERNS = ["angelone.in", "angelbroking.com", "angel-one.in"]
_OTP_SUBJECT_KEYWORDS     = ["otp", "one time", "one-time", "verification", "login"]

_OWA_URL = "https://outlook.office.com/mail/"


class EmailOTPAgent(Agent):
    """OWA Playwright-based OTP monitor for Angel One login emails."""

    name = "EmailOTPAgent"

    def __init__(self) -> None:
        self.skills: Dict[str, Callable] = {
            "monitor_for_otp": self._skill_monitor,
            "check_once":      self._skill_check_once,
        }

    # ── skills ────────────────────────────────────────────────────────────────

    def _skill_monitor(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Open OWA, log in, then poll inbox until an OTP email arrives.

        Params
        ──────
        timeout        int   Total seconds to keep scanning      [120]
        poll_interval  int   Seconds between inbox checks         [5]
        username       str   OWA email  (or OWA_USERNAME env var)
        password       str   OWA password (or OWA_PASSWORD env var)
        headless       bool  Run browser invisibly                [True]
        """
        timeout       = int(params.get("timeout",       120))
        poll_interval = int(params.get("poll_interval", 5))
        username      = params.get("username", os.environ.get("OWA_USERNAME", ""))
        password      = params.get("password", os.environ.get("OWA_PASSWORD", ""))
        headless      = params.get("headless", True)

        if not username or not password:
            return AgentResponse(
                status = AgentStatus.FAILED,
                error  = "OWA_USERNAME and OWA_PASSWORD are required",
            )

        logger.info("[EmailOTPAgent] Opening OWA in browser (headless=%s) …", headless)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AgentResponse(
                status = AgentStatus.FAILED,
                error  = "playwright not installed. Run: pip install playwright && playwright install chromium",
            )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            page    = browser.new_page()

            try:
                self._owa_login(page, username, password)
            except Exception as exc:
                browser.close()
                return AgentResponse(status=AgentStatus.FAILED, error=f"OWA login failed: {exc}")

            logger.info("[EmailOTPAgent] OWA login successful. Polling inbox …")

            deadline = time.time() + timeout
            attempt  = 0
            result   = None

            while time.time() < deadline:
                attempt += 1
                try:
                    result = self._scan_inbox(page)
                except Exception as exc:
                    logger.warning("[EmailOTPAgent] Poll #%d error: %s", attempt, exc)

                if result:
                    break

                remaining = deadline - time.time()
                sleep_for = min(poll_interval, remaining)
                if sleep_for > 0:
                    logger.debug("[EmailOTPAgent] Poll #%d: no OTP yet. Sleeping %.0fs …", attempt, sleep_for)
                    time.sleep(sleep_for)
                    # Refresh inbox
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=15_000)
                    except Exception:
                        pass

            browser.close()

        if result:
            otp, subject, sender = result
            write_otp(otp, subject=subject, sender=sender)
            logger.info("[EmailOTPAgent] OTP found: %r  (subject=%r)", otp, subject)
            return AgentResponse(
                status = AgentStatus.SUCCESS,
                output = {"otp": otp, "subject": subject, "sender": sender},
            )

        return AgentResponse(
            status = AgentStatus.FAILED,
            error  = f"No OTP email found within {timeout}s (polled {attempt} times)",
        )

    def _skill_check_once(self, params: Dict[str, Any]) -> AgentResponse:
        """Single non-blocking OWA check (opens browser, checks once, closes)."""
        return self._skill_monitor({**params, "timeout": 1, "poll_interval": 1})

    # ── OWA login ─────────────────────────────────────────────────────────────

    @staticmethod
    def _owa_login(page, username: str, password: str) -> None:
        from playwright.sync_api import TimeoutError as PWTimeout

        logger.info("[EmailOTPAgent] Navigating to OWA …")
        page.goto(_OWA_URL, wait_until="domcontentloaded", timeout=30_000)

        # Step 1 – Email
        page.wait_for_selector("input[type='email']", state="visible", timeout=15_000)
        page.fill("input[type='email']", username)
        page.click("input[type='submit'], button:has-text('Next')")

        # Step 2 – Password
        page.wait_for_selector("input[type='password']", state="visible", timeout=15_000)
        page.fill("input[type='password']", password)
        page.click("input[type='submit'], button:has-text('Sign in')")

        # Step 3 – "Stay signed in?" — always click No to avoid prompts
        try:
            page.wait_for_selector("#idBtn_Back", state="visible", timeout=8_000)
            page.click("#idBtn_Back")   # "No" button
            logger.debug("[EmailOTPAgent] Dismissed 'Stay signed in?' prompt")
        except PWTimeout:
            pass  # prompt didn't appear

        # Wait for inbox to load
        page.wait_for_url(lambda u: "outlook.office.com/mail" in u, timeout=30_000)
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
        logger.info("[EmailOTPAgent] OWA inbox loaded")

    # ── Inbox scan ────────────────────────────────────────────────────────────

    def _scan_inbox(self, page) -> Optional[Tuple[str, str, str]]:
        """
        Scan the visible inbox for an unread Angel One OTP email.
        Returns (otp, subject, sender) or None.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        # Collect all visible email row elements
        # OWA renders each email as a div with role="option" or aria-label containing subject
        email_rows = page.locator("div[role='option']").all()
        logger.debug("[EmailOTPAgent] Found %d email rows in inbox view", len(email_rows))

        for row in email_rows:
            try:
                row_text = row.inner_text()
            except Exception:
                continue

            row_lower = row_text.lower()

            # Quick pre-filter before opening the email
            is_angelone = any(p in row_lower for p in _ANGELONE_SENDER_PATTERNS)
            is_otp_subj = any(k in row_lower for k in _OTP_SUBJECT_KEYWORDS)
            if not is_angelone and not is_otp_subj:
                continue

            logger.info("[EmailOTPAgent] Potential OTP email found, opening: %r", row_text[:80])

            # Click to open the email
            try:
                row.click()
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception as exc:
                logger.debug("[EmailOTPAgent] Could not open email row: %s", exc)
                continue

            # Read the email body from the reading pane
            body_text = ""
            for body_sel in [
                "div[role='main'] div[class*='bodyContainer']",
                "div[data-testid='message-body']",
                "div[role='main'] div[id*='UniqueMessageBody']",
                "div[role='main']",
            ]:
                try:
                    loc = page.locator(body_sel).first
                    loc.wait_for(state="visible", timeout=5_000)
                    body_text = loc.inner_text()
                    if body_text.strip():
                        break
                except PWTimeout:
                    continue

            if not body_text:
                logger.debug("[EmailOTPAgent] Could not read email body")
                continue

            otp_match = _OTP_RE.search(body_text)
            if not otp_match:
                logger.debug("[EmailOTPAgent] No 6-digit OTP found in body: %r", body_text[:100])
                continue

            # Extract sender from the email header area
            sender = ""
            for sender_sel in [
                "span[data-testid='SenderEmailAddress']",
                "div[role='main'] span[class*='sender']",
            ]:
                try:
                    sender = page.locator(sender_sel).first.inner_text()
                    break
                except Exception:
                    pass

            # Extract subject
            subject = row_text.split("\n")[0] if row_text else ""
            otp     = otp_match.group(1)

            logger.info(
                "[EmailOTPAgent] ✓ OTP extracted: %s  |  subject: %r  |  sender: %r",
                otp, subject, sender,
            )
            return otp, subject, sender

        return None
