"""
scripts/ao_fetch.py
────────────────────
Manual login + automated post-login data capture for Angel One NXT.

Flow
────
1. Script launches real Edge (no automation flags, no banner).
2. Angel One login page opens — user logs in normally.
3. Playwright detects when login is complete (URL leaves /auth).
4. Playwright goes to Downloads, finds today's 'Client DP Holdings' card,
   clicks DOWNLOAD, converts xlsx → CSV, uploads to GCS.

Lessons learnt
──────────────
  - Manual login avoids reCAPTCHA entirely.
  - Launching Edge via subprocess (not Playwright) means no automation banner.
  - CDP lets Playwright attach to the live session without controlling the launch.
"""

import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Config ─────────────────────────────────────────────────────────────────────

EDGE_EXE    = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE_DIR = str(Path.home() / ".angelone" / "edge_profile")
CDP_PORT    = 9222
CDP_URL     = f"http://localhost:{CDP_PORT}"

URL_AUTH        = "https://nxt.angelone.in/auth/authorised-partner"
URL_DOWNLOADS   = "https://nxt.angelone.in/downloads"
LOGIN_TIMEOUT_S = 300   # seconds to wait for the user to log in

OUT_DIR     = Path("data")
REPORTS = [
    {"report_name": "Client DP Holdings", "csv_name": "client-dp-holdings.csv"},
    {"report_name": "Security Holdings",  "csv_name": "equity.csv"},
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _screenshot(page, name: str) -> None:
    path = str(OUT_DIR / f"debug_{name}.png")
    try:
        page.screenshot(path=path)
        print(f"  [screenshot] {path}")
    except Exception:
        pass


def _dismiss_popup(page) -> None:
    """Dismiss the notification popup if it appears — retry a few times."""
    for _ in range(3):
        try:
            btn = page.locator("button:has-text('LATER'), button:has-text('Later')").first
            btn.wait_for(state="visible", timeout=2_000)
            btn.click()
            print("  [popup] Dismissed.")
            time.sleep(0.5)
            return
        except PWTimeout:
            time.sleep(1)


def _download_report(page, out_dir: Path, report_name: str, csv_name: str) -> Path:
    """
    Navigate to Downloads, tab to the DOWNLOAD element for report_name,
    click it, convert xlsx → csv_name, upload to GCS.
    """
    from agents.gcs_storage_agent import GCSStorageAgent
    from agents.base import AgentStatus

    today_str = date.today().strftime("%b %d %Y")   # "Mar 19 2026"
    print(f"\n[ao_fetch] Downloading '{report_name}'…")
    page.goto(URL_DOWNLOADS, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_load_state("networkidle", timeout=10_000)

    _dismiss_popup(page)

    MAX_TABS = 60
    found = False
    page.keyboard.press("Tab")

    for i in range(MAX_TABS):
        time.sleep(0.3)
        label = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el) return null;
            const txt = (el.innerText || el.textContent || '').trim();
            let card = el.parentElement;
            let cardText = '';
            for (let j = 0; j < 15; j++) {
                if (!card) break;
                cardText = card.innerText || card.textContent || '';
                if (cardText.length > 30) break;
                card = card.parentElement;
            }
            return {focused: txt, card: cardText};
        }""")

        if label and label["focused"] == "DOWNLOAD":
            card_text = label["card"]
            if report_name in card_text and today_str in card_text:
                print(f"  [found] Tab #{i+1}: DOWNLOAD for '{report_name}' focused.")
                found = True
                break

        page.keyboard.press("Tab")

    if not found:
        raise RuntimeError(
            f"Could not tab to DOWNLOAD for '{report_name}' ({today_str}) "
            f"within {MAX_TABS} tabs."
        )

    print("  [download] Clicking…")
    with page.expect_download(timeout=60_000) as dl_info:
        page.evaluate("() => document.activeElement.click()")

    download  = dl_info.value
    xlsx_path = out_dir / download.suggested_filename
    download.save_as(xlsx_path)
    print(f"  [downloaded] {xlsx_path}")

    csv_path = out_dir / csv_name
    df = pd.read_excel(xlsx_path, engine="openpyxl")
    df.to_csv(csv_path, index=False)
    print(f"  [converted] {len(df)} rows, {len(df.columns)} columns → {csv_name}")

    gcs_uri = f"gs://winrich_shared/data/AngleNxt/{csv_name}"
    print(f"  [gcs] Uploading to {gcs_uri} …")
    gcs_resp = GCSStorageAgent().run("upload_csv", {
        "file_path":   str(csv_path),
        "filename":    csv_name,
        "bucket_name": "winrich_shared",
        "prefix":      "data/AngleNxt",
    })

    if gcs_resp.status == AgentStatus.SUCCESS:
        print(f"  [gcs] Uploaded → {gcs_resp.output['gcs_uri']}")
    else:
        print(f"  [gcs] Upload failed: {gcs_resp.error}")

    return csv_path


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:

    # ── Step 1: Launch real Edge with CDP port ────────────────────────────────
    print("[ao_fetch] Launching Edge…")
    edge_proc = subprocess.Popen([
        EDGE_EXE,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
    ])

    print("[ao_fetch] Waiting for Edge to start…")
    time.sleep(3)

    with sync_playwright() as pw:

        # ── Step 2: Connect to running Edge via CDP ───────────────────────────
        print(f"[ao_fetch] Connecting to Edge via CDP ({CDP_URL})…")
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # ── Step 3: Reuse existing tab or open login page ────────────────────
        pages = context.pages
        page  = pages[0] if pages else context.new_page()

        # If already logged in, skip the login wait entirely
        if "nxt.angelone.in" in page.url and "/auth" not in page.url:
            print(f"[ao_fetch] Already logged in: {page.url}")
        else:
            page.goto(URL_AUTH, wait_until="domcontentloaded", timeout=30_000)
            print(f"[ao_fetch] Opened: {page.url}")

            # ── Step 4: Wait for user to log in ──────────────────────────────
            print(f"\n{'─'*55}")
            print("  Please log in to Angel One in the browser window.")
            print(f"  Waiting up to {LOGIN_TIMEOUT_S}s…")
            print(f"{'─'*55}\n")

            try:
                page.wait_for_url(
                    lambda url: "nxt.angelone.in" in url and "/auth" not in url,
                    timeout = LOGIN_TIMEOUT_S * 1_000,
                )
            except PWTimeout:
                print("[ao_fetch] Login timeout — exiting.")
                browser.close()
                edge_proc.terminate()
                return

            print(f"[ao_fetch] Login detected! URL: {page.url}")

        # ── Step 5: Download all reports ─────────────────────────────────────
        OUT_DIR.mkdir(exist_ok=True)
        for r in REPORTS:
            _download_report(page, OUT_DIR, r["report_name"], r["csv_name"])

        browser.close()
        edge_proc.terminate()
        print("\n[ao_fetch] All done. Edge closed.")


if __name__ == "__main__":
    main()
