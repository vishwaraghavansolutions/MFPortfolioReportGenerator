"""
campaign_dashboard.py
=====================
Streamlit dashboard for running, monitoring, and resuming the
MF Portfolio Report bulk email campaign.

Run with:
    streamlit run campaign_dashboard.py
"""

import json
import os
import queue
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

# ── Load .env before any agent/config imports ─────────────────────────────────
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(_env_file):
        _load_dotenv(_env_file, override=False)
except ImportError:
    pass

import pandas as pd
import streamlit as st

# ── Page config must be the very first Streamlit call ─────────────────────────
st.set_page_config(
    page_title="MF Report Campaign",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

from utils.auth import require_login
from utils.navbar import navbar
require_login()

# ── Inject Google Font + custom CSS ───────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Sora:wght@300;400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Sora', sans-serif;
}

/* ── Global background ── */
.stApp {
    background: #0d1117;
    color: #e6edf3;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #161b22;
    border-right: 1px solid #21262d;
}
section[data-testid="stSidebar"] * { color: #c9d1d9 !important; }
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stSlider label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stTextInput label {
    font-size: 0.72rem !important;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #8b949e !important;
    font-family: 'DM Mono', monospace !important;
}

/* ── Metric cards ── */
div[data-testid="metric-container"] {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 16px 20px;
}
div[data-testid="metric-container"] label {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.68rem !important;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #8b949e !important;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-family: 'Sora', sans-serif;
    font-size: 2rem !important;
    font-weight: 700;
    color: #e6edf3;
}

/* ── Buttons ── */
.stButton > button {
    font-family: 'DM Mono', monospace;
    font-size: 0.78rem;
    letter-spacing: 0.06em;
    border-radius: 6px;
    padding: 10px 22px;
    border: 1px solid #30363d;
    background: #21262d;
    color: #e6edf3;
    transition: all 0.15s ease;
    width: 100%;
}
.stButton > button:hover {
    border-color: #388bfd;
    background: #1f3a5f;
    color: #58a6ff;
}
.start-btn > button {
    background: linear-gradient(135deg, #238636 0%, #2ea043 100%) !important;
    border-color: #238636 !important;
    color: #fff !important;
    font-weight: 600 !important;
}
.start-btn > button:hover {
    background: linear-gradient(135deg, #2ea043 0%, #3fb950 100%) !important;
}
.stop-btn > button {
    background: linear-gradient(135deg, #b91c1c 0%, #dc2626 100%) !important;
    border-color: #b91c1c !important;
    color: #fff !important;
}
.resume-btn > button {
    background: linear-gradient(135deg, #1d4ed8 0%, #2563eb 100%) !important;
    border-color: #1d4ed8 !important;
    color: #fff !important;
}

/* ── Phase pill badges ── */
.phase-badge {
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.08em;
    padding: 3px 10px;
    border-radius: 20px;
    margin-right: 6px;
}
.phase-pdf  { background: #1f3a5f; color: #58a6ff; border: 1px solid #388bfd33; }
.phase-gcs  { background: #1a3a2a; color: #3fb950; border: 1px solid #2ea04333; }
.phase-mail { background: #3a1f3a; color: #d2a8ff; border: 1px solid #8957e533; }

/* ── Log box ── */
.log-box {
    background: #0d1117;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 14px 18px;
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    line-height: 1.8;
    color: #8b949e;
    max-height: 340px;
    overflow-y: auto;
}
.log-ok   { color: #3fb950; }
.log-err  { color: #f85149; }
.log-warn { color: #d29922; }
.log-info { color: #58a6ff; }

/* ── Status table ── */
.status-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.status-table th {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #8b949e;
    border-bottom: 1px solid #21262d;
    padding: 8px 12px;
    text-align: left;
}
.status-table td { padding: 7px 12px; border-bottom: 1px solid #161b22; }
.badge-success { color: #3fb950; font-family: 'DM Mono', monospace; }
.badge-failed  { color: #f85149; font-family: 'DM Mono', monospace; }
.badge-skipped { color: #d29922; font-family: 'DM Mono', monospace; }
.badge-pending { color: #8b949e; font-family: 'DM Mono', monospace; }

/* ── Section headers ── */
.section-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #8b949e;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #21262d;
}
h1, h2, h3 { font-family: 'Sora', sans-serif; }
h1 { font-weight: 700; color: #e6edf3; }

/* ── Progress bar override ── */
.stProgress > div > div > div > div {
    background: linear-gradient(90deg, #238636, #3fb950) !important;
    border-radius: 4px;
}

/* ── Expandable details ── */
.streamlit-expanderHeader {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.75rem !important;
    color: #8b949e !important;
}

/* ── Portfolio filter highlight ── */
.portfolio-filter-box {
    background: #1a2332;
    border: 1px solid #388bfd44;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 8px;
}
.filter-stat {
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    color: #58a6ff;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Session-state initialisation
# ══════════════════════════════════════════════════════════════════════════════

def _init_state():
    defaults = {
        "campaign_running":   False,
        "campaign_thread":    None,
        "stop_event":         None,
        "log_queue":          None,
        "log_lines":          [],
        "phase":              None,        # "pdf" | "gcs" | "email" | "done"
        "counts": {
            "total": 0, "succeeded": 0, "failed": 0, "skipped": 0,
            "pdf_done": 0, "gcs_done": 0,
        },
        "results": {
            "succeeded": [], "failed": [], "skipped": [],
        },
        "start_time":         None,
        "end_time":           None,
        "checkpoint_path":    None,
        "customer_list":      [],
        "selected_customers": [],
        "duration_s":         None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ══════════════════════════════════════════════════════════════════════════════
# Checkpoint reader (reads JSON directly — no agent import needed for UI)
# ══════════════════════════════════════════════════════════════════════════════

def _read_checkpoint(path: str) -> Optional[Dict]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _default_checkpoint_path() -> str:
    return f"campaign_checkpoint_{datetime.now().strftime('%Y%m%d')}.json"


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio value helpers
# ══════════════════════════════════════════════════════════════════════════════

# Candidate column names for "current portfolio value" — add more as needed

def _cr(val: float) -> float:
    """Convert raw rupee value to Crores."""
    return val / 1e7


def _from_cr(cr: float) -> float:
    """Convert Crores back to raw rupee value."""
    return cr * 1e7




def _from_cr(cr: float) -> float:
    """Convert Crores back to raw rupee value."""
    return cr * 1e7


# ══════════════════════════════════════════════════════════════════════════════
# Campaign runner (runs in a background thread)
# ══════════════════════════════════════════════════════════════════════════════

def _run_campaign_thread(params: Dict, log_q: queue.Queue, stop_evt: threading.Event):
    """
    Background thread that imports the orchestrator, monkey-patches the
    progress callbacks via queue messages, and drives the campaign.
    """
    def log(text, level="info"):
        log_q.put({"type": "log", "level": level, "text": f"[{_ts()}] {text}"})

    def _ts():
        return datetime.now().strftime("%H:%M:%S")

    try:
        log("Importing orchestrator...", "info")
        sys.path.insert(0, os.path.dirname(__file__))
        from agents.mf_portfolio_report_manager import PortfolioReportOrchestrator
        from utils.campaign_utils import CampaignCheckpoint, RateConfig

        orc = PortfolioReportOrchestrator()

        log("Loading customer list...", "info")
        list_res = orc.run("list_customers", {"csv_path": params.get("csv_path", "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv")})
        if list_res.status.name == "FAILED":
            log_q.put({"type": "error", "text": list_res.error})
            return

        all_customers = list_res.output["all_customers"]
        selected = params.get("customer_names") or all_customers
        log(f"Found {len(all_customers)} customers — processing {len(selected)}", "ok")

        # Log portfolio filter info if applied
        pf_min = params.get("portfolio_filter_min_cr")
        pf_max = params.get("portfolio_filter_max_cr")
        if pf_min is not None or pf_max is not None:
            log(f"Portfolio filter active: ≥ {pf_min} Cr — ≤ {pf_max} Cr", "info")

        checkpoint_path = params.get("checkpoint_path", _default_checkpoint_path())
        cp = CampaignCheckpoint(checkpoint_path)
        if params.get("fresh_start", False):
            cp.clear()
            log("Fresh start — checkpoint cleared", "warn")
        else:
            resumed = cp.load()
            if resumed:
                already_done = len(cp.succeeded)
                log(f"Resuming — {already_done} customers already completed", "warn")
                log_q.put({"type": "count", "key": "succeeded", "delta": already_done})

        t0 = time.monotonic()

        import concurrent.futures
        from utils.campaign_utils import TokenBucketRateLimiter, with_retry, chunk_list
        from agents.mf_portfolio_agent import MFPortfolioAgent
        from agents.gcs_storage_agent import GCSStorageAgent
        from agents.outlook_inbox_agent import OutlookInboxAgent

        # mf_agent is intentionally NOT created here — each _gen_pdf worker
        # creates its own MFPortfolioAgent() to avoid thread-safety issues
        # with the lazy-init sub-agents (WinrichMFDataAgent, IndexAgent, etc.)
        gcs_agent     = GCSStorageAgent()
        outlook_agent = OutlookInboxAgent()

        cfg = _build_rate_config(params)

        log_q.put({"type": "phase", "phase": "pdf"})
        log(f"Phase 1 — PDF generation | workers={cfg.pdf_workers}", "info")

        fresh_customers = [
            c for c in selected
            if not cp.is_done(c) and not cp.has_pdf(c) and not cp.has_gcs(c)
        ]
        resume_gcs   = [c for c in selected if not cp.is_done(c) and cp.has_pdf(c) and not cp.has_gcs(c)]
        resume_email = [c for c in selected if not cp.is_done(c) and cp.has_gcs(c)]

        phase1_results: Dict[str, str] = {}
        for name in resume_gcs + resume_email:
            phase1_results[name] = cp.pdf_done.get(name, "")

        raw_output_dir = params.get("output_dir", "reports") or "reports"
        norm_output_dir = os.path.normpath(os.path.abspath(raw_output_dir))
        os.makedirs(norm_output_dir, exist_ok=True)
        log(f"PDF output dir: {norm_output_dir}", "info")

        # All params forwarded to generate_quarterly_report
        shared_pdf = {k: params[k] for k in (
            "csv_path", "company_name", "skip_commentary",
            "parquet_dir", "bucket_name", "max_lookback_days",
            "logo_path", "risk_profile", "as_of_date",
        ) if k in params and params[k]}
        # Always pass filter values (0 = no bound, not None, so they always reach the agent)
        shared_pdf["portfolio_min_value"] = float(params.get("portfolio_min_value") or 0)
        shared_pdf["portfolio_max_value"] = float(params.get("portfolio_max_value") or 0)
        shared_pdf["output_dir"] = norm_output_dir

        def _gen_pdf(name):
            if stop_evt.is_set():
                return name, None, "Stopped by user", None
            try:
                # Each worker gets its own agent instance — the lazy-init
                # sub-agents (WinrichMFDataAgent, MFBenchmarkAgent, IndexAgent)
                # are not thread-safe when shared across threads.
                thread_agent = MFPortfolioAgent()
                res = with_retry(
                    lambda: thread_agent.run("generate_quarterly_report", {
                        **shared_pdf, "customer_name": name,
                    }),
                    max_retries=cfg.max_retries,
                    backoff_s=cfg.retry_backoff_s,
                    label=f"PDF:{name}",
                )
                if res.status.name == "SKIPPED":
                    return name, None, None, res.metadata.get("reason", "outside portfolio value range")
                if res.status.name == "FAILED":
                    return name, None, res.error, None
                return name, res.output["pdf_path"], None, None
            except Exception as e:
                return name, None, str(e), None

        if fresh_customers:
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.pdf_workers) as pool:
                futs = {pool.submit(_gen_pdf, c): c for c in fresh_customers}
                for fut in concurrent.futures.as_completed(futs):
                    if stop_evt.is_set():
                        break
                    name, pdf_path, err, skip_reason = fut.result()
                    if skip_reason:
                        log(f"⊘ Skipped {name}: {skip_reason}", "warn")
                        log_q.put({"type": "result", "bucket": "skipped",
                                   "entry": {"customer_name": name, "error": skip_reason}})
                        log_q.put({"type": "count", "key": "skipped", "delta": 1})
                        cp.mark_skipped(name, skip_reason); cp.save()
                    elif err:
                        log(f"✗ PDF {name}: {err}", "err")
                        log_q.put({"type": "result", "bucket": "failed",
                                   "entry": {"customer_name": name, "error": err}})
                        log_q.put({"type": "count", "key": "failed", "delta": 1})
                        cp.mark_failed(name, err); cp.save()
                    else:
                        log(f"✓ PDF {name}", "ok")
                        phase1_results[name] = pdf_path
                        log_q.put({"type": "count", "key": "pdf_done", "delta": 1})
                        cp.mark_pdf_done(name, pdf_path); cp.save()

        if stop_evt.is_set():
            log("Campaign stopped by user after Phase 1", "warn")
            log_q.put({"type": "done", "duration_s": round(time.monotonic() - t0, 1)})
            return

        # ═══════════════════════════════════════════════════════════════════
        # PHASE 2 — GCS Upload
        # ═══════════════════════════════════════════════════════════════════
        log_q.put({"type": "phase", "phase": "gcs"})
        log(f"Phase 2 — GCS upload | workers={cfg.gcs_workers} rate={cfg.gcs_rate_per_sec}/s", "info")

        gcs_limiter = TokenBucketRateLimiter(cfg.gcs_rate_per_sec, cfg.gcs_rate_per_sec * 2)
        phase2_results: Dict[str, str] = {}
        for name in resume_email:
            phase2_results[name] = cp.gcs_done.get(name, "")

        # Build GCS prefix from as_of_date: Quarterly/Mutual Fund Portfolio Reports/{Year}/Q{N}
        _aod_raw = params.get("as_of_date")
        try:
            _aod = datetime.strptime(_aod_raw[:10], "%Y-%m-%d") if _aod_raw else datetime.now()
        except Exception:
            _aod = datetime.now()
        _quarter  = (_aod.month - 1) // 3 + 1
        _gcs_prefix = f"Quarterly/Mutual Fund Portfolio Reports/{_aod.year}/Q{_quarter}"

        gcs_candidates = [n for n in phase1_results if not cp.has_gcs(n) and not cp.is_done(n)]

        def _upload_gcs(name):
            if stop_evt.is_set():
                return name, None, "Stopped by user"
            gcs_limiter.acquire()
            pdf_path = phase1_results.get(name, "")
            if not pdf_path:
                return name, None, "No PDF path"
            try:
                res = with_retry(
                    lambda: gcs_agent.run("upload_report", {
                        "pdf_path": pdf_path,
                        "customer_name": name,
                        "filename": os.path.basename(pdf_path),
                        "bucket_name": "winrich_customer_reports",
                        "prefix": _gcs_prefix,
                        "flat": True,
                    }),
                    max_retries=cfg.max_retries,
                    backoff_s=cfg.retry_backoff_s,
                    label=f"GCS:{name}",
                )
                if res.status.name == "SUCCESS":
                    return name, res.output["gcs_uri"], None
                return name, None, res.error
            except Exception as e:
                return name, None, str(e)

        if gcs_candidates and not params.get("skip_gcs", False):
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.gcs_workers) as pool:
                futs = {pool.submit(_upload_gcs, c): c for c in gcs_candidates}
                for fut in concurrent.futures.as_completed(futs):
                    if stop_evt.is_set():
                        break
                    name, gcs_uri, err = fut.result()
                    if gcs_uri:
                        log(f"✓ GCS {name} → {gcs_uri}", "ok")
                        phase2_results[name] = gcs_uri
                        log_q.put({"type": "count", "key": "gcs_done", "delta": 1})
                        cp.mark_gcs_done(name, gcs_uri); cp.save()
                    else:
                        log(f"⚠ GCS {name}: {err} (non-fatal, continuing)", "warn")
                        phase2_results[name] = ""
                        cp.mark_gcs_done(name, ""); cp.save()

        if stop_evt.is_set():
            log("Campaign stopped by user after Phase 2", "warn")
            log_q.put({"type": "done", "duration_s": round(time.monotonic() - t0, 1)})
            return

        # ═══════════════════════════════════════════════════════════════════
        # PHASE 3 — Email Delivery
        # ═══════════════════════════════════════════════════════════════════
        log_q.put({"type": "phase", "phase": "email"})
        log(f"Phase 3 — Email delivery | workers={cfg.email_workers} rate={cfg.email_rate_per_sec}/s batch={cfg.email_batch_size}", "info")

        email_limiter  = TokenBucketRateLimiter(cfg.email_rate_per_sec, cfg.email_rate_per_sec * 3)
        report_period  = datetime.now().strftime("%B %Y")
        company_name   = params.get("company_name", "Winrich Professional Services")
        csv_path       = params.get("csv_path", "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv")

        email_candidates = list({
            n for n in list(phase1_results.keys()) + resume_email
            if not cp.is_done(n)
        })

        def _resolve_email(name):
            try:
                df    = pd.read_csv(csv_path, usecols=["c_name", "Email"])
                match = df[df["c_name"] == name]
                if match.empty:
                    return ""
                addr = str(match.iloc[0]["Email"]).strip()
                return addr if "@" in addr else ""
            except Exception:
                return ""

        def _send_email(name):
            if stop_evt.is_set():
                return name, False, "Stopped by user"
            to_email = _resolve_email(name)
            if not to_email:
                return name, False, "No email address"
            email_limiter.acquire()
            try:
                res = with_retry(
                    lambda: outlook_agent.run("send_email", {
                        "to_email":      to_email,
                        "client_name":   name,
                        "subject":       f"Your {report_period} Portfolio Report — {company_name}",
                        "pdf_path":      phase1_results.get(name, cp.pdf_done.get(name, "")),
                        "company_name":  company_name,
                        "report_period": report_period,
                    }),
                    max_retries=cfg.max_retries,
                    backoff_s=cfg.retry_backoff_s,
                    label=f"Email:{name}",
                )
                sent = res.status.name == "SUCCESS"
                return name, sent, None if sent else res.error
            except Exception as e:
                return name, False, str(e)

        if not params.get("skip_email", False):
            batches = chunk_list(email_candidates, cfg.email_batch_size)
            for b_idx, batch in enumerate(batches):
                if stop_evt.is_set():
                    break
                log(f"Email batch {b_idx + 1}/{len(batches)} ({len(batch)} customers)", "info")
                with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.email_workers) as pool:
                    futs = {pool.submit(_send_email, c): c for c in batch}
                    for fut in concurrent.futures.as_completed(futs):
                        name, sent, err = fut.result()
                        pdf_path = phase1_results.get(name, cp.pdf_done.get(name, ""))
                        gcs_uri  = phase2_results.get(name, cp.gcs_done.get(name, ""))
                        to_email = _resolve_email(name)
                        if sent:
                            log(f"✓ Email → {to_email}", "ok")
                            entry = {"customer_name": name, "pdf_path": pdf_path,
                                     "gcs_uri": gcs_uri, "to_email": to_email, "email_sent": True}
                            log_q.put({"type": "result", "bucket": "succeeded", "entry": entry})
                            log_q.put({"type": "count", "key": "succeeded", "delta": 1})
                            cp.mark_succeeded(name); cp.save()
                        elif err == "No email address":
                            log(f"⚠ Skip {name}: no email", "warn")
                            entry = {"customer_name": name, "reason": "No email address", "pdf_path": pdf_path}
                            log_q.put({"type": "result", "bucket": "skipped", "entry": entry})
                            log_q.put({"type": "count", "key": "skipped", "delta": 1})
                            cp.mark_skipped(name, "No email address"); cp.save()
                        else:
                            log(f"✗ Email {name}: {err}", "err")
                            entry = {"customer_name": name, "error": err}
                            log_q.put({"type": "result", "bucket": "failed", "entry": entry})
                            log_q.put({"type": "count", "key": "failed", "delta": 1})
                            cp.mark_failed(name, err or "Email failed"); cp.save()

                if b_idx < len(batches) - 1 and not stop_evt.is_set():
                    log(f"Batch pause {cfg.email_batch_pause_s}s...", "warn")
                    time.sleep(cfg.email_batch_pause_s)
        else:
            for name in email_candidates:
                pdf_path = phase1_results.get(name, "")
                entry = {"customer_name": name, "reason": "skip_email=True", "pdf_path": pdf_path}
                log_q.put({"type": "result", "bucket": "skipped", "entry": entry})
                log_q.put({"type": "count", "key": "skipped", "delta": 1})
                cp.mark_skipped(name, "skip_email=True"); cp.save()

        duration = round(time.monotonic() - t0, 1)
        log_q.put({"type": "phase", "phase": "done"})
        log(f"Campaign complete in {duration}s", "ok")
        log_q.put({"type": "done", "duration_s": duration})

    except Exception as exc:
        import traceback
        log_q.put({"type": "error", "text": f"{exc}\n{traceback.format_exc()}"})


def _build_rate_config(params):
    from utils.campaign_utils import RateConfig
    preset = params.get("rate_preset", "").lower()
    if preset == "sendgrid":
        cfg = RateConfig.for_sendgrid()
    elif preset == "smtp_free":
        cfg = RateConfig.for_smtp_free()
    else:
        cfg = RateConfig()
    for f in ("pdf_workers", "gcs_workers", "gcs_rate_per_sec",
              "email_workers", "email_rate_per_sec",
              "email_batch_size", "email_batch_pause_s", "max_retries"):
        if f in params:
            setattr(cfg, f, params[f])
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# Queue drainer
# ══════════════════════════════════════════════════════════════════════════════

def _drain_queue():
    q: Optional[queue.Queue] = st.session_state.get("log_queue")
    if q is None:
        return
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        t = msg.get("type")
        if t == "log":
            css = {"ok": "log-ok", "err": "log-err", "warn": "log-warn", "info": "log-info"}.get(msg["level"], "")
            st.session_state["log_lines"].append(f'<span class="{css}">{msg["text"]}</span>')
        elif t == "phase":
            st.session_state["phase"] = msg["phase"]
            if msg["phase"] == "done":
                st.session_state["campaign_running"] = False
                st.session_state["end_time"]         = datetime.now()
        elif t == "count":
            st.session_state["counts"][msg["key"]] += msg["delta"]
        elif t == "result":
            st.session_state["results"][msg["bucket"]].append(msg["entry"])
        elif t == "done":
            st.session_state["duration_s"]         = msg["duration_s"]
            st.session_state["campaign_running"]   = False
            st.session_state["end_time"]           = datetime.now()
        elif t == "error":
            st.session_state["log_lines"].append(f'<span class="log-err">FATAL: {msg["text"]}</span>')
            st.session_state["campaign_running"] = False


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar — Configuration
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown('<div class="section-label">Campaign Config</div>', unsafe_allow_html=True)

    csv_path = st.text_input(
        "CSV path",
        value="data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv",
        help="Path to the data-warehouse CSV",
    )
    output_dir = st.text_input("PDF output directory", value="reports", help="Relative to where you run streamlit, or use an absolute path")
    company    = st.text_input("Company name", value="Winrich Professional Services")

    st.markdown('<div class="section-label" style="margin-top:18px">Email Transport</div>', unsafe_allow_html=True)
    st.caption("📧 Sending via Microsoft Outlook (Graph API)")

    st.markdown('<div class="section-label" style="margin-top:18px">Throttle Overrides</div>', unsafe_allow_html=True)
    pdf_workers        = st.slider("PDF workers",         1, 12, 4)
    gcs_workers        = st.slider("GCS workers",         1, 20, 10)
    email_workers      = st.slider("Email workers",       1,  8,  3)
    email_rate_per_sec = st.slider("Email rate (req/s)",  0.1, 20.0, 2.0, step=0.1)
    email_batch_size   = st.number_input("Email batch size", min_value=10, max_value=200, value=50)
    email_batch_pause  = st.number_input("Batch pause (s)",  min_value=5,  max_value=300, value=30)

    st.markdown('<div class="section-label" style="margin-top:18px">Options</div>', unsafe_allow_html=True)
    skip_commentary = st.checkbox("Skip AI commentary (faster)", value=False)
    skip_gcs        = st.checkbox("Skip GCS upload",             value=False)
    skip_email      = st.checkbox("Dry-run (no emails)",         value=False)
    fresh_start     = st.checkbox("Fresh start (ignore checkpoint)", value=False)

    st.markdown('<div class="section-label" style="margin-top:18px">Report Settings</div>', unsafe_allow_html=True)
    parquet_dir       = st.text_input("Parquet data dir",   value="data",   help="Directory containing Index_Dashboard_*.parquet and SchemeData CSV files")
    bucket_name       = st.text_input("GCS bucket (data)",  value="winrich", help="GCS bucket for QoQ portfolio parquet files (WinrichMFDataAgent)")
    max_lookback_days = st.slider("Max lookback days", 1, 30, 10, help="How many days back to search for the latest portfolio data file")
    logo_path         = st.text_input("Logo path",          value="assets/winrich-logo.png", help="Absolute path to company logo PNG/JPG for the PDF report")
    risk_profile      = st.text_input("Risk profile label", value="",       help="e.g. 'Moderate', 'Aggressive' — shown on the PDF")
    as_of_date        = st.date_input("As of date", value=None,             help="Report as-of date. Leave blank to use today.")
    as_of_date_str    = as_of_date.strftime("%Y-%m-%d") if as_of_date else ""

    # portfolio_min_value / portfolio_max_value are derived from the
    # "Portfolio value range" slider in the Customer Selection section below.
    # Initialised here so they are always defined; overwritten once the slider renders.
    portfolio_min_value = 0.0
    portfolio_max_value = 0.0


    # ── Customer Selection ────────────────────────────────────────────────────
    st.markdown('<div class="section-label" style="margin-top:18px">Customer Selection</div>', unsafe_allow_html=True)

    # Step 1: list_customers via agent — one call, cached per csv_path
    @st.cache_data(show_spinner=False)
    def _agent_list_customers(csv_path: str):
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(__file__))
            from agents.mf_portfolio_agent import MFPortfolioAgent
            resp = MFPortfolioAgent().run("list_customers", {"csv_path": csv_path})
            if resp.status.name == "SUCCESS":
                return sorted(resp.output.get("all_customers", []))
            return []
        except Exception:
            return []

    # Portfolio-summary loader — returns {customer_name: total_value} from GCS parquet.
    # TTL=300s so values refresh automatically without a page reload.
    @st.cache_data(show_spinner=False, ttl=300)
    def _load_portfolio_summary_data() -> dict:
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(__file__))
            from agents.gcs_storage_agent import GCSStorageAgent
            resp = GCSStorageAgent().run("load_portfolio_summary", {})
            if resp.status.name == "SUCCESS" and resp.output.get("row_count", 0) > 0:
                df = resp.output["dataframe"]
                if "customer_name" in df.columns and "total_value" in df.columns:
                    return dict(zip(df["customer_name"], df["total_value"].fillna(0)))
            return {}
        except Exception:
            return {}

    all_customers   = _agent_list_customers(csv_path)
    total_customers = len(all_customers)

    if not all_customers:
        st.warning("Could not load customer list — check CSV path.")
        selected_customers    = []
        pf_min_cr = pf_max_cr = 0.0
        enable_portfolio_filter = False
        pf_filtered_customers = []
        portfolio_min_value = 0.0
        portfolio_max_value = 0.0
    else:
        # Step 2: Load portfolio summary and apply value filter
        summary_values = _load_portfolio_summary_data()
        n_with_summary = sum(1 for c in all_customers if c in summary_values)
        n_no_summary   = total_customers - n_with_summary

        include_no_summary = st.checkbox(
            "Include customers with no summary data",
            value=True,
            help="Customers whose report has never been generated have no portfolio-summary entry. "
                 "Enable to process them regardless of the value filter.",
        )

        # Derive slider bounds from only the customers that are in scope
        _candidates = (
            list(summary_values.values())
            if not include_no_summary
            else list(summary_values.values())   # bounds always from known values
        )
        _max_known  = max(_candidates, default=0) / 1e7 if _candidates else 0
        _slider_max = max(10.0, round(_max_known / 10 + 0.5) * 10)

        # When checkbox is unchecked and no summary data exists at all, disable the slider
        _has_summary_data = bool(summary_values)
        if not _has_summary_data and not include_no_summary:
            st.info("No portfolio summary data available — uncheck 'Include customers with no summary data' would result in 0 customers.")
            pf_min_cr, pf_max_cr = 0.0, 0.0
        else:
            pf_min_cr, pf_max_cr = st.slider(
                "Portfolio value range (₹ Cr)",
                min_value=0.0,
                max_value=float(_slider_max),
                value=(0.0, float(_slider_max)),
                step=0.5,
                help="Filter to customers whose stored total_value is in this range.",
            )

        # 0 = sentinel for "no bound" passed into the report agent
        portfolio_min_value = pf_min_cr * 1e7                                          # 0.0 → no minimum
        portfolio_max_value = pf_max_cr * 1e7 if pf_max_cr < _slider_max else 0.0     # 0.0 → no maximum

        min_raw = _from_cr(pf_min_cr)
        max_raw = _from_cr(pf_max_cr)

        n_in_range = sum(
            1 for c in all_customers
            if c in summary_values and min_raw <= summary_values[c] <= max_raw
        )

        if summary_values:
            pf_filtered_customers = [
                c for c in all_customers
                if (c in summary_values and min_raw <= summary_values[c] <= max_raw)
                or (include_no_summary and c not in summary_values)
            ]
        else:
            pf_filtered_customers = all_customers

        n_filtered = len(pf_filtered_customers)

        st.markdown(
            f'<div class="portfolio-filter-box">'
            f'<span class="filter-stat">'
            f'▸ {total_customers} total  •  '
            f'{n_with_summary} with summary  •  '
            f'{n_in_range} in ₹{pf_min_cr:.1f}–{pf_max_cr:.1f} Cr  •  '
            f'{n_no_summary} no data  →  <b>{n_filtered} to process</b>'
            f'</span></div>',
            unsafe_allow_html=True,
        )

        # Step 3: Count slider — first N from the filtered list
        if n_filtered == 0:
            st.warning("No customers match the current filter — adjust the range or enable 'Include customers with no summary data'.")
            n_customers = 0
        elif n_filtered == 1:
            st.info("1 customer matches the current filter.")
            n_customers = 1
        else:
            n_customers = st.slider(
                f"Number to process (of {n_filtered} filtered)",
                min_value=1,
                max_value=n_filtered,
                value=min(n_filtered, 50),
                help="Picks the first N customers from the portfolio-filtered list.",
            )
        pool_after_n = pf_filtered_customers[:n_customers]

        # Step 4: Optional specific-customer multiselect
        use_multiselect = st.checkbox("Pick specific customers", value=False)
        if use_multiselect:
            selected_customers = st.multiselect(
                "Customers",
                options=pf_filtered_customers,
                default=pool_after_n[:10],
                help="Overrides the count slider.",
            )
        else:
            selected_customers = pool_after_n

        st.caption(f"**{len(selected_customers)}** customers selected")

        enable_portfolio_filter = True

    checkpoint_path = st.text_input(
        "Checkpoint file",
        value=_default_checkpoint_path(),
        help="JSON file that tracks progress. Re-use the same path to resume.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Build params dict from sidebar values
# ══════════════════════════════════════════════════════════════════════════════

_norm_output_dir = os.path.normpath(os.path.abspath(output_dir)) if output_dir else os.path.abspath("reports")

campaign_params = {
    "csv_path":               csv_path,
    "output_dir":             _norm_output_dir,
    "company_name":           company,
    "pdf_workers":            pdf_workers,
    "gcs_workers":            gcs_workers,
    "email_workers":          email_workers,
    "email_rate_per_sec":     email_rate_per_sec,
    "email_batch_size":       email_batch_size,
    "email_batch_pause_s":    email_batch_pause,
    "skip_commentary":        skip_commentary,
    "skip_gcs":               skip_gcs,
    "skip_email":             skip_email,
    "fresh_start":            fresh_start,
    "checkpoint_path":        checkpoint_path,
    "customer_names":         selected_customers if selected_customers else None,
    # Report generation settings passed through to generate_quarterly_report
    "parquet_dir":            parquet_dir,
    "bucket_name":            bucket_name,
    "max_lookback_days":      max_lookback_days,
    "logo_path":              logo_path,
    "risk_profile":           risk_profile,
    "as_of_date":             as_of_date_str,
    # Value range from "Portfolio value range" slider — customers outside are skipped during generation
    "portfolio_min_value": portfolio_min_value,
    "portfolio_max_value": portfolio_max_value,
}


# ══════════════════════════════════════════════════════════════════════════════
# Main area — Header
# ══════════════════════════════════════════════════════════════════════════════

navbar()
st.markdown("## 📊 MF Portfolio Report Campaign")

phase_map = {
    None:    ("—",    ""),
    "pdf":   ("Phase 1",  "phase-pdf"),
    "gcs":   ("Phase 2",  "phase-gcs"),
    "email": ("Phase 3",  "phase-mail"),
    "done":  ("Complete", "phase-gcs"),
}
phase_label, phase_class = phase_map.get(st.session_state["phase"], ("—", ""))
phase_html = f'<span class="phase-badge {phase_class}">{phase_label}</span>' if phase_class else ""

running_html = (
    '<span class="phase-badge phase-gcs">● Running</span>'
    if st.session_state["campaign_running"]
    else '<span class="phase-badge" style="background:#161b22;color:#8b949e;border:1px solid #21262d">● Idle</span>'
)

# Portfolio filter badge in header
if enable_portfolio_filter and pf_min_cr is not None:
    pf_badge = (
        f'<span class="phase-badge" style="background:#1a2332;color:#58a6ff;border:1px solid #388bfd44">'
        f'₹ ≥ {pf_min_cr:.1f} Cr</span>'
    )
else:
    pf_badge = ""

st.markdown(f"{running_html} {phase_html} {pf_badge}", unsafe_allow_html=True)
st.markdown("---")


# Control buttons
# ══════════════════════════════════════════════════════════════════════════════

_drain_queue()

col_start, col_resume, col_stop, col_reset = st.columns([1.2, 1.2, 1, 1])

with col_start:
    can_start = not st.session_state["campaign_running"]
    st.markdown('<div class="start-btn">', unsafe_allow_html=True)
    if st.button("▶  Start Campaign", disabled=not can_start):
        st.session_state["log_lines"]   = []
        st.session_state["phase"]       = "pdf"
        st.session_state["counts"]      = {"total": len(selected_customers or all_customers),
                                            "succeeded": 0, "failed": 0, "skipped": 0,
                                            "pdf_done": 0, "gcs_done": 0}
        st.session_state["results"]     = {"succeeded": [], "failed": [], "skipped": []}
        st.session_state["start_time"]  = datetime.now()
        st.session_state["end_time"]    = None
        st.session_state["duration_s"]  = None
        st.session_state["checkpoint_path"] = checkpoint_path

        stop_evt = threading.Event()
        log_q    = queue.Queue()
        st.session_state["stop_event"]      = stop_evt
        st.session_state["log_queue"]       = log_q
        st.session_state["campaign_running"] = True

        t = threading.Thread(
            target=_run_campaign_thread,
            args=(campaign_params, log_q, stop_evt),
            daemon=True,
        )
        t.start()
        st.session_state["campaign_thread"] = t
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

with col_resume:
    cp_data = _read_checkpoint(checkpoint_path)
    has_checkpoint = cp_data is not None
    st.markdown('<div class="resume-btn">', unsafe_allow_html=True)
    if st.button("↺  Resume", disabled=st.session_state["campaign_running"] or not has_checkpoint):
        resume_params = {**campaign_params, "fresh_start": False}
        st.session_state["log_lines"]  = []
        st.session_state["phase"]      = "pdf"
        st.session_state["results"]    = {"succeeded": [], "failed": [], "skipped": []}
        st.session_state["start_time"] = datetime.now()
        st.session_state["end_time"]   = None
        st.session_state["checkpoint_path"] = checkpoint_path

        if cp_data:
            st.session_state["counts"] = {
                "total":     len(selected_customers or all_customers),
                "succeeded": len(cp_data.get("succeeded", [])),
                "failed":    len(cp_data.get("failed",    {})),
                "skipped":   len(cp_data.get("skipped",   {})),
                "pdf_done":  len(cp_data.get("pdf_done",  {})),
                "gcs_done":  len(cp_data.get("gcs_done",  {})),
            }

        stop_evt = threading.Event()
        log_q    = queue.Queue()
        st.session_state["stop_event"]       = stop_evt
        st.session_state["log_queue"]        = log_q
        st.session_state["campaign_running"] = True

        t = threading.Thread(
            target=_run_campaign_thread,
            args=(resume_params, log_q, stop_evt),
            daemon=True,
        )
        t.start()
        st.session_state["campaign_thread"] = t
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

with col_stop:
    st.markdown('<div class="stop-btn">', unsafe_allow_html=True)
    if st.button("■  Stop", disabled=not st.session_state["campaign_running"]):
        if st.session_state.get("stop_event"):
            st.session_state["stop_event"].set()
        st.session_state["campaign_running"] = False
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

with col_reset:
    if st.button("⟳  Reset UI", disabled=st.session_state["campaign_running"]):
        for k in ("log_lines", "phase", "counts", "results", "start_time",
                  "end_time", "duration_s"):
            del st.session_state[k]
        _init_state()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Checkpoint info banner
# ══════════════════════════════════════════════════════════════════════════════

if has_checkpoint and cp_data:
    done_n    = len(cp_data.get("succeeded", []))
    failed_n  = len(cp_data.get("failed",    {}))
    updated   = cp_data.get("updated_at", "unknown")[:19].replace("T", " ")
    st.info(f"📌 Checkpoint found — **{done_n}** completed, **{failed_n}** failed (last updated {updated}). Click **Resume** to continue.")


# ══════════════════════════════════════════════════════════════════════════════
# Metrics row
# ══════════════════════════════════════════════════════════════════════════════

counts = st.session_state["counts"]
total  = counts["total"] or 1
done   = counts["succeeded"] + counts["failed"] + counts["skipped"]

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total",     counts["total"])
m2.metric("✓ Sent",    counts["succeeded"])
m3.metric("✗ Failed",  counts["failed"])
m4.metric("⊘ Skipped", counts["skipped"])
m5.metric("PDF ✓",     counts["pdf_done"])
m6.metric("GCS ✓",     counts["gcs_done"])

progress_pct = done / max(counts["total"], 1)
st.progress(progress_pct)

if st.session_state["start_time"]:
    elapsed = (
        (st.session_state["end_time"] or datetime.now()) - st.session_state["start_time"]
    ).total_seconds()
    speed = counts["succeeded"] / max(elapsed, 1) * 60
    cols  = st.columns(3)
    cols[0].caption(f"⏱ Elapsed: **{elapsed:.0f}s**")
    cols[1].caption(f"⚡ Throughput: **{speed:.1f}** sent/min")
    remaining = counts["total"] - done
    eta_s = remaining / max(speed / 60, 0.001)
    cols[2].caption(f"🏁 ETA: **{eta_s/60:.0f}m**" if not st.session_state["end_time"] else "🏁 Completed")


# ══════════════════════════════════════════════════════════════════════════════
# Phase progress indicators
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown('<div class="section-label">Pipeline Phases</div>', unsafe_allow_html=True)

ph_col1, ph_col2, ph_col3 = st.columns(3)
current_phase = st.session_state.get("phase")

def _phase_card(col, label, badge_class, key_done, key_total_override=None):
    done_v  = counts.get(key_done, 0)
    total_v = key_total_override or counts["total"]
    pct     = done_v / max(total_v, 1)
    active  = "▶ " if current_phase == key_done.replace("_done", "") else "  "
    col.markdown(
        f'<span class="phase-badge {badge_class}">{active}{label}</span>'
        f'<br><small style="font-family:\'DM Mono\';color:#8b949e">'
        f'{done_v} / {total_v}</small>',
        unsafe_allow_html=True,
    )
    col.progress(pct)

_phase_card(ph_col1, "PDF Generation", "phase-pdf",  "pdf_done")
_phase_card(ph_col2, "GCS Upload",     "phase-gcs",  "gcs_done")
_phase_card(ph_col3, "Email Delivery", "phase-mail", "succeeded")


# ══════════════════════════════════════════════════════════════════════════════
# Live log
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown('<div class="section-label">Live Log</div>', unsafe_allow_html=True)

log_lines = st.session_state.get("log_lines", [])
if log_lines:
    log_html = "<br>".join(log_lines[-120:])
    st.markdown(f'<div class="log-box">{log_html}</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="log-box"><span class="log-info">Waiting for campaign to start...</span></div>',
                unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Results tables
# ══════════════════════════════════════════════════════════════════════════════

results = st.session_state["results"]

st.markdown("---")
tab_success, tab_failed, tab_skipped = st.tabs([
    f"✓ Succeeded ({len(results['succeeded'])})",
    f"✗ Failed ({len(results['failed'])})",
    f"⊘ Skipped ({len(results['skipped'])})",
])

with tab_success:
    if results["succeeded"]:
        df_s = pd.DataFrame(results["succeeded"])[
            ["customer_name", "to_email", "email_sent", "gcs_uri"]
        ]
        st.dataframe(df_s, use_container_width=True, hide_index=True)
    else:
        st.caption("No results yet.")

with tab_failed:
    if results["failed"]:
        df_f = pd.DataFrame(results["failed"])
        st.dataframe(df_f, use_container_width=True, hide_index=True)
        if not st.session_state["campaign_running"]:
            if st.button("↺ Retry failed customers"):
                retry_names = [r["customer_name"] for r in results["failed"]]
                retry_params = {**campaign_params, "customer_names": retry_names, "fresh_start": False}
                stop_evt = threading.Event()
                log_q    = queue.Queue()
                st.session_state["stop_event"]       = stop_evt
                st.session_state["log_queue"]        = log_q
                st.session_state["campaign_running"] = True
                st.session_state["log_lines"]        = []
                t = threading.Thread(
                    target=_run_campaign_thread,
                    args=(retry_params, log_q, stop_evt),
                    daemon=True,
                )
                t.start()
                st.session_state["campaign_thread"] = t
                st.rerun()
    else:
        st.caption("No failures.")

with tab_skipped:
    if results["skipped"]:
        df_sk = pd.DataFrame(results["skipped"])
        st.dataframe(df_sk, use_container_width=True, hide_index=True)
    else:
        st.caption("No skipped customers.")


# ══════════════════════════════════════════════════════════════════════════════
# Checkpoint viewer
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("🔍 Checkpoint Inspector", expanded=False):
    cp_data = _read_checkpoint(checkpoint_path)
    if cp_data:
        c1, c2, c3 = st.columns(3)
        c1.metric("Succeeded",   len(cp_data.get("succeeded", [])))
        c2.metric("Failed",      len(cp_data.get("failed",    {})))
        c3.metric("Skipped",     len(cp_data.get("skipped",   {})))
        st.caption(f"Started: {cp_data.get('started_at','?')[:19]} | Updated: {cp_data.get('updated_at','?')[:19]}")
        st.json(cp_data, expanded=False)
        if st.button("🗑 Delete checkpoint"):
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
            st.success("Checkpoint deleted.")
            st.rerun()
    else:
        st.caption("No checkpoint file found at the configured path.")


# ══════════════════════════════════════════════════════════════════════════════
# Auto-refresh while running
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state["campaign_running"]:
    time.sleep(1.5)
    st.rerun()