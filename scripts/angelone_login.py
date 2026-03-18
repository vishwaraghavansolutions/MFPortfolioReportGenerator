"""
scripts/angelone_login.py
─────────────────────────
Logs into Angel One.

Two modes (set ANGELONE_MANUAL=1 to choose):

  Manual (default, ANGELONE_MANUAL=1)
  ────────────────────────────────────
  Opens a browser window, navigates to the Angel One login page, and waits
  for YOU to log in.  Once the URL leaves /auth the code takes over,
  harvests cookies, saves the session, and closes the tab.
  This mode always succeeds against reCAPTCHA because a real human is logging in.

  Automated (ANGELONE_MANUAL=0)
  ──────────────────────────────
  Tries to fill in the login form automatically using Playwright.
  Falls back to prompting you for the OTP on the console.

Environment variables
─────────────────────
    ANGELONE_CLIENT_ID    Angel One client ID          default: BNGWWW2
    ANGELONE_PASSWORD     Account password             (required)
    ANGELONE_MANUAL       1 = manual browser login     default: 1
    ANGELONE_LOGIN_TIMEOUT Seconds to wait for manual  default: 300
    ANGELONE_OTP_TIMEOUT  Seconds to wait for OTP      default: 120

    OWA_USERNAME          OWA email (automated mode)
    OWA_PASSWORD          OWA password (automated mode)
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

from agents.angelone_agent import AngelOneAgent
from agents.base import AgentStatus

# ── Credentials ────────────────────────────────────────────────────────────────
CLIENT_ID     = os.environ.get("ANGELONE_CLIENT_ID",    "BNGWWW2")
AO_PASSWORD   = os.environ.get("ANGELONE_PASSWORD",     "Sridharan@123")
MANUAL        = os.environ.get("ANGELONE_MANUAL",       "1") != "0"
LOGIN_TIMEOUT = int(os.environ.get("ANGELONE_LOGIN_TIMEOUT", 300))
OTP_TIMEOUT   = int(os.environ.get("ANGELONE_OTP_TIMEOUT",   120))

OWA_USERNAME  = os.environ.get("OWA_USERNAME", "knsridharan@w2pinvest.com")
OWA_PASSWORD  = os.environ.get("OWA_PASSWORD", "")

# Prompt for password if not set (automated mode only needs it)
if not MANUAL and not AO_PASSWORD:
    AO_PASSWORD = input("Enter AngelOne account password: ").strip()

# ── Run ────────────────────────────────────────────────────────────────────────

mode_label = "MANUAL (human login)" if MANUAL else "AUTOMATED"
print(f"\n[angelone_login] Mode: {mode_label}  client_id={CLIENT_ID}\n")

agent = AngelOneAgent()
resp  = agent.run("login", {
    "client_id":     CLIENT_ID,
    "password":      AO_PASSWORD,
    "manual_login":  MANUAL,
    "login_timeout": LOGIN_TIMEOUT,
    # automated-mode params (ignored when manual_login=True)
    "use_email_otp": False,
    "owa_username":  OWA_USERNAME,
    "owa_password":  OWA_PASSWORD,
    "otp_timeout":   OTP_TIMEOUT,
})

# ── Results ────────────────────────────────────────────────────────────────────

if resp and resp.status == AgentStatus.SUCCESS:
    print("\n[OK] Login successful!")
    print(f"  Final URL : {resp.output['url']}")
    print(f"  Cookies   : {len(resp.output['cookies'])} stored")

    auth_keys = {"jwtToken", "authToken", "token", "refreshToken", "Authorization"}
    for c in resp.output["cookies"]:
        if any(k.lower() in c["name"].lower() for k in auth_keys):
            print(f"    [auth cookie] {c['name']} = {str(c['value'])[:60]}")

    ls = resp.output.get("local_storage", {})
    for k, v in ls.items():
        if "token" in k.lower() or "auth" in k.lower():
            print(f"    [localStorage] {k} = {str(v)[:60]}")
else:
    err = resp.error if resp else "No response"
    print(f"\n[FAILED] Login failed: {err}", file=sys.stderr)
    sys.exit(1)
