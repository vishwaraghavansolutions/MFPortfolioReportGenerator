"""
scripts/ao_daily_runner.py
──────────────────────────
One-click daily runner for Angel One NXT data fetch.

Launch via run_daily.bat (double-click on desktop).

Flow
────
  1. GUI window opens showing status.
  2. Edge browser launches → user logs in manually.
  3. Edge minimizes automatically after login.
  4. Downloads 'Client DP Holdings' and 'Security Holdings' → GCS.
  5. Extracts margin/balance for all customers → equity_margins.csv → GCS.
  6. Shows "All done!" when complete. Edge stays open.

If Edge is closed mid-run, the script detects the disconnect and
prompts the user to log in again — no need to restart.
"""

import ctypes
import ctypes.wintypes
import subprocess
import sys
import threading
import time
import traceback
from datetime import date
from pathlib import Path

import tkinter as tk
from tkinter import scrolledtext, font as tkfont

# ── resolve project root so imports work from anywhere ────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Config ────────────────────────────────────────────────────────────────────
def _find_edge() -> str:
    candidates = [
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    # Fall back to registry
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
        return winreg.QueryValue(key, None)
    except Exception:
        pass
    raise RuntimeError(
        "Microsoft Edge not found. Please install Edge and try again.\n"
        "Download: https://www.microsoft.com/edge"
    )

EDGE_EXE = _find_edge()
PROFILE_DIR = str(Path.home() / ".angelone" / "edge_profile")
CDP_PORT    = 9222
CDP_URL     = f"http://localhost:{CDP_PORT}"

URL_AUTH      = "https://nxt.angelone.in/auth/authorised-partner"
URL_DASHBOARD = "https://nxt.angelone.in/dashboard"
URL_DOWNLOADS = "https://nxt.angelone.in/downloads"

REPORTS = [
    {"report_name": "Client DP Holdings", "csv_name": "client-dp-holdings.csv"},
    {"report_name": "Security Holdings",  "csv_name": "equity.csv"},
]

IN_CSV  = ROOT / "data" / "Active_Equity_customers.csv"
OUT_CSV = ROOT / "data" / "equity_margins.csv"
OUT_DIR = ROOT / "data"

# ── Email config ───────────────────────────────────────────────────────────────
EMAIL_FROM    = "winrichgroup@gmail.com"
EMAIL_TO      = "winrichgroup@gmail.com"
EMAIL_APP_PWD = "wbog cehl soec icaa"

# Set to None to run all customers; set to 10 for testing
TEST_LIMIT = None   # set to an int (e.g. 10) for testing; None = all customers


# ══════════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════════

class RunnerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Angel One Daily Runner")
        root.geometry("640x460")
        root.resizable(False, False)
        root.configure(bg="#1e1e2e")

        self._paused    = False
        self._pause_evt = threading.Event()
        self._pause_evt.set()   # not paused initially

        # ── Status banner ──────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Starting…")
        tk.Label(
            root, textvariable=self.status_var,
            font=tkfont.Font(family="Segoe UI", size=13, weight="bold"),
            fg="#cdd6f4", bg="#1e1e2e", pady=10,
        ).pack(fill="x")

        # ── Progress bar (marquee) ─────────────────────────────────────────
        self.progress_canvas = tk.Canvas(root, height=6, bg="#313244", highlightthickness=0)
        self.progress_canvas.pack(fill="x", padx=20)
        self._bar         = self.progress_canvas.create_rectangle(0, 0, 0, 6, fill="#89b4fa", outline="")
        self._bar_pos     = 0
        self._bar_running = False

        # ── Buttons row ────────────────────────────────────────────────────
        btn_frame = tk.Frame(root, bg="#1e1e2e")
        btn_frame.pack(pady=(4, 0))

        self.pause_btn = tk.Button(
            btn_frame, text="⏸  Pause",
            font=tkfont.Font(family="Segoe UI", size=10),
            fg="#1e1e2e", bg="#89b4fa", activebackground="#74c7ec",
            relief="flat", padx=12, pady=4,
            command=self._toggle_pause,
        )
        self.pause_btn.pack(side="left", padx=(0, 8))

        tk.Button(
            btn_frame, text="✕  Exit",
            font=tkfont.Font(family="Segoe UI", size=10),
            fg="#1e1e2e", bg="#f38ba8", activebackground="#eba0ac",
            relief="flat", padx=12, pady=4,
            command=self._on_close,
        ).pack(side="left")

        # ── Log area ───────────────────────────────────────────────────────
        self.log = scrolledtext.ScrolledText(
            root, height=16, bg="#181825", fg="#cdd6f4",
            font=tkfont.Font(family="Consolas", size=9),
            relief="flat", state="disabled",
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        self.log.tag_config("ok",   foreground="#a6e3a1")
        self.log.tag_config("warn", foreground="#f9e2af")
        self.log.tag_config("err",  foreground="#f38ba8")
        self.log.tag_config("info", foreground="#89dceb")

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Pause / resume ─────────────────────────────────────────────────────

    def _toggle_pause(self):
        self._paused = not self._paused
        if self._paused:
            self._pause_evt.clear()
            self.pause_btn.config(text="▶  Resume", bg="#f9e2af")
            self.set_status("Paused — click Resume to continue…")
            self.log_line("⏸ Paused.", "warn")
        else:
            self._pause_evt.set()
            self.pause_btn.config(text="⏸  Pause", bg="#89b4fa")
            self.log_line("▶ Resumed.", "ok")

    def wait_if_paused(self):
        """Automation thread calls this between customers to honour pause."""
        self._pause_evt.wait()

    # ── Helpers ────────────────────────────────────────────────────────────

    def set_status(self, text: str):
        self.root.after(0, lambda: self.status_var.set(text))

    def log_line(self, text: str, tag: str = ""):
        def _do():
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n", tag)
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, _do)

    def start_progress(self):
        self._bar_running = True
        self._animate()

    def stop_progress(self):
        self._bar_running = False
        self.progress_canvas.coords(self._bar, 0, 0, 0, 6)

    def _animate(self):
        if not self._bar_running:
            return
        w = self.progress_canvas.winfo_width()
        self._bar_pos = (self._bar_pos + 4) % (w + 80)
        x0 = self._bar_pos - 80
        x1 = self._bar_pos
        self.progress_canvas.coords(self._bar, x0, 0, x1, 6)
        self.root.after(20, self._animate)

    def _on_close(self):
        # Kill Edge before exiting
        try:
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
        except Exception:
            pass
        self.root.destroy()
        sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _clean_amount(val) -> float | None:
    """Strip ₹, commas, spaces and return a float. Returns None if unparseable."""
    if val is None:
        return None
    cleaned = str(val).replace("₹", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _edge_running() -> bool:
    import socket
    try:
        with socket.create_connection(("localhost", CDP_PORT), timeout=1):
            return True
    except OSError:
        return False


def _minimize_edge():
    user32    = ctypes.windll.user32
    SW_MINIMIZE = 6

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf    = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title  = buf.value
        if user32.IsWindowVisible(hwnd) and ("Edge" in title or "Angel" in title):
            user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True

    user32.EnumWindows(_cb, 0)


def _dismiss_popup(page, gui: RunnerGUI) -> None:
    from playwright.sync_api import TimeoutError as PWTimeout
    for _ in range(3):
        try:
            btn = page.locator("button:has-text('LATER'), button:has-text('Later')").first
            btn.wait_for(state="visible", timeout=2_000)
            btn.click()
            gui.log_line("  [popup] Dismissed.", "info")
            time.sleep(0.5)
            return
        except PWTimeout:
            time.sleep(1)


def _download_report(page, report_name: str, csv_name: str, gui: RunnerGUI) -> None:
    from playwright.sync_api import TimeoutError as PWTimeout
    from agents.gcs_storage_agent import GCSStorageAgent
    from agents.base import AgentStatus
    import pandas as pd

    today_str = date.today().strftime("%b %d %Y")
    gui.log_line(f"\n► Downloading '{report_name}'…", "info")
    page.goto(URL_DOWNLOADS, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_load_state("networkidle", timeout=10_000)
    _dismiss_popup(page, gui)

    MAX_TABS = 60
    found    = False
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
            if report_name in label["card"] and today_str in label["card"]:
                gui.log_line(f"  [found] Tab #{i+1}: DOWNLOAD for '{report_name}'", "ok")
                found = True
                break

        page.keyboard.press("Tab")

    if not found:
        raise RuntimeError(f"Could not find DOWNLOAD for '{report_name}' ({today_str})")

    with page.expect_download(timeout=60_000) as dl_info:
        page.evaluate("() => document.activeElement.click()")

    download  = dl_info.value
    xlsx_path = OUT_DIR / download.suggested_filename
    download.save_as(xlsx_path)
    gui.log_line(f"  [downloaded] {download.suggested_filename}", "ok")

    csv_path = OUT_DIR / csv_name
    # Angel One reports have a title row before the actual column headers.
    # Detect the real header row by finding the first row where >50% of cells
    # are non-empty strings (i.e. actual column names, not a sparse title row).
    raw = pd.read_excel(xlsx_path, engine="openpyxl", header=None)
    header_row = 0
    for i, row in raw.iterrows():
        non_empty = row.dropna().astype(str).str.strip().str.len().gt(0).sum()
        if non_empty > len(row) * 0.5:
            header_row = i
            break
    df = pd.read_excel(xlsx_path, engine="openpyxl", skiprows=header_row)
    df.to_csv(csv_path, index=False)
    gui.log_line(f"  [converted] {len(df)} rows → {csv_name} (header at row {header_row})", "ok")

    gcs_resp = GCSStorageAgent().run("upload_csv", {
        "file_path":   str(csv_path),
        "filename":    csv_name,
        "bucket_name": "winrich_shared",
        "prefix":      "data/AngleNxt",
    })
    if gcs_resp.status == AgentStatus.SUCCESS:
        gui.log_line(f"  [gcs] ✓ {gcs_resp.output['gcs_uri']}", "ok")
    else:
        gui.log_line(f"  [gcs] Upload failed: {gcs_resp.error}", "err")


def _focus_top_search(page) -> bool:
    page.evaluate("() => { document.body.focus(); }")
    time.sleep(0.2)
    page.keyboard.press("Tab")
    time.sleep(0.3)
    info = page.evaluate("""() => {
        const el = document.activeElement;
        return {tag: el.tagName, placeholder: el.placeholder || ''};
    }""")
    if info["tag"] == "INPUT":
        return True
    page.keyboard.press("Tab")
    time.sleep(0.3)
    info = page.evaluate("""() => {
        const el = document.activeElement;
        return {tag: el.tagName, placeholder: el.placeholder || ''};
    }""")
    return info["tag"] == "INPUT"


def _search_customer(page, username: str) -> bool:
    if not _focus_top_search(page):
        return False

    page.keyboard.press("Control+a")
    page.keyboard.type(username, delay=60)
    time.sleep(2.0)

    page.keyboard.press("ArrowDown")
    time.sleep(0.3)

    tag, text = page.evaluate("""() => {
        const el = document.activeElement;
        return [el.tagName, (el.innerText || el.textContent || '').trim()];
    }""")

    if tag == "INPUT" or not text:
        page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        return False

    page.evaluate("() => document.activeElement.click()")
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(2.0)
    return True


def _extract_customer_data(page) -> dict:
    time.sleep(1.0)
    raw = page.evaluate("""() => {
        const FIELDS = [
            'AUM (Equity)', 'AUM (MF)', 'AUM (Bonds)',
            'Margin Available for Trade', 'Accrual',
            'Net Ledger Balance', 'T-1 Equity Portfolio',
        ];
        function findValue(label) {
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.trim() === label) {
                    const el = walker.currentNode.parentElement;
                    const next = el.nextElementSibling;
                    if (next) return next.innerText.trim();
                    const parentNext = el.parentElement && el.parentElement.nextElementSibling;
                    if (parentNext) return parentNext.innerText.trim();
                }
            }
            return null;
        }
        const result = {};
        for (const f of FIELDS) result[f] = findValue(f);
        return result;
    }""")
    # Convert all monetary values to plain floats
    return {k: _clean_amount(v) for k, v in raw.items()}


# ══════════════════════════════════════════════════════════════════════════════
# Main automation loop (background thread)
# ══════════════════════════════════════════════════════════════════════════════

def _send_report_email(df, total: int, captured: int, errors: list) -> str:
    """Send scraped data as an HTML table to EMAIL_TO. Returns status string."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    today_str = date.today().strftime("%d %b %Y")

    # ── Build HTML table ───────────────────────────────────────────────────
    # Show only the key columns in the email
    display_cols = [c for c in [
        "master_customer_id", "name", "date",
        "AUM (Equity)", "AUM (MF)", "AUM (Bonds)",
        "Margin Available for Trade", "Net Ledger Balance",
        "Accrual", "T-1 Equity Portfolio",
    ] if c in df.columns]
    table_df = df[display_cols].copy()

    # Format numeric columns as ₹ values
    for col in table_df.columns:
        if col not in ("master_customer_id", "name", "date"):
            table_df[col] = table_df[col].apply(
                lambda v: f"₹{v:,.0f}" if v is not None and str(v) not in ("", "nan") and str(v) != "None"
                else "—"
            )

    th_style = (
        "background:#1a2a5e;color:#fff;padding:6px 10px;"
        "text-align:left;font-size:12px;white-space:nowrap;"
    )
    td_style = "padding:5px 10px;font-size:12px;border-bottom:1px solid #ddd;"
    tr_alt   = "background:#f4f6fb;"

    header_html = "".join(f"<th style='{th_style}'>{c}</th>" for c in table_df.columns)
    rows_html   = ""
    for i, (_, row) in enumerate(table_df.iterrows()):
        tr_style = f"style='{tr_alt}'" if i % 2 == 1 else ""
        cells = "".join(f"<td style='{td_style}'>{v}</td>" for v in row)
        rows_html += f"<tr {tr_style}>{cells}</tr>"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;">
    <h2 style="color:#1a2a5e;">ANGEL NXT — Daily Scrape Report</h2>
    <p style="color:#555;">Date: <b>{today_str}</b> &nbsp;|&nbsp;
       Customers scraped: <b>{captured}/{total}</b>
       {"&nbsp;|&nbsp;<span style='color:red'>Errors: " + str(len(errors)) + "</span>" if errors else ""}
    </p>
    <table border="0" cellspacing="0" cellpadding="0"
           style="border-collapse:collapse;width:100%;min-width:600px;">
      <thead><tr>{header_html}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    {"<p style='color:red;margin-top:12px;'>Failed customers: " + ", ".join(errors) + "</p>" if errors else ""}
    <p style="color:#aaa;font-size:11px;margin-top:16px;">
      Sent automatically by ANGEL NXT Daily Runner.
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"ANGEL NXT Daily Report — {today_str}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_FROM, EMAIL_APP_PWD)
        smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

    return f"Email sent to {EMAIL_TO}"


def _setup_log_file():
    """Open a dated log file in data/ and return it."""
    OUT_DIR.mkdir(exist_ok=True)
    log_path = OUT_DIR / f"run_{date.today().isoformat()}.log"
    return open(log_path, "a", encoding="utf-8")


def _log(gui: RunnerGUI, fh, text: str, tag: str = "") -> None:
    """Write to GUI and log file simultaneously."""
    gui.log_line(text, tag)
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {text}", file=fh, flush=True)


def _run(gui: RunnerGUI) -> None:
    import pandas as pd
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    OUT_DIR.mkdir(exist_ok=True)
    log_fh = _setup_log_file()

    customers = pd.read_csv(IN_CSV)
    if TEST_LIMIT:
        customers = customers.head(TEST_LIMIT)
    total = len(customers)
    _log(gui, log_fh, f"Loaded {total} customers from {IN_CSV.name}", "info")
    if TEST_LIMIT:
        _log(gui, log_fh, f"(TEST MODE — first {TEST_LIMIT} only)", "warn")

    while True:   # retry loop if Edge is closed mid-run

        # Kill any leftover Edge process on the CDP port before launching fresh
        subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"],
                       capture_output=True)
        time.sleep(2)

        gui.set_status("Launching browser…")
        _log(gui, log_fh, f"\nLaunching Edge from: {EDGE_EXE}", "info")
        edge_proc = subprocess.Popen([
            EDGE_EXE,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ])
        _log(gui, log_fh, f"Edge PID: {edge_proc.pid}", "info")

        # Wait for Edge CDP to be ready — slower machines need more time
        ready = False
        for _ in range(20):
            time.sleep(1)
            if _edge_running():
                ready = True
                break
        if not ready:
            raise RuntimeError(
                "Edge launched but CDP port not ready after 20s. "
                "Check Edge is installed correctly."
            )

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(CDP_URL)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                pages   = context.pages
                page    = pages[0] if pages else context.new_page()

                # ── Login ─────────────────────────────────────────────────
                if "nxt.angelone.in" in page.url and "/auth" not in page.url:
                    _log(gui, log_fh, "Already logged in.", "ok")
                else:
                    # Swallow navigation errors (ERR_ABORTED is common on fresh
                    # Edge launches due to redirects) — we rely on wait_for_url
                    try:
                        page.goto(URL_AUTH, wait_until="commit", timeout=30_000)
                    except Exception:
                        pass
                    gui.set_status("Please log in to Angel One in the browser…")
                    _log(gui, log_fh, "Waiting for login…", "warn")

                    page.wait_for_url(
                        lambda url: "nxt.angelone.in" in url and "/auth" not in url,
                        timeout=600_000,
                    )
                    _log(gui, log_fh, "Login detected!", "ok")

                # ── Minimize Edge ─────────────────────────────────────────
                time.sleep(1)
                _minimize_edge()
                _log(gui, log_fh, "Browser minimized.", "info")
                gui.start_progress()

                # ── Step 1: Download reports ───────────────────────────────
                gui.set_status("Downloading reports…")
                for r in REPORTS:
                    _download_report(page, r["report_name"], r["csv_name"], gui)

                # ── Step 2: Extract customer balances ─────────────────────
                _log(gui, log_fh, "\n► Extracting customer balances…", "info")
                page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_load_state("networkidle", timeout=10_000)

                results  = []
                errors   = []
                today    = date.today().isoformat()

                for idx, (_, row) in enumerate(customers.iterrows(), 1):
                    gui.wait_if_paused()

                    master_id = row["master_customer_id"]
                    username  = row["username"]
                    gui.set_status(f"Extracting balances ({idx}/{total})…  {username}")

                    try:
                        found = _search_customer(page, username)
                        if not found:
                            _log(gui, log_fh,
                                 f"  [{idx}/{total}] {username} — not found, skipped", "warn")
                            results.append({
                                "date": today,
                                "master_customer_id": master_id,
                                "name": username,
                            })
                        else:
                            data     = _extract_customer_data(page)
                            row_data = {
                                "date": today,
                                "master_customer_id": master_id,
                                "name": username,
                            }
                            row_data.update(data)
                            results.append(row_data)

                            margin  = data.get("Margin Available for Trade")
                            balance = data.get("Net Ledger Balance")
                            _log(gui, log_fh,
                                 f"  [{idx}/{total}] {username}  "
                                 f"margin={margin}  balance={balance}", "ok")

                            pd.DataFrame(results).to_csv(OUT_CSV, index=False)

                        page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=30_000)
                        page.wait_for_load_state("networkidle", timeout=10_000)

                    except Exception as cust_exc:
                        errors.append(username)
                        _log(gui, log_fh,
                             f"  [{idx}/{total}] {username} — ERROR: {cust_exc}", "err")
                        print(traceback.format_exc(), file=log_fh, flush=True)
                        # Recover: navigate back to dashboard and continue
                        try:
                            page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=30_000)
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:
                            pass

                # ── Final save + GCS upload ────────────────────────────────
                out_df = pd.DataFrame(results)
                out_df.to_csv(OUT_CSV, index=False)

                from agents.gcs_storage_agent import GCSStorageAgent
                from agents.base import AgentStatus

                gcs_resp = GCSStorageAgent().run("upload_csv", {
                    "file_path":   str(OUT_CSV),
                    "filename":    "angelone_equity_margins.csv",
                    "bucket_name": "winrich_shared",
                    "prefix":      "data/AngleNxt",
                })
                if gcs_resp.status == AgentStatus.SUCCESS:
                    _log(gui, log_fh, f"\n[gcs] ✓ {gcs_resp.output['gcs_uri']}", "ok")
                else:
                    _log(gui, log_fh, f"\n[gcs] Upload failed: {gcs_resp.error}", "err")

                captured = out_df["Margin Available for Trade"].notna().sum()

                try:
                    email_status = _send_report_email(out_df, total, captured, errors)
                    _log(gui, log_fh, f"[email] ✓ {email_status}", "ok")
                except Exception as email_exc:
                    _log(gui, log_fh, f"[email] Failed: {email_exc}", "err")

                gui.stop_progress()
                summary  = f"✓ All done!  {captured}/{total} captured."
                if errors:
                    summary += f"  {len(errors)} errors (see log)."
                gui.set_status(summary)
                _log(gui, log_fh, f"\n{summary}", "ok")
                log_fh.close()
                return

        except Exception as exc:
            gui.stop_progress()
            _log(gui, log_fh, f"\n[error] {exc}", "err")
            print(traceback.format_exc(), file=log_fh, flush=True)

            if not _edge_running():
                gui.set_status("Browser was closed — please log in again…")
                _log(gui, log_fh, "Edge was closed. Relaunching…", "warn")
                time.sleep(2)
                continue

            gui.set_status("Error — see log.")
            log_fh.close()
            return


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    gui  = RunnerGUI(root)

    t = threading.Thread(target=_run, args=(gui,), daemon=True)
    t.start()

    root.mainloop()


if __name__ == "__main__":
    main()
