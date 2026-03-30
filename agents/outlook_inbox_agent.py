"""
OutlookInboxAgent
=================
Reads and sends Outlook / Microsoft 365 emails via the Microsoft Graph API.

Authentication uses the OAuth2 client-credentials flow (app-only), which
means no interactive login is required — suitable for background/automated
scripts, provided the Azure AD application has been granted the Mail.Read
(or Mail.ReadWrite) and Mail.Send application permissions and admin consent
has been given.

Environment variables
---------------------
MS_CLIENT_ID      : Azure AD application (client) ID
MS_CLIENT_SECRET  : Client secret value
MS_TENANT_ID      : Azure AD tenant ID
MS_GRAPH_MAILBOX  : Mailbox UPN to use, e.g. user@company.com

Skills (public)
---------------
  fetch_inbox         – List messages from the inbox (latest first, paginated)
  fetch_email         – Retrieve a single message by its Graph message ID
  search_emails       – Filter messages by subject keyword, sender, or date range
  list_attachments    – List attachment metadata for a given message
  download_attachment – Download and save a single attachment
  send_email          – Send an email with optional PDF attachment
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agents.base import Agent, AgentResponse, AgentStatus

# ── optional imports (fail gracefully at skill call time) ─────────────────────
try:
    import requests
    from msal import ConfidentialClientApplication
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY   = "https://login.microsoftonline.com/{tenant_id}"
_SCOPE       = ["https://graph.microsoft.com/.default"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_deps() -> None:
    if not _DEPS_OK:
        raise ImportError(
            "Required packages not installed. Run:\n"
            "  pip install requests msal"
        )


def _get_config() -> Dict[str, str]:
    cfg = {
        "client_id":     os.environ.get("MS_CLIENT_ID",     ""),
        "client_secret": os.environ.get("MS_CLIENT_SECRET", ""),
        "tenant_id":     os.environ.get("MS_TENANT_ID",     ""),
        "mailbox":       os.environ.get("MS_GRAPH_MAILBOX", ""),
    }
    _ENV_NAMES = {
        "client_id":     "MS_CLIENT_ID",
        "client_secret": "MS_CLIENT_SECRET",
        "tenant_id":     "MS_TENANT_ID",
        "mailbox":       "MS_GRAPH_MAILBOX",
    }
    missing = [_ENV_NAMES[k] for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(
            f"Missing environment variable(s): " + ", ".join(missing)
        )
    return cfg


def _acquire_token(cfg: Dict[str, str]) -> str:
    """Acquire an app-only access token via MSAL."""
    authority = _AUTHORITY.format(tenant_id=cfg["tenant_id"])
    app = ConfidentialClientApplication(
        cfg["client_id"],
        authority=authority,
        client_credential=cfg["client_secret"],
    )
    result = app.acquire_token_for_client(scopes=_SCOPE)
    if "access_token" not in result:
        error = result.get("error_description") or result.get("error", "Unknown error")
        raise RuntimeError(f"Token acquisition failed: {error}")
    return result["access_token"]


def _graph_get(token: str, url: str, params: Optional[Dict] = None) -> Dict:
    """Execute a GET request against Graph API, raise on non-2xx."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if not resp.ok:
        raise RuntimeError(
            f"Graph API error {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _parse_message(msg: Dict) -> Dict:
    """Flatten a Graph message object into a cleaner dict."""
    sender_obj  = msg.get("from", {}).get("emailAddress", {})
    return {
        "id":           msg.get("id", ""),
        "subject":      msg.get("subject", "(no subject)"),
        "sender_name":  sender_obj.get("name", ""),
        "sender_email": sender_obj.get("address", ""),
        "received_at":  msg.get("receivedDateTime", ""),
        "is_read":      msg.get("isRead", False),
        "has_attachments": msg.get("hasAttachments", False),
        "preview":      msg.get("bodyPreview", ""),
        "importance":   msg.get("importance", "normal"),
        "categories":   msg.get("categories", []),
    }


def _parse_message_full(msg: Dict) -> Dict:
    """Include full body content in addition to the standard fields."""
    base = _parse_message(msg)
    body = msg.get("body", {})
    base["body_type"]    = body.get("contentType", "text")
    base["body_content"] = body.get("content", "")
    return base


# ═════════════════════════════════════════════════════════════════════════════
class OutlookInboxAgent(Agent):
    """
    Agent for reading Outlook mailboxes via Microsoft Graph API.

    Stateless — credentials are resolved from environment variables at
    skill invocation time so the agent can be safely instantiated once
    and shared across requests.
    """

    name = "OutlookInboxAgent"

    # ── Skill map ─────────────────────────────────────────────────────────────
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "fetch_inbox":         self._fetch_inbox,
            "fetch_email":         self._fetch_email,
            "search_emails":       self._search_emails,
            "list_attachments":    self._list_attachments,
            "download_attachment": self._download_attachment,
            "send_email":          self._send_email,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 1 — fetch_inbox
    # ──────────────────────────────────────────────────────────────────────────
    def _fetch_inbox(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Retrieve the latest messages from the Inbox folder.

        Optional params
        ---------------
        mailbox   : str   – override MS_GRAPH_MAILBOX env var
        top       : int   – max messages to return (default 25, max 50)
        skip      : int   – offset for pagination (default 0)
        folder    : str   – mail folder name (default "inbox")
        select    : str   – comma-separated Graph fields to return
                            (default: id,subject,from,receivedDateTime,
                             isRead,hasAttachments,bodyPreview,importance)

        Output keys
        -----------
        messages      : list[dict]   – parsed message objects
        count         : int          – number returned
        next_skip     : int | None   – pass as 'skip' to get the next page
        """
        _check_deps()

        try:
            cfg     = _get_config()
            mailbox = params.get("mailbox") or cfg["mailbox"]
            top     = min(int(params.get("top", 25)), 50)
            skip    = int(params.get("skip", 0))
            folder  = params.get("folder", "inbox")
            select  = params.get(
                "select",
                "id,subject,from,receivedDateTime,isRead,hasAttachments,bodyPreview,importance,categories",
            )

            token = _acquire_token(cfg)
            url   = f"{_GRAPH_BASE}/users/{mailbox}/mailFolders/{folder}/messages"

            data = _graph_get(token, url, params={
                "$top":     top,
                "$skip":    skip,
                "$select":  select,
                "$orderby": "receivedDateTime desc",
            })

            messages  = [_parse_message(m) for m in data.get("value", [])]
            has_more  = "@odata.nextLink" in data
            next_skip = skip + top if has_more else None

            return AgentResponse(
                AgentStatus.SUCCESS,
                output={
                    "messages":  messages,
                    "count":     len(messages),
                    "next_skip": next_skip,
                },
                metadata={
                    "mailbox": mailbox,
                    "folder":  folder,
                    "top":     top,
                    "skip":    skip,
                },
            )

        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 2 — fetch_email
    # ──────────────────────────────────────────────────────────────────────────
    def _fetch_email(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Retrieve a single message by its Graph message ID (including full body).

        Required params
        ---------------
        message_id : str   – the Graph message ID

        Optional params
        ---------------
        mailbox    : str   – override MS_GRAPH_MAILBOX env var

        Output keys
        -----------
        message : dict   – full message with body_content
        """
        _check_deps()

        message_id = params.get("message_id", "").strip()
        if not message_id:
            return AgentResponse(AgentStatus.FAILED, error="'message_id' is required")

        try:
            cfg     = _get_config()
            mailbox = params.get("mailbox") or cfg["mailbox"]
            token   = _acquire_token(cfg)
            url     = f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}"

            data    = _graph_get(token, url, params={
                "$select": (
                    "id,subject,from,receivedDateTime,isRead,hasAttachments,"
                    "bodyPreview,importance,categories,body"
                )
            })

            return AgentResponse(
                AgentStatus.SUCCESS,
                output={"message": _parse_message_full(data)},
                metadata={"mailbox": mailbox, "message_id": message_id},
            )

        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 3 — search_emails
    # ──────────────────────────────────────────────────────────────────────────
    def _search_emails(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Search messages using OData $filter or $search expressions.

        Optional params (at least one filter should be supplied)
        ---------------------------------------------------------
        mailbox        : str   – override MS_GRAPH_MAILBOX env var
        subject        : str   – keyword that must appear in the subject
        sender_email   : str   – filter by exact sender email address
        unread_only    : bool  – if True, return only unread messages
        has_attachments: bool  – if True, return only messages with attachments
        received_after : str   – ISO 8601 date string, e.g. "2025-01-01"
        received_before: str   – ISO 8601 date string
        top            : int   – max results (default 25, max 50)
        folder         : str   – folder to search in (default "inbox")

        Output keys
        -----------
        messages : list[dict]
        count    : int
        """
        _check_deps()

        try:
            cfg     = _get_config()
            mailbox = params.get("mailbox") or cfg["mailbox"]
            top     = min(int(params.get("top", 25)), 50)
            folder  = params.get("folder", "inbox")
            token   = _acquire_token(cfg)

            # Build $filter clauses
            filters: List[str] = []

            subject = params.get("subject", "").strip()
            if subject:
                safe = subject.replace("'", "''")
                filters.append(f"contains(subject, '{safe}')")

            sender_email = params.get("sender_email", "").strip()
            if sender_email:
                safe = sender_email.replace("'", "''")
                filters.append(f"from/emailAddress/address eq '{safe}'")

            if params.get("unread_only"):
                filters.append("isRead eq false")

            if params.get("has_attachments"):
                filters.append("hasAttachments eq true")

            received_after = params.get("received_after", "").strip()
            if received_after:
                filters.append(f"receivedDateTime ge {received_after}T00:00:00Z")

            received_before = params.get("received_before", "").strip()
            if received_before:
                filters.append(f"receivedDateTime le {received_before}T23:59:59Z")

            url        = f"{_GRAPH_BASE}/users/{mailbox}/mailFolders/{folder}/messages"
            query: Dict[str, Any] = {
                "$top":    top,
                "$select": "id,subject,from,receivedDateTime,isRead,hasAttachments,bodyPreview,importance,categories",
            }
            if filters:
                query["$filter"] = " and ".join(filters)
            # Graph API rejects $orderby when from/emailAddress filter is present
            if not sender_email:
                query["$orderby"] = "receivedDateTime desc"

            data     = _graph_get(token, url, params=query)
            messages = [_parse_message(m) for m in data.get("value", [])]

            return AgentResponse(
                AgentStatus.SUCCESS,
                output={"messages": messages, "count": len(messages)},
                metadata={
                    "mailbox":  mailbox,
                    "folder":   folder,
                    "filters":  filters,
                },
            )

        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 4 — list_attachments
    # ──────────────────────────────────────────────────────────────────────────
    def _list_attachments(self, params: Dict[str, Any]) -> AgentResponse:
        """
        List attachment metadata for a given message.

        Required params
        ---------------
        message_id : str   – Graph message ID

        Optional params
        ---------------
        mailbox    : str   – override MS_GRAPH_MAILBOX env var

        Output keys
        -----------
        attachments : list[dict]   – [{name, size_kb, content_type, is_inline, id}]
        count       : int
        """
        _check_deps()

        message_id = params.get("message_id", "").strip()
        if not message_id:
            return AgentResponse(AgentStatus.FAILED, error="'message_id' is required")

        try:
            cfg     = _get_config()
            mailbox = params.get("mailbox") or cfg["mailbox"]
            token   = _acquire_token(cfg)
            url     = f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}/attachments"

            data = _graph_get(token, url, params={
                "$select": "id,name,size,contentType,isInline"
            })

            attachments = [
                {
                    "id":           a.get("id", ""),
                    "name":         a.get("name", ""),
                    "size_kb":      round(a.get("size", 0) / 1024, 1),
                    "content_type": a.get("contentType", ""),
                    "is_inline":    a.get("isInline", False),
                }
                for a in data.get("value", [])
                if not a.get("isInline", False)   # skip embedded images
            ]

            return AgentResponse(
                AgentStatus.SUCCESS,
                output={"attachments": attachments, "count": len(attachments)},
                metadata={"mailbox": mailbox, "message_id": message_id},
            )

        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 5 — download_attachment
    # ──────────────────────────────────────────────────────────────────────────
    def _download_attachment(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Download a single attachment and save it to disk.

        Required params
        ---------------
        message_id    : str   – Graph message ID
        attachment_id : str   – attachment ID (from list_attachments output)
        file_name     : str   – filename to save as

        Optional params
        ---------------
        mailbox    : str   – override MS_GRAPH_MAILBOX env var
        save_dir   : str   – directory to save into (default: current directory)

        Output keys
        -----------
        saved_path : str   – absolute path of the downloaded file
        size_kb    : float
        """
        _check_deps()

        message_id    = params.get("message_id", "").strip()
        attachment_id = params.get("attachment_id", "").strip()
        file_name     = params.get("file_name", "").strip()

        if not message_id:
            return AgentResponse(AgentStatus.FAILED, error="'message_id' is required")
        if not attachment_id:
            return AgentResponse(AgentStatus.FAILED, error="'attachment_id' is required")
        if not file_name:
            return AgentResponse(AgentStatus.FAILED, error="'file_name' is required")

        try:
            cfg      = _get_config()
            mailbox  = params.get("mailbox") or cfg["mailbox"]
            save_dir = Path(params.get("save_dir") or ".").resolve()
            save_dir.mkdir(parents=True, exist_ok=True)

            token = _acquire_token(cfg)
            url   = (
                f"{_GRAPH_BASE}/users/{mailbox}"
                f"/messages/{message_id}/attachments/{attachment_id}"
            )
            data = _graph_get(token, url)

            content_b64 = data.get("contentBytes", "")
            if not content_b64:
                return AgentResponse(AgentStatus.FAILED, error="Attachment has no content bytes")

            content   = base64.b64decode(content_b64)
            save_path = save_dir / file_name
            save_path.write_bytes(content)

            return AgentResponse(
                AgentStatus.SUCCESS,
                output={
                    "saved_path": str(save_path),
                    "size_kb":    round(len(content) / 1024, 1),
                },
                metadata={"mailbox": mailbox, "message_id": message_id, "file_name": file_name},
            )

        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 6 — send_email
    # ──────────────────────────────────────────────────────────────────────────
    def _send_email(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Send an email with an optional PDF attachment via Microsoft Graph API.
        Requires Mail.Send application permission on the Azure AD app.

        Required params
        ---------------
        to_email      : str   – recipient email address
        subject       : str   – email subject line

        Optional params
        ---------------
        mailbox       : str   – sender mailbox UPN (override MS_GRAPH_MAILBOX)
        client_name   : str   – recipient display name (used in default body)
        body_html     : str   – full HTML body; if omitted a default is built
        pdf_path      : str   – absolute path to a PDF to attach
        company_name  : str   – used in default body template
        report_period : str   – e.g. "March 2026", used in default body template

        Output keys
        -----------
        to_email : str   – address the message was sent to
        """
        _check_deps()

        to_email = (params.get("to_email") or "").strip()
        subject  = (params.get("subject")  or "").strip()
        if not to_email:
            return AgentResponse(AgentStatus.FAILED, error="'to_email' is required")
        if not subject:
            return AgentResponse(AgentStatus.FAILED, error="'subject' is required")

        client_name   = params.get("client_name",   "") or ""
        company_name  = params.get("company_name",  "WinRich Professional Services")
        report_period = params.get("report_period", "") or ""
        body_html     = params.get("body_html")
        pdf_path      = (params.get("pdf_path") or "").strip()

        if not body_html:
            salutation = f"Dear {client_name}," if client_name else "Dear Investor,"
            period_str = f"{report_period} " if report_period else ""
            body_html = (
                f"<p>{salutation}</p>"
                f"<p>Please find attached your {period_str}portfolio performance report "
                f"from <b>{company_name}</b>.</p>"
                f"<p>This report provides a comprehensive view of your mutual fund investments, "
                f"including fund-wise performance, benchmark comparison, and portfolio allocation.</p>"
                f"<p>Please do not hesitate to reach out if you have any questions.</p>"
                f"<p>Warm regards,<br><b>{company_name}</b></p>"
            )

        message: Dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
            "saveToSentItems": True,
        }

        if pdf_path and os.path.isfile(pdf_path):
            with open(pdf_path, "rb") as fh:
                content_b64 = base64.b64encode(fh.read()).decode("ascii")
            message["message"]["attachments"] = [{
                "@odata.type":  "#microsoft.graph.fileAttachment",
                "name":         os.path.basename(pdf_path),
                "contentType":  "application/pdf",
                "contentBytes": content_b64,
            }]

        try:
            cfg     = _get_config()
            mailbox = params.get("mailbox") or cfg["mailbox"]
            token   = _acquire_token(cfg)
            url     = f"{_GRAPH_BASE}/users/{mailbox}/sendMail"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            resp    = requests.post(url, json=message, headers=headers, timeout=60)
            if resp.status_code == 202:
                return AgentResponse(
                    AgentStatus.SUCCESS,
                    output={"to_email": to_email},
                    metadata={"mailbox": mailbox, "subject": subject},
                )
            return AgentResponse(
                AgentStatus.FAILED,
                error=f"Graph sendMail returned {resp.status_code}: {resp.text[:300]}",
            )
        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))
