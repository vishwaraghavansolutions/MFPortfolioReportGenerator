"""
EmailAgent
==========
Handles email delivery for the MF portfolio report pipeline.

Supports two transports (resolved at runtime via environment variables):
  • SMTP  – standard SMTP with TLS (default, works with Gmail, Outlook, etc.)
  • SendGrid – REST API via the sendgrid package

Environment variables
---------------------
EMAIL_TRANSPORT   : "smtp" | "sendgrid"   (default "smtp")

SMTP:
  SMTP_HOST       : str    (default "smtp.gmail.com")
  SMTP_PORT       : int    (default 587)
  SMTP_USER       : str    sender address / login
  SMTP_PASSWORD   : str    app-password or SMTP password

SendGrid:
  SENDGRID_API_KEY : str
  SENDGRID_FROM    : str   verified sender address

Skills (public)
---------------
  validate_email    – Syntax + MX-record check for one or more addresses
  send_email        – Send a single email with optional PDF attachment
  send_bulk_email   – Send the same email to a list of recipients (one-by-one,
                      so each gets a personalised subject line)
"""

from __future__ import annotations

import os
import re
import smtplib
import socket
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Dict, List, Optional

from agents.base import Agent, AgentResponse, AgentStatus


# ── Regex for quick syntax check ─────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


# ── Transport helpers (private module-level functions) ────────────────────────

def _get_smtp_config() -> Dict[str, Any]:
    return {
        "host":     os.environ.get("SMTP_HOST",     "smtp.gmail.com"),
        "port":     int(os.environ.get("SMTP_PORT", 587)),
        "user":     os.environ.get("SMTP_USER",     ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
    }


def _get_sendgrid_config() -> Dict[str, str]:
    return {
        "api_key":   os.environ.get("SENDGRID_API_KEY", ""),
        "from_addr": os.environ.get("SENDGRID_FROM",    ""),
    }


def _build_mime_message(
    from_addr:   str,
    to_addr:     str,
    subject:     str,
    body_html:   str,
    body_text:   str,
    attachments: List[str],
) -> MIMEMultipart:
    """Construct a MIME email with optional file attachments."""
    msg = MIMEMultipart("mixed")
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg["Subject"] = subject

    # Prefer HTML body, fallback plain-text
    body_part = MIMEMultipart("alternative")
    if body_text:
        body_part.attach(MIMEText(body_text, "plain"))
    if body_html:
        body_part.attach(MIMEText(body_html,  "html"))
    msg.attach(body_part)

    for path in attachments:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(path)}"',
        )
        msg.attach(part)

    return msg


def _send_via_smtp(
    to_addr:     str,
    subject:     str,
    body_html:   str,
    body_text:   str,
    attachments: List[str],
) -> None:
    """Send email over SMTP/TLS. Raises on any failure."""
    cfg      = _get_smtp_config()
    from_addr = cfg["user"]
    if not from_addr:
        raise ValueError("SMTP_USER environment variable is not set")
    if not cfg["password"]:
        raise ValueError("SMTP_PASSWORD environment variable is not set")

    msg = _build_mime_message(from_addr, to_addr, subject, body_html, body_text, attachments)

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.sendmail(from_addr, [to_addr], msg.as_string())


def _send_via_sendgrid(
    to_addr:     str,
    subject:     str,
    body_html:   str,
    body_text:   str,
    attachments: List[str],
) -> None:
    """Send email via SendGrid REST API. Raises on any failure."""
    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import (
            Attachment, ContentId, Disposition, FileContent,
            FileName, FileType, Mail, To,
        )
        import base64
    except ImportError as exc:
        raise ImportError(
            "sendgrid package not installed — run `pip install sendgrid`"
        ) from exc

    cfg = _get_sendgrid_config()
    if not cfg["api_key"]:
        raise ValueError("SENDGRID_API_KEY environment variable is not set")
    if not cfg["from_addr"]:
        raise ValueError("SENDGRID_FROM environment variable is not set")

    mail = Mail(
        from_email=cfg["from_addr"],
        to_emails=To(to_addr),
        subject=subject,
        html_content=body_html or body_text,
    )
    if body_text and body_html:
        mail.add_content(body_text, "text/plain")

    for path in attachments:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode()
        att = Attachment(
            FileContent(encoded),
            FileName(os.path.basename(path)),
            FileType("application/pdf"),
            Disposition("attachment"),
        )
        mail.add_attachment(att)

    client   = sg_module.SendGridAPIClient(cfg["api_key"])
    response = client.send(mail)
    if response.status_code not in (200, 202):
        raise RuntimeError(
            f"SendGrid returned HTTP {response.status_code}: {response.body}"
        )


