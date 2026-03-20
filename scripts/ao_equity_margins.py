"""
scripts/ao_equity_margins.py
─────────────────────────────
For each customer in data/Active_Equity_customers.csv, search Angel One NXT
by username, navigate to their profile, capture margin and balance,
and save results to data/equity_margins.csv.

If search returns no results, the customer is skipped.
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
LOGIN_TIMEOUT_S = 300

IN_CSV  = Path("data/Active_Equity_customers.csv")
OUT_CSV = Path("data/equity_master.csv")
DBG_DIR = Path("data")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _screenshot(page, name: str) -> None:
    DBG_DIR.mkdir(exist_ok=True)
    path = str(DBG_DIR / f"debug_{name}.png")
    try:
        page.screenshot(path=path)
        print(f"  [screenshot] {path}")
    except Exception as e:
        print(f"  [screenshot] FAILED {path}: {e}")


def _ensure_logged_in(page) -> bool:
    """Return True if logged in, False if login timed out."""
    if "nxt.angelone.in" in page.url and "/auth" not in page.url:
        print("[ao] Already logged in.")
        return True

    page.goto(URL_AUTH, wait_until="domcontentloaded", timeout=30_000)
    print(f"\n{'─'*55}")
    print("  Please log in to Angel One in the browser window.")
    print(f"  Waiting up to {LOGIN_TIMEOUT_S}s…")
    print(f"{'─'*55}\n")

    try:
        page.wait_for_url(
            lambda url: "nxt.angelone.in" in url and "/auth" not in url,
            timeout=LOGIN_TIMEOUT_S * 1_000,
        )
        print(f"[ao] Login detected: {page.url}")
        return True
    except PWTimeout:
        print("[ao] Login timeout — exiting.")
        return False


def _dismiss_banner(page) -> None:
    """Dismiss notification banner/popup if present."""
    for selector in [
        "button:has-text('LATER')",
        "button:has-text('Later')",
        "button:has-text('DISMISS')",
        "button:has-text('Dismiss')",
    ]:
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=2_000)
            btn.click()
            print("  [banner] Dismissed.")
            time.sleep(1.0)
            return
        except PWTimeout:
            pass


def _focus_top_search(page) -> bool:
    """Click top-left of page to reset focus, then Tab once to land on search."""
    # Reset focus to document body, then single Tab reaches the search field
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); document.body.focus(); }")
    time.sleep(0.2)
    page.keyboard.press("Tab")
    time.sleep(0.3)
    info = page.evaluate("""() => {
        const el = document.activeElement;
        return {tag: el.tagName, placeholder: el.placeholder || ''};
    }""")
    print(f"    [tab] Focused: <{info['tag']}> placeholder='{info['placeholder']}'")
    if info["tag"] == "INPUT":
        return True
    # Try one more tab in case there's a skip-link before the search
    page.keyboard.press("Tab")
    time.sleep(0.3)
    info = page.evaluate("""() => {
        const el = document.activeElement;
        return {tag: el.tagName, placeholder: el.placeholder || ''};
    }""")
    print(f"    [tab] Focused: <{info['tag']}> placeholder='{info['placeholder']}'")
    return info["tag"] == "INPUT"


def _search_customer(page, username: str) -> bool:
    """
    Tab to the top header search bar, type username, select from dropdown,
    then press Enter to navigate to the customer profile.
    Returns True if a result was found, False otherwise.
    """
    if not _focus_top_search(page):
        print(f"    [error] Could not tab to search bar")
        _screenshot(page, "error_no_searchbar")
        return False

    page.keyboard.press("Control+a")
    page.keyboard.type(username, delay=60)
    time.sleep(2.0)

    _screenshot(page, "01_search_dropdown")

    # Press ArrowDown to highlight the first dropdown item
    page.keyboard.press("ArrowDown")
    time.sleep(0.3)

    tag, text = page.evaluate("""() => {
        const el = document.activeElement;
        return [el.tagName, (el.innerText || el.textContent || '').trim()];
    }""")

    # If focus stayed on INPUT, the dropdown has no results — reload for next customer
    if tag == "INPUT" or not text:
        print(f"    [skip] No dropdown results for '{username}' — reloading page")
        page.goto("https://nxt.angelone.in/dashboard", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        _dismiss_banner(page)
        return False

    print(f"    [dropdown] Selected: '{text[:80]}'")

    # Click the highlighted dropdown item
    page.evaluate("() => document.activeElement.click()")
    time.sleep(0.5)

    # Press Enter to trigger the search
    page.keyboard.press("Enter")
    time.sleep(2.0)

    _screenshot(page, "02_after_search")
    print(f"    [nav] Landed on: {page.url}")
    return True


def _extract_customer_data(page) -> dict:
    """
    Extract all financial fields from the customer profile page.
    """
    _screenshot(page, "client_profile")
    time.sleep(1.0)

    return page.evaluate("""() => {
        const FIELDS = [
            'AUM (Equity)',
            'AUM (MF)',
            'AUM (Bonds)',
            'Margin Available for Trade',
            'Accrual',
            'Net Ledger Balance',
            'T-1 Equity Portfolio',
        ];

        function findValue(label) {
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.trim() === label) {
                    const el = walker.currentNode.parentElement;
                    // Try next sibling element
                    const next = el.nextElementSibling;
                    if (next) return next.innerText.trim();
                    // Try parent's next sibling
                    const parentNext = el.parentElement && el.parentElement.nextElementSibling;
                    if (parentNext) return parentNext.innerText.trim();
                }
            }
            return null;
        }

        const result = {};
        for (const f of FIELDS) {
            result[f] = findValue(f);
        }
        return result;
    }""")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    customers = pd.read_csv(IN_CSV)
    print(f"[ao] Loaded {len(customers)} customers from {IN_CSV}")

    # ── Launch Edge only if not already running ───────────────────────────────
    import socket
    def _edge_running():
        try:
            with socket.create_connection(("localhost", CDP_PORT), timeout=1):
                return True
        except OSError:
            return False

    if _edge_running():
        print("[ao] Edge already running — reusing.")
    else:
        print("[ao] Launching Edge…")
        subprocess.Popen([
            EDGE_EXE,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ])
        time.sleep(3)

    results = []

    with sync_playwright() as pw:
        print(f"[ao] Connecting via CDP ({CDP_URL})…")
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # Reuse existing tab, don't open new ones
        pages = context.pages
        page  = pages[0] if pages else context.new_page()

        if not _ensure_logged_in(page):
            return

        # Start from dashboard so the top search bar is the active one
        page.goto("https://nxt.angelone.in/dashboard", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        _dismiss_banner(page)

        today = date.today().isoformat()
        for _, row in customers.iterrows():
            master_id = row["master_customer_id"]
            username  = row["username"]
            print(f"\n[ao] {master_id} — {username}")

            found = _search_customer(page, username)
            if not found:
                results.append({"date": today, "master_customer_id": master_id, "name": username})
                continue

            data = _extract_customer_data(page)
            for k, v in data.items():
                print(f"    {k}: {v}")

            row_data = {"date": today, "master_customer_id": master_id, "name": username}
            row_data.update(data)
            results.append(row_data)

            # Save incrementally so progress isn't lost on crash
            pd.DataFrame(results).to_csv(OUT_CSV, index=False)

            # Return to dashboard for next search
            page.goto("https://nxt.angelone.in/dashboard", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=10_000)
            _dismiss_banner(page)

        print("\n[ao] Done. Browser left open.")

    # ── Save results ──────────────────────────────────────────────────────────
    out_df = pd.DataFrame(results)
    out_df.to_csv(OUT_CSV, index=False)
    print(f"[ao] Saved {len(out_df)} rows → {OUT_CSV}")
    found_count = out_df["Margin Available for Trade"].notna().sum()
    print(f"[ao] Data captured for {found_count}/{len(out_df)} customers.")


if __name__ == "__main__":
    main()
