"""
agents/angelone_cdp_agent.py
─────────────────────────────
Connects to a Chrome instance that was launched with --remote-debugging-port=9222,
waits for the user to log in to Angel One, then navigates to data pages and
intercepts every JSON API response the page makes.

Usage
─────
1. Launch Chrome via scripts/launch_chrome_debug.bat
2. Log in to Angel One in that window
3. Call this agent from Streamlit or directly:

    agent = AngelOneCDPAgent()
    resp  = agent.run("fetch_data", {
        "pages": ["portfolio", "mutualfunds"],   # which sections to visit
    })
    holdings = resp.output["captured"]           # list of {url, data} dicts

How it works
────────────
- Playwright connect_over_cdp("http://localhost:9222") attaches to the running
  Chrome — no new browser window, no new profile, no reCAPTCHA.
- A response listener intercepts every JSON XHR the page fires as we navigate.
- Results are returned as a list so the caller can filter what they need.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests as _requests

from agents.base import Agent, AgentResponse, AgentStatus

logger = logging.getLogger(__name__)

_CDP_URL      = "http://localhost:9222"
_ANGELONE_AUTH = "https://nxt.angelone.in/auth"

# Pages to visit when fetching data — (label, url)
_DATA_PAGES: Dict[str, str] = {
    "portfolio":    "https://nxt.angelone.in/portfolio",
    "mutualfunds":  "https://nxt.angelone.in/mutual-funds",
    "holdings":     "https://nxt.angelone.in/portfolio/holdings",
    "positions":    "https://nxt.angelone.in/portfolio/positions",
    "dashboard":    "https://nxt.angelone.in/dashboard",
}


def _chrome_running(cdp_url: str = _CDP_URL) -> bool:
    """Return True if Chrome is listening on the CDP port."""
    try:
        r = _requests.get(f"{cdp_url}/json/version", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


class AngelOneCDPAgent(Agent):
    """Connects to real Chrome via CDP and intercepts Angel One data."""

    name = "AngelOneCDPAgent"

    def __init__(self) -> None:
        self.skills: Dict[str, Callable] = {
            "check_chrome":  self._skill_check_chrome,
            "wait_for_login": self._skill_wait_for_login,
            "fetch_data":    self._skill_fetch_data,
        }

    # ── skills ────────────────────────────────────────────────────────────────

    def _skill_check_chrome(self, params: Dict[str, Any]) -> AgentResponse:
        """Returns whether Chrome is running with CDP on port 9222."""
        running = _chrome_running()
        return AgentResponse(
            status = AgentStatus.SUCCESS,
            output = {"running": running},
        )

    def _skill_wait_for_login(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Connects to Chrome and waits until the user has logged in
        (URL leaves /auth). Returns the post-login URL.

        Params
        ──────
        timeout   int   Seconds to wait  [300]
        """
        timeout = int(params.get("timeout", 300))

        if not _chrome_running():
            return AgentResponse(
                status = AgentStatus.FAILED,
                error  = "Chrome is not running with --remote-debugging-port=9222. "
                         "Run scripts/launch_chrome_debug.bat first.",
            )

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            return AgentResponse(
                status = AgentStatus.FAILED,
                error  = "playwright not installed — run: pip install playwright",
            )

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(_CDP_URL)
            except Exception as exc:
                return AgentResponse(
                    status = AgentStatus.FAILED,
                    error  = f"Could not connect to Chrome CDP: {exc}",
                )

            # Find or open the Angel One tab
            context = browser.contexts[0] if browser.contexts else None
            if not context:
                return AgentResponse(
                    status = AgentStatus.FAILED,
                    error  = "No browser context found in Chrome.",
                )

            page = self._find_or_open_page(context, "angelone.in", _ANGELONE_AUTH)

            logger.info("[CDPAgent] Waiting up to %ds for login …", timeout)
            try:
                page.bring_to_front()
                page.wait_for_url(
                    lambda url: "/auth" not in url,
                    timeout = timeout * 1_000,
                )
            except PWTimeout:
                browser.disconnect()
                return AgentResponse(
                    status = AgentStatus.FAILED,
                    error  = f"Login not completed within {timeout}s.",
                )

            post_login_url = page.url
            logger.info("[CDPAgent] Login detected. URL: %s", post_login_url)
            browser.disconnect()

        return AgentResponse(
            status = AgentStatus.SUCCESS,
            output = {"url": post_login_url},
        )

    def _skill_fetch_data(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Navigates to the requested Angel One pages and intercepts all JSON
        API responses those pages make.

        Params
        ──────
        pages    list[str]   Keys from _DATA_PAGES to visit   ["portfolio", "mutualfunds"]
        wait_ms  int         networkidle wait per page (ms)    [5000]
        """
        pages_to_visit = params.get("pages", ["portfolio", "mutualfunds"])
        wait_ms        = int(params.get("wait_ms", 5_000))

        if not _chrome_running():
            return AgentResponse(
                status = AgentStatus.FAILED,
                error  = "Chrome is not running with --remote-debugging-port=9222.",
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AgentResponse(
                status = AgentStatus.FAILED,
                error  = "playwright not installed",
            )

        captured: List[Dict] = []

        def _on_response(response) -> None:
            """Intercept every JSON response the page receives."""
            try:
                ct = response.headers.get("content-type", "")
                if "json" not in ct:
                    return
                if "angelone.in" not in response.url and "angelbroking.com" not in response.url:
                    return
                if response.status not in (200, 201):
                    return
                body = response.json()
                captured.append({
                    "url":    response.url,
                    "status": response.status,
                    "data":   body,
                })
                logger.info("[CDPAgent] Captured %s (%d bytes)",
                            response.url, len(str(body)))
            except Exception:
                pass

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(_CDP_URL)
            except Exception as exc:
                return AgentResponse(
                    status = AgentStatus.FAILED,
                    error  = f"Could not connect to Chrome CDP: {exc}",
                )

            context = browser.contexts[0] if browser.contexts else None
            if not context:
                browser.disconnect()
                return AgentResponse(status=AgentStatus.FAILED, error="No browser context.")

            page = self._find_or_open_page(context, "angelone.in", _ANGELONE_AUTH)

            # Check still logged in
            if "/auth" in page.url:
                browser.disconnect()
                return AgentResponse(
                    status = AgentStatus.FAILED,
                    error  = "Not logged in — URL is still /auth.",
                )

            # Attach response listener
            page.on("response", _on_response)

            for key in pages_to_visit:
                url = _DATA_PAGES.get(key)
                if not url:
                    logger.warning("[CDPAgent] Unknown page key %r — skipping", key)
                    continue

                logger.info("[CDPAgent] Navigating to %s …", url)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    page.wait_for_load_state("networkidle", timeout=wait_ms)
                    time.sleep(1)   # let any deferred XHRs fire
                except Exception as exc:
                    logger.warning("[CDPAgent] Navigation to %s failed: %s", url, exc)

            browser.disconnect()

        logger.info("[CDPAgent] Captured %d JSON responses total.", len(captured))

        # Save raw dump next to data/ for debugging
        out = Path("data") / "ao_cdp_captured.json"
        try:
            out.write_text(json.dumps(captured, indent=2, default=str), encoding="utf-8")
            logger.info("[CDPAgent] Raw dump saved to %s", out)
        except Exception:
            pass

        return AgentResponse(
            status = AgentStatus.SUCCESS,
            output = {
                "captured": captured,
                "count":    len(captured),
            },
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _find_or_open_page(context, domain: str, fallback_url: str):
        """Return the first page matching domain, or open fallback_url."""
        for p in context.pages:
            if domain in p.url:
                return p
        # No matching tab — open a new one
        page = context.new_page()
        page.goto(fallback_url, wait_until="domcontentloaded", timeout=30_000)
        return page
