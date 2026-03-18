"""
scripts/ao_fetch.py
────────────────────
Runs as a subprocess launched by pages/angelone_login.py.

Opens Edge (or Chromium), waits for the user to log in to Angel One,
then navigates to portfolio/MF pages and intercepts all JSON API
responses those pages make.

Writes two files:
  data/ao_fetch_status.json   — live status (polled by Streamlit)
  data/ao_cdp_captured.json  — final captured API responses
"""

import json
import sys
import time
from pathlib import Path

# Allow importing project modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_STATUS_FILE = Path("data/ao_fetch_status.json")
_DATA_FILE   = Path("data/ao_cdp_captured.json")

Path("data").mkdir(exist_ok=True)

_DATA_PAGES = [
    "https://nxt.angelone.in/portfolio",
    "https://nxt.angelone.in/mutual-funds",
    "https://nxt.angelone.in/portfolio/holdings",
]


def _write_status(status: str, message: str = "", count: int = 0) -> None:
    _STATUS_FILE.write_text(
        json.dumps({"status": status, "message": message, "count": count}),
        encoding="utf-8",
    )


def main() -> None:
    _write_status("starting", "Starting browser…")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        _write_status("error", "playwright not installed — run: pip install playwright")
        return

    captured: list[dict] = []

    def _on_response(response) -> None:
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if "angelone.in" not in response.url and "angelbroking.com" not in response.url:
                return
            if response.status not in (200, 201):
                return
            body = response.json()
            captured.append({"url": response.url, "status": response.status, "data": body})
        except Exception:
            pass

    profile_dir = str(Path.home() / ".angelone" / "browser_profile_fetch")

    with sync_playwright() as pw:
        # Try real Edge first, fall back to bundled Chromium
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir = profile_dir,
                channel       = "msedge",
                headless      = False,
                args          = ["--disable-blink-features=AutomationControlled"],
            )
            print("[ao_fetch] Launched Edge", flush=True)
        except Exception as err:
            print(f"[ao_fetch] Edge not found ({err}), falling back to Chromium", flush=True)
            context = pw.chromium.launch_persistent_context(
                user_data_dir = profile_dir,
                headless      = False,
                args          = ["--disable-blink-features=AutomationControlled"],
            )

        page = context.new_page()

        # Navigate to Angel One — may already be logged in (persistent profile)
        _write_status("navigating", "Opening Angel One…")
        try:
            page.goto("https://nxt.angelone.in/auth", wait_until="domcontentloaded", timeout=30_000)
        except Exception as err:
            _write_status("error", f"Could not open Angel One: {err}")
            context.close()
            return

        # If still on /auth, wait for the user to log in
        if "/auth" in page.url:
            _write_status("waiting_login", "Please log in to Angel One in the browser window…")
            try:
                page.wait_for_url(
                    lambda url: "/auth" not in url,
                    timeout = 300_000,   # 5-minute window
                )
            except PWTimeout:
                _write_status("error", "Login timeout — no login detected within 5 minutes")
                context.close()
                return

        _write_status("fetching", "Logged in! Fetching portfolio data…")
        print("[ao_fetch] Login detected, starting data capture", flush=True)

        # Attach response interceptor BEFORE navigating to data pages
        page.on("response", _on_response)

        for url in _DATA_PAGES:
            try:
                print(f"[ao_fetch] → {url}", flush=True)
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                page.wait_for_load_state("networkidle", timeout=8_000)
                time.sleep(1.5)   # let deferred XHRs fire
            except Exception as exc:
                print(f"[ao_fetch] Warning: {url} — {exc}", file=sys.stderr, flush=True)

        context.close()

    print(f"[ao_fetch] Captured {len(captured)} JSON responses", flush=True)

    _DATA_FILE.write_text(
        json.dumps(captured, indent=2, default=str),
        encoding="utf-8",
    )
    _write_status("done", f"Done — {len(captured)} responses captured", len(captured))


if __name__ == "__main__":
    main()