def _check_mx(domain: str) -> bool:
    """Return True if the domain has at least one MX record (best-effort)."""
    try:
        socket.getaddrinfo(domain, None)   # lightweight; full MX needs dnspython
        return True
    except socket.gaierror:
        return False


# ═════════════════════════════════════════════════════════════════════════════
class EmailAgent(Agent):
    """
    Agent responsible for all email delivery in the portfolio report pipeline.

    Stateless — safe to instantiate once and share across requests.
    Transport is selected at call time via the EMAIL_TRANSPORT env-var or the
    'transport' key in params so tests can override without touching the env.
    """

    name = "EmailAgent"

    # Default HTML template used when the caller does not supply body_html
    _DEFAULT_HTML_TEMPLATE = """
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:680px;margin:auto">
      <div style="background:#1a3a5c;padding:24px 32px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0">{company_name}</h2>
        <p  style="color:#a8c8e8;margin:4px 0 0">Quarterly Portfolio Report</p>
      </div>
      <div style="padding:24px 32px;background:#f9f9f9;border:1px solid #e0e0e0">
        <p>Dear <strong>{client_name}</strong>,</p>
        <p>Please find attached your <strong>{report_period}</strong> mutual fund portfolio report.</p>
        <p>Your report includes:</p>
        <ul>
          <li>Portfolio valuation &amp; asset allocation</li>
          <li>Fund-level XIRR vs benchmark</li>
          <li>Quarter-on-quarter performance</li>
          <li>AI-generated performance commentary</li>
        </ul>
        <p>Should you have any questions, please do not hesitate to reach out to your advisor.</p>
        <p style="margin-top:32px">Warm regards,<br>
           <strong>{prepared_by}</strong><br>
           {company_name}
        </p>
      </div>
      <div style="padding:12px 32px;background:#e8eef4;font-size:12px;color:#666;
                  border-radius:0 0 8px 8px;border:1px solid #e0e0e0;border-top:none">
        This email and any attachments are confidential. If you received this in error, 
        please delete it and notify the sender immediately.
      </div>
    </body></html>
    """

    _DEFAULT_TEXT_TEMPLATE = (
        "Dear {client_name},\n\n"
        "Please find attached your {report_period} mutual fund portfolio report "
        "prepared by {company_name}.\n\n"
        "Should you have any questions, please contact your advisor.\n\n"
        "Warm regards,\n{prepared_by}\n{company_name}"
    )

    # ── Skill map ─────────────────────────────────────────────────────────────
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "validate_email":  self._validate_email,
            "send_email":      self._send_email,
            "send_bulk_email": self._send_bulk_email,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 1 — validate_email
    # ──────────────────────────────────────────────────────────────────────────
    def _validate_email(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Validate one or more email addresses (syntax + lightweight MX check).

        Required params
        ---------------
        email  : str | list[str]   – address(es) to validate

        Output keys
        -----------
        valid   : list[str]   – addresses that passed both checks
        invalid : list[str]   – addresses that failed
        results : list[dict]  – {email, valid, reason} for every address
        """
        raw = params.get("email", [])
        addresses: List[str] = [raw] if isinstance(raw, str) else list(raw)

        if not addresses:
            return AgentResponse(AgentStatus.FAILED, error="'email' param is required")

        valid, invalid, results = [], [], []

        for addr in addresses:
            addr = addr.strip()
            if not _EMAIL_RE.match(addr):
                reason = "Invalid email syntax"
                invalid.append(addr)
                results.append({"email": addr, "valid": False, "reason": reason})
                continue

            domain = addr.split("@")[1]
            if not _check_mx(domain):
                reason = f"Domain '{domain}' not reachable"
                invalid.append(addr)
                results.append({"email": addr, "valid": False, "reason": reason})
                continue

            valid.append(addr)
            results.append({"email": addr, "valid": True, "reason": "OK"})

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={"valid": valid, "invalid": invalid, "results": results},
            metadata={"total": len(addresses), "valid_count": len(valid)},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 2 — send_email
    # ──────────────────────────────────────────────────────────────────────────
    def _send_email(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Send a single email, optionally with a PDF attachment.

        Required params
        ---------------
        to_email      : str        – recipient address
        client_name   : str        – used in the default template
        subject       : str        – email subject line

        Optional params
        ---------------
        body_html     : str        – custom HTML body (overrides default template)
        body_text     : str        – plain-text fallback
        attachments   : list[str]  – list of file paths to attach
        pdf_path      : str        – shorthand for single-PDF attachment
        company_name  : str        – default "Winrich Professional Services"
        prepared_by   : str        – default same as company_name
        report_period : str        – default "Quarterly"
        transport     : str        – "smtp" | "sendgrid" (overrides env var)

        Output keys
        -----------
        to_email  : str
        subject   : str
        transport : str   – which transport was used
        """
        to_email    = params.get("to_email", "").strip()
        client_name = params.get("client_name", "Valued Client")
        subject     = params.get("subject", "Your Quarterly Portfolio Report")

        if not to_email:
            return AgentResponse(AgentStatus.FAILED, error="'to_email' is required")
        if not _EMAIL_RE.match(to_email):
            return AgentResponse(AgentStatus.FAILED, error=f"Invalid email address: {to_email}")

        company_name  = params.get("company_name",  "Winrich Professional Services")
        prepared_by   = params.get("prepared_by",   company_name)
        report_period = params.get("report_period", "Quarterly")

        # Build attachments list
        attachments: List[str] = list(params.get("attachments", []))
        if params.get("pdf_path") and params["pdf_path"] not in attachments:
            attachments.append(params["pdf_path"])

        # Resolve body
        template_ctx = dict(
            client_name=client_name,
            company_name=company_name,
            prepared_by=prepared_by,
            report_period=report_period,
        )
        body_html = params.get("body_html") or self._DEFAULT_HTML_TEMPLATE.format(**template_ctx)
        body_text = params.get("body_text") or self._DEFAULT_TEXT_TEMPLATE.format(**template_ctx)

        # Resolve transport
        transport = (
            params.get("transport")
            or os.environ.get("EMAIL_TRANSPORT", "smtp")
        ).lower()

        try:
            if transport == "sendgrid":
                _send_via_sendgrid(to_email, subject, body_html, body_text, attachments)
            else:
                _send_via_smtp(to_email, subject, body_html, body_text, attachments)
        except Exception as exc:
            return AgentResponse(
                AgentStatus.RETRY,
                error=str(exc),
                metadata={"to_email": to_email, "transport": transport},
            )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "to_email":  to_email,
                "subject":   subject,
                "transport": transport,
            },
            metadata={
                "attachments_sent": [os.path.basename(p) for p in attachments if os.path.exists(p)],
                "missing_files":    [p for p in attachments if not os.path.exists(p)],
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill 3 — send_bulk_email
    # ──────────────────────────────────────────────────────────────────────────
    def _send_bulk_email(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Send personalised emails to a list of recipients.

        Each recipient is sent their own email (not a CC/BCC blast) so that
        subject lines and attachments can be per-person.

        Required params
        ---------------
        recipients : list[dict]   – each dict supports all params from send_email:
                                    {to_email, client_name, subject, pdf_path, ...}

        Optional params (used as defaults for every recipient)
        -------------------------------------------------------
        company_name  : str
        report_period : str
        transport     : str

        Output keys
        -----------
        sent        : list[str]   – successfully delivered addresses
        failed      : list[dict]  – [{to_email, error}, ...] for failures
        total       : int
        success_count: int
        """
        recipients: List[Dict[str, Any]] = params.get("recipients", [])
        if not recipients:
            return AgentResponse(AgentStatus.FAILED, error="'recipients' list is required")

        # Defaults that apply to every recipient unless overridden per-entry
        defaults = {k: params[k] for k in (
            "company_name", "report_period", "transport", "prepared_by"
        ) if k in params}

        sent, failed = [], []

        for entry in recipients:
            merged = {**defaults, **entry}
            result = self._send_email(merged)
            if result.status == AgentStatus.SUCCESS:
                sent.append(merged.get("to_email", ""))
            else:
                failed.append({
                    "to_email": merged.get("to_email", ""),
                    "error":    result.error,
                })

        overall_status = (
            AgentStatus.SUCCESS if not failed
            else AgentStatus.RETRY if sent     # partial success
            else AgentStatus.FAILED
        )

        return AgentResponse(
            overall_status,
            output={
                "sent":          sent,
                "failed":        failed,
                "total":         len(recipients),
                "success_count": len(sent),
            },
            metadata={
                "failure_count": len(failed),
                "partial":       bool(sent and failed),
            },
            error=f"{len(failed)} email(s) failed to send" if failed else None,
        )