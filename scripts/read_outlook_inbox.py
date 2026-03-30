"""
scripts/read_outlook_inbox.py
──────────────────────────────
CLI utility to read the Outlook inbox via the OutlookInboxAgent.

Usage
-----
  python read_outlook_inbox.py [options]

Options
-------
  --action          fetch_inbox | fetch_email | search_emails   (default: fetch_inbox)
  --top             Number of messages to fetch                  (default: 25)
  --skip            Pagination offset                            (default: 0)
  --folder          Mail folder name                             (default: inbox)
  --id              Message ID — required for fetch_email
  --subject         Subject keyword filter  (for search_emails)
  --sender          Sender email filter     (for search_emails)
  --unread          Show only unread mail   (flag)
  --attach          Show only mail with attachments (flag)
  --after           Received after date  YYYY-MM-DD (for search_emails)
  --before          Received before date YYYY-MM-DD (for search_emails)
  --show-attachments  List attachments for every matching email (flag)
  --ipru            Shortcut: search emails from IPRUAUTOMAILER@icicipruamc.com
                    and list all attachments (sets --sender and --show-attachments)

Environment variables required
--------------------------------
  MS_CLIENT_ID
  MS_CLIENT_SECRET
  MS_TENANT_ID
  MS_GRAPH_MAILBOX        (can be overridden per run with --mailbox)

Example .env (do NOT commit this file)
----------------------------------------
  MS_CLIENT_ID=4bec8f7d-...
  MS_CLIENT_SECRET=iHD8Q~...
  MS_TENANT_ID=6a6ecc80-...
  MS_GRAPH_MAILBOX=user@company.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from textwrap import shorten

# ── resolve project root so the agents package is importable ─────────────────
# AngelOneRunner/scripts/ → AngelOneRunner/ → look for MFPortfolioReportGenerator
_SCRIPT_DIR  = Path(__file__).resolve().parent
_RUNNER_ROOT = _SCRIPT_DIR.parent

# Try to find the MFPortfolioReportGenerator project alongside or on PYTHONPATH
_MF_ROOT = Path(r"C:\MyWorkSpace\Ourlife\Apps\Python\MFPortfolioReportGenerator")
if _MF_ROOT.exists():
    sys.path.insert(0, str(_MF_ROOT))
else:
    # Fall back: look relative to this script
    for candidate in _RUNNER_ROOT.parent.rglob("agents/outlook_inbox_agent.py"):
        sys.path.insert(0, str(candidate.parent.parent))
        break


# ── load .env if python-dotenv is installed ──────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_file = _RUNNER_ROOT / ".env"
    print(f"[info] Looking for .env at {_env_file} …")
    if _env_file.exists():
        load_dotenv(_env_file)
        print(f"[info] Loaded env from {_env_file}")
except ImportError:
    pass  # dotenv is optional


# ── import agent ─────────────────────────────────────────────────────────────
try:
    from agents.outlook_inbox_agent import OutlookInboxAgent
except ImportError as exc:
    sys.exit(
        f"[error] Cannot import OutlookInboxAgent: {exc}\n"
        "Ensure MFPortfolioReportGenerator is on PYTHONPATH or update _MF_ROOT in this script."
    )


# ── Formatters ────────────────────────────────────────────────────────────────

_IPRU_SENDER = "IPRUAUTOMAILER@icicipruamc.com"


def _fmt_message_row(msg: dict, idx: int) -> None:
    read_flag   = " " if msg["is_read"] else "*"
    attach_flag = "+" if msg["has_attachments"] else " "
    print(
        f"  [{idx:>3}] {read_flag}{attach_flag} "
        f"{msg['received_at'][:10]}  "
        f"{shorten(msg['sender_email'], 30, placeholder='…'):<30}  "
        f"{shorten(msg['subject'], 55, placeholder='…')}"
    )


def _fmt_attachments(attachments: list) -> None:
    if not attachments:
        print("       (no attachments)")
        return
    for a in attachments:
        print(f"       • {a['name']:<50}  {a['size_kb']:>8.1f} KB  {a['content_type']}")


def _fmt_full_message(msg: dict) -> None:
    print("=" * 72)
    print(f"Subject  : {msg['subject']}")
    print(f"From     : {msg['sender_name']} <{msg['sender_email']}>")
    print(f"Received : {msg['received_at']}")
    print(f"Read     : {msg['is_read']}   Attachments: {msg['has_attachments']}")
    print(f"Type     : {msg.get('body_type', 'n/a')}")
    print("-" * 72)
    body = msg.get("body_content", "")
    if msg.get("body_type") == "html":
        # Strip basic HTML tags for terminal display
        import re
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"\s{2,}", " ", body).strip()
    print(body[:2000])
    if len(body) > 2000:
        print(f"\n... (truncated, {len(body)} chars total)")
    print("=" * 72)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read Outlook inbox via MS Graph API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--action",  default="fetch_inbox",
                        choices=["fetch_inbox", "fetch_email", "search_emails"])
    parser.add_argument("--mailbox", default="", help="Override MS_GRAPH_MAILBOX")
    parser.add_argument("--top",     type=int, default=25)
    parser.add_argument("--skip",    type=int, default=0)
    parser.add_argument("--folder",  default="inbox")
    parser.add_argument("--id",      default="", dest="message_id",
                        help="Message ID for fetch_email")
    parser.add_argument("--subject", default="", help="Subject keyword filter")
    parser.add_argument("--sender",  default="", help="Sender email filter")
    parser.add_argument("--unread",  action="store_true", help="Unread only")
    parser.add_argument("--attach",  action="store_true", help="Has attachments only")
    parser.add_argument("--after",   default="", help="Received after YYYY-MM-DD")
    parser.add_argument("--before",  default="", help="Received before YYYY-MM-DD")
    parser.add_argument("--days",    type=int, default=0,
                        help="Shortcut: received in the last N days (overrides --after)")
    parser.add_argument("--show-attachments", action="store_true", dest="show_attachments",
                        help="List attachments for every matching email")
    parser.add_argument("--download-dir", default="", dest="download_dir",
                        help="Download attachments into this folder (implies --show-attachments)")
    parser.add_argument("--ipru",    action="store_true",
                        help=f"Shortcut: search from {_IPRU_SENDER} and list attachments")
    parser.add_argument("--json",    action="store_true", dest="as_json",
                        help="Print raw JSON output")

    args = parser.parse_args()

    # --download-dir implies --show-attachments
    if args.download_dir:
        args.show_attachments = True

    # --days N → compute received_after as N days ago
    if args.days > 0:
        args.after  = (date.today() - timedelta(days=args.days)).isoformat()
        args.action = "search_emails"

    # --ipru sets sender + show-attachments + forces search_emails action
    if args.ipru:
        args.sender           = _IPRU_SENDER
        args.show_attachments = True
        args.action           = "search_emails"

    agent = OutlookInboxAgent()

    # ── Build params per action ───────────────────────────────────────────────
    if args.action == "fetch_inbox":
        params: dict = {
            "top":    args.top,
            "skip":   args.skip,
            "folder": args.folder,
        }
        if args.mailbox:
            params["mailbox"] = args.mailbox

        print(f"\nFetching inbox (top={args.top}, skip={args.skip}) …")
        result = agent.run("fetch_inbox", params)

    elif args.action == "fetch_email":
        if not args.message_id:
            sys.exit("[error] --id <message_id> is required for fetch_email")
        params = {"message_id": args.message_id}
        if args.mailbox:
            params["mailbox"] = args.mailbox

        print(f"\nFetching message {args.message_id} …")
        result = agent.run("fetch_email", params)

    else:  # search_emails
        params = {
            "top":    args.top,
            "folder": args.folder,
        }
        if args.mailbox:        params["mailbox"]         = args.mailbox
        if args.subject:        params["subject"]         = args.subject
        if args.sender:         params["sender_email"]    = args.sender
        if args.unread:         params["unread_only"]     = True
        if args.attach:         params["has_attachments"] = True
        if args.after:          params["received_after"]  = args.after
        if args.before:         params["received_before"] = args.before

        print(f"\nSearching emails …")
        result = agent.run("search_emails", params)

    # ── Output ────────────────────────────────────────────────────────────────
    if result.status.value != "success":
        print(f"\n[FAILED] {result.error}")
        sys.exit(1)

    if args.as_json:
        print(json.dumps(result.output, indent=2, default=str))
        return

    if args.action == "fetch_email":
        _fmt_full_message(result.output["message"])
        return

    # List view (fetch_inbox / search_emails)
    messages = result.output.get("messages", [])
    count    = result.output.get("count", 0)
    print(f"\n  {'#':>4}  {'R A':>3}  {'Date':<10}  {'Sender':<30}  Subject")
    print("  " + "-" * 105)
    for i, msg in enumerate(messages, start=1):
        _fmt_message_row(msg, i)
        if args.show_attachments and msg["has_attachments"]:
            att_result = agent.run("list_attachments", {"message_id": msg["id"]})
            if att_result.status.value != "success":
                print(f"       [error fetching attachments: {att_result.error}]")
                continue
            attachments = att_result.output.get("attachments", [])
            _fmt_attachments(attachments)
            if args.download_dir:
                for att in attachments:
                    dl = agent.run("download_attachment", {
                        "message_id":    msg["id"],
                        "attachment_id": att["id"],
                        "file_name":     att["name"],
                        "save_dir":      args.download_dir,
                    })
                    if dl.status.value == "success":
                        print(f"         → saved: {dl.output['saved_path']}")
                    else:
                        print(f"         → [download failed: {dl.error}]")

    print(f"\n  {count} message(s) shown.  (* = unread, + = has attachment)")

    next_skip = result.output.get("next_skip")
    if next_skip is not None:
        print(f"  More messages available — re-run with --skip {next_skip}")


if __name__ == "__main__":
    main()
