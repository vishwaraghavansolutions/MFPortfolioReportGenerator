"""
agents/angelone_agent.py  
────────────────────────
Automates login to https://nxt.angelone.in/auth using Playwright.

Login flow (Angel One NXT):
  1. Navigate to /auth
  2. Enter Client ID  →  click Next
  3a. If device NOT trusted: Enter OTP  →  submit  (OTP sent to registered email/mobile)
  3b. If device trusted within 24 h: OTP step is skipped
  4. Enter Password   →  click Login

OTP sources (checked in order)
───────────────────────────────
  1. "otp" param supplied directly
  2. Relay file written by EmailOTPAgent  (use_email_otp=True, the default)
  3. Console prompt fallback

Usage
─────
    from agents.angelone_agent import AngelOneAgent

    agent = AngelOneAgent()
    resp  = agent.run("login", {
        "client_id":     "BNGWWW2",
        "password":      "your_password",
        # use_email_otp=True (default) – reads OTP from relay file written
        # by EmailOTPAgent running in a background thread.
        # Set use_email_otp=False to fall back to console prompt.
    })
    if resp.status.value == "success":
        cookies = resp.output["cookies"]   # list of cookie dicts
        ...

Headless mode
─────────────
    Set env var  ANGELONE_HEADLESS=1   to run the browser invisibly.
    Default is headed (visible) so you can watch / debug the login.

OTP
───
    Pass "otp" in params if you have it in advance.
    Otherwise the agent will call input() to read it from the console.
    You may also pass "otp_timeout" (seconds, default 120) to control
    how long the agent waits on the OTP screen before giving up.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from agents.base import Agent, AgentResponse, AgentStatus
from agents._otp_relay import read_fresh_otp, clear_otp

logger = logging.getLogger(__name__)

# ── Selectors ──────────────────────────────────────────────────────────────────
# These are best-effort CSS/text selectors for the Angel One NXT login UI.
# Update if the page structure changes.

_URL_AUTH       = "https://nxt.angelone.in/auth"

# Step 1 – Client ID input
_SEL_CLIENT_ID  = "input[placeholder*='Client'], input[name*='client'], input[id*='client'], input[type='text']:first-of-type"
# 'Next' / 'Continue' button after Client ID
_SEL_NEXT_BTN   = "button:has-text('Next'), button:has-text('Continue'), button:has-text('NEXT')"

# Step 2 – Password input
_SEL_PASSWORD   = "input[type='password']"
# 'Login' button
_SEL_LOGIN_BTN  = "button:has-text('Login'), button:has-text('LOGIN'), button:has-text('Sign In')"

# Step 2.5 – Captcha (appears on the password screen)
_SEL_CAPTCHA_IMG   = "img[src*='captcha' i], img[alt*='captcha' i], img[id*='captcha' i], .captcha-image, #captchaImage"
_SEL_CAPTCHA_INPUT = "input[placeholder*='captcha' i], input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='Enter captcha' i]"

# "Invalid Captcha" error — reCAPTCHA Enterprise rejection toast/dialog after clicking Next
_SEL_CAPTCHA_ERR     = ":text-matches('(?i)invalid.{0,5}captcha'), :text-matches('(?i)captcha.{0,10}invalid'), :text-matches('(?i)captcha.{0,10}failed')"
_SEL_ERR_DISMISS_BTN = "button:has-text('OK'), button:has-text('Ok'), button:has-text('Close'), button:has-text('Got it'), button:has-text('Dismiss')"
_MAX_CAPTCHA_RETRIES = 8   # retry Next up to this many times before giving up

# Step 3 – OTP inputs (Angel One uses individual digit boxes or a single field)
_SEL_OTP_FIELD  = "input[placeholder*='OTP'], input[name*='otp'], input[id*='otp'], input[autocomplete='one-time-code']"
_SEL_OTP_DIGIT  = "input.otp-input, .otp-container input"   # individual digit boxes
_SEL_OTP_SUBMIT = "button:has-text('Submit'), button:has-text('Verify'), button:has-text('Confirm'), button:has-text('SUBMIT')"

# Post-login indicator – URL changes away from /auth
_URL_POST_LOGIN = "nxt.angelone.in"   # just the domain part; check path != /auth


# ── Agent ──────────────────────────────────────────────────────────────────────

class AngelOneAgent(Agent):
    """Browser-based login agent for Angel One NXT."""

    name = "AngelOneAgent"

    def __init__(self) -> None:
        self.skills: Dict[str, Callable] = {
            "login": self._skill_login,
        }

    # ── public skill ──────────────────────────────────────────────────────────

    def _skill_login(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Params
        ──────
        client_id     str   Angel One Client ID  (e.g. "BNGWWW2")
        password      str   Account password
        manual_login  bool  Open browser and wait for a human to log in  [False]
        login_timeout int   Seconds to wait for human login               [300]
        otp           str   (optional) OTP if already known
        headless      bool  Run browser invisibly                         [False]
        otp_timeout   int   Seconds to wait for OTP entry                 [120]
        use_email_otp bool  Poll OWA tab for OTP automatically            [True]
        force_login   bool  Skip session cache                            [False]
        owa_username  str   OWA email   (or OWA_USERNAME env var)
        owa_password  str   OWA password (or OWA_PASSWORD env var)
        """
        client_id     = params.get("client_id",     "").strip()
        password      = params.get("password",      "").strip()
        manual_login  = params.get("manual_login",  False)
        login_timeout = int(params.get("login_timeout", 300))
        otp_preset    = params.get("otp",            "").strip()
        otp_timeout   = int(params.get("otp_timeout",   120))
        use_email_otp = params.get("use_email_otp", True)
        force_login   = params.get("force_login",   False)
        owa_username  = params.get("owa_username", os.environ.get("OWA_USERNAME", ""))
        owa_password  = params.get("owa_password", os.environ.get("OWA_PASSWORD", ""))

        env_headless = os.environ.get("ANGELONE_HEADLESS", "0")
        headless     = params.get("headless", env_headless == "1")
        if manual_login:
            headless = False   # must be visible for human interaction

        if not client_id or not password:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error="'client_id' and 'password' are required params",
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error="playwright not installed. Run: pip install playwright && playwright install chromium",
            )

        # ── Try saved session first ────────────────────────────────────────────
        if not force_login:
            session = self._load_session(client_id)
            if session:
                result = self._try_resume_session(session, headless, client_id)
                if result:
                    logger.info("[AngelOneAgent] Resumed saved session – skipped login")
                    return AgentResponse(
                        status   = AgentStatus.SUCCESS,
                        output   = result,
                        metadata = {"client_id": client_id, "session": "resumed"},
                    )
                logger.info("[AngelOneAgent] Saved session expired – doing full login")

        logger.info("[AngelOneAgent] Starting login for client_id=%r  headless=%s  manual=%s",
                    client_id, headless, manual_login)

        from pathlib import Path
        profile_dir = str(Path.home() / ".angelone" / f"browser_profile_{client_id}")

        with sync_playwright() as pw:
            # Prefer the user's real installed Chrome — it passes reCAPTCHA Enterprise
            # much better than Playwright's bundled Chromium (real GPU, real fingerprint).
            # Fall back to Chromium if Chrome is not installed.
            _launch_kwargs = dict(
                user_data_dir     = profile_dir,
                headless          = headless,
                args              = [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--disable-notifications",
                ],
                viewport          = {"width": 1280, "height": 800},
                locale            = "en-IN",
                timezone_id       = "Asia/Kolkata",
                extra_http_headers= {"Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8"},
            )
            try:
                context = pw.chromium.launch_persistent_context(
                    channel = "msedge",   # real Edge install (pre-installed on Windows 11)
                    **_launch_kwargs,
                )
                logger.info("[AngelOneAgent] Launched real Edge (channel=msedge)")
            except Exception as _edge_err:
                logger.warning(
                    "[AngelOneAgent] Real Edge not found (%s) – falling back to Chromium", _edge_err
                )
                context = pw.chromium.launch_persistent_context(
                    user_agent = (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
                    ),
                    **_launch_kwargs,
                )

            # Comprehensive stealth patches — applied to every page/frame in this context
            context.add_init_script("""
(function () {
    // 1. Hide webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Realistic plugins (Chrome PDF Plugin, PDF Viewer, Native Client)
    function makePlugin(name, desc, filename, mimeTypes) {
        const plugin = Object.create(Plugin.prototype);
        Object.defineProperty(plugin, 'name',        { value: name });
        Object.defineProperty(plugin, 'description', { value: desc });
        Object.defineProperty(plugin, 'filename',    { value: filename });
        Object.defineProperty(plugin, 'length',      { value: mimeTypes.length });
        mimeTypes.forEach(function(mt, i) {
            const m = Object.create(MimeType.prototype);
            Object.defineProperty(m, 'type',        { value: mt.type });
            Object.defineProperty(m, 'description', { value: mt.description || '' });
            Object.defineProperty(m, 'suffixes',    { value: mt.suffixes || '' });
            Object.defineProperty(m, 'enabledPlugin',{ value: plugin });
            plugin[i] = m;
            plugin[mt.type] = m;
        });
        return plugin;
    }
    const p0 = makePlugin('Chrome PDF Plugin',  'Portable Document Format', 'internal-pdf-viewer',
                          [{type:'application/x-google-chrome-pdf', description:'Portable Document Format', suffixes:'pdf'}]);
    const p1 = makePlugin('Chrome PDF Viewer',  '', 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                          [{type:'application/pdf', description:'', suffixes:'pdf'}]);
    const p2 = makePlugin('Native Client', '', 'internal-nacl-plugin',
                          [{type:'application/x-nacl', description:'Native Client Executable', suffixes:''},
                           {type:'application/x-pnacl',description:'Portable Native Client Executable',suffixes:''}]);
    const fakePlugins = [p0, p1, p2];
    fakePlugins.namedItem = function(n) { return this.find(p => p.name === n) || null; };
    fakePlugins.item      = function(i) { return this[i]; };
    fakePlugins.refresh   = function() {};
    Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins });

    // 3. Languages
    Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-GB', 'en'] });

    // 4. Hardware hints — make it look like a mid-range laptop
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });

    // 5. Complete window.chrome
    const isChrome = /Chrome/.test(navigator.userAgent);
    if (isChrome) {
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: { DISABLED:'disabled', INSTALLED:'installed', NOT_INSTALLED:'not_installed' },
                RunningState: { CANNOT_RUN:'cannot_run', READY_TO_RUN:'ready_to_run', RUNNING:'running' }
            },
            csi:        function() { return {}; },
            loadTimes: function() {
                return {
                    commitLoadTime: Date.now()/1000 - Math.random()*2,
                    connectionInfo: 'h2',
                    finishDocumentLoadTime: Date.now()/1000 - Math.random(),
                    finishLoadTime: Date.now()/1000,
                    firstPaintAfterLoadTime: 0,
                    firstPaintTime: Date.now()/1000 - Math.random()*0.5,
                    navigationType: 'Other',
                    npnNegotiatedProtocol: 'h2',
                    requestTime: Date.now()/1000 - Math.random()*3,
                    startLoadTime: Date.now()/1000 - Math.random()*3,
                    wasAlternateProtocolAvailable: false,
                    wasFetchedViaSpdy: true,
                    wasNpnNegotiated: true
                };
            },
            runtime: {
                PlatformOs: { MAC:'mac', WIN:'win', ANDROID:'android', CROS:'cros', LINUX:'linux', OPENBSD:'openbsd' },
                PlatformArch: { ARM:'arm', X86_32:'x86-32', X86_64:'x86-64' },
                PlatformNaclArch: { ARM:'arm', X86_32:'x86-32', X86_64:'x86-64' },
                RequestUpdateCheckStatus: { THROTTLED:'throttled', NO_UPDATE:'no_update', UPDATE_AVAILABLE:'update_available' },
                OnInstalledReason: { INSTALL:'install', UPDATE:'update', CHROME_UPDATE:'chrome_update', SHARED_MODULE_UPDATE:'shared_module_update' },
                OnRestartRequiredReason: { APP_UPDATE:'app_update', OS_UPDATE:'os_update', PERIODIC:'periodic' }
            }
        };
    }

    // 6. Permissions.query — reCAPTCHA checks notification permission
    const origQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = function(params) {
        if (params && params.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission, onchange: null });
        }
        return origQuery(params);
    };

    // 7. Prevent iframe contentWindow detection leaking webdriver
    const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
            const win = origContentWindow.get.call(this);
            if (win) {
                try { Object.defineProperty(win.navigator, 'webdriver', { get: () => undefined }); } catch(e) {}
            }
            return win;
        }
    });
})();
""")

            import random as _rnd

            # ── Tab 1: OWA ────────────────────────────────────────────────────
            # Open OWA first so the user/agent can read the OTP email.
            # The persistent profile means OWA is usually already logged in.
            owa_page = context.pages[0] if context.pages else context.new_page()
            logger.info("[AngelOneAgent] Tab 1: opening OWA …")
            try:
                self._open_owa(owa_page, owa_username, owa_password)
                logger.info("[AngelOneAgent] OWA tab ready")
            except Exception as exc:
                logger.warning("[AngelOneAgent] OWA tab failed (non-fatal): %s", exc)
                owa_page = None

            # ── Tab 2: Angel One ───────────────────────────────────────────────
            logger.info("[AngelOneAgent] Tab 2: opening Angel One …")
            angel_page = context.new_page()
            angel_page.bring_to_front()

            if manual_login:
                # ── Manual login path ──────────────────────────────────────────
                try:
                    angel_page.goto(_URL_AUTH, wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                # OWA is already open as Tab 1
                angel_page.bring_to_front()

                print(
                    f"\n{'='*60}\n"
                    f"  Tab 1 → Angel One login:  {_URL_AUTH}\n"
                    f"  Tab 2 → OWA inbox:        https://outlook.office.com/mail/\n"
                    f"\n"
                    f"  Log in to Angel One (OTP will arrive in the OWA tab).\n"
                    f"  This browser will close automatically after login.\n"
                    f"  You have {login_timeout}s.\n"
                    f"{'='*60}\n",
                    flush=True,
                )
                logger.info("[AngelOneAgent] Waiting up to %ds for manual login …", login_timeout)

                try:
                    from playwright.sync_api import TimeoutError as PWTimeout
                    angel_page.wait_for_url(
                        lambda url: "/auth" not in url,
                        timeout = login_timeout * 1_000,
                    )
                    logger.info("[AngelOneAgent] Manual login detected. URL: %s", angel_page.url)
                except PWTimeout:
                    context.close()
                    return AgentResponse(
                        status = AgentStatus.FAILED,
                        error  = f"Manual login not completed within {login_timeout}s",
                    )

                # ── Post-login: refresh then navigate to Reports ───────────────
                captured = self._navigate_to_reports(angel_page)
                result = {"url": angel_page.url, "captured": captured}

            else:
                # ── Automated login path ───────────────────────────────────────
                # OWA is already open as Tab 1 (opened above)
                angel_page.bring_to_front()

                try:
                    result = self._do_login(
                        page          = angel_page,
                        client_id     = client_id,
                        password      = password,
                        otp_preset    = otp_preset,
                        otp_timeout   = otp_timeout,
                        use_email_otp = use_email_otp,
                        owa_page      = owa_page,
                    )
                except Exception as exc:
                    logger.exception("[AngelOneAgent] Login failed: %s", exc)
                    context.close()
                    return AgentResponse(
                        status  = AgentStatus.FAILED,
                        error   = str(exc),
                        metadata= {"url": angel_page.url},
                    )

            # ── Harvest session ────────────────────────────────────────────────
            cookies       = context.cookies()
            local_storage = angel_page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")

            # ── Logout and close Angel tab ─────────────────────────────────────
            self._angel_logout(angel_page)
            angel_page.close()
            logger.info("[AngelOneAgent] Angel One tab closed. OWA tab remains open.")

            context.close()   # persists profile

        output = {
            "cookies":       cookies,
            "local_storage": local_storage,
            "url":           result["url"],
        }
        self._save_session(client_id, output)
        logger.info("[AngelOneAgent] Login succeeded. Session saved. Final URL: %s", result["url"])
        return AgentResponse(
            status   = AgentStatus.SUCCESS,
            output   = output,
            metadata = {"client_id": client_id, "session": "new"},
        )

    # ── internal login flow ───────────────────────────────────────────────────

    def _do_login(
        self,
        page,
        client_id:     str,
        password:      str,
        otp_preset:    str,
        otp_timeout:   int,
        use_email_otp: bool = True,
        owa_page       = None,
    ) -> Dict[str, str]:
        from playwright.sync_api import TimeoutError as PWTimeout

        import random as _rnd

        # ── Step 0: Navigate ──────────────────────────────────────────────────
        logger.info("[AngelOneAgent] Navigating to %s", _URL_AUTH)
        page.goto(_URL_AUTH, wait_until="networkidle", timeout=30_000)

        self._idle_mouse(page)
        time.sleep(_rnd.uniform(1.5, 3.0))
        page.mouse.wheel(0, _rnd.randint(50, 150))
        time.sleep(_rnd.uniform(0.3, 0.7))
        page.mouse.wheel(0, -_rnd.randint(50, 150))

        # ── Step 1: Client ID ─────────────────────────────────────────────────
        logger.info("[AngelOneAgent] Entering client ID …")
        for sel in [s.strip() for s in _SEL_CLIENT_ID.split(",")]:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=3_000)
                box = loc.bounding_box()
                if box:
                    cx = box["x"] + box["width"]  / 2
                    cy = box["y"] + box["height"] / 2
                    self._idle_mouse(page)
                    self._move_to(page, loc)
                    time.sleep(_rnd.uniform(0.15, 0.35))
                    page.mouse.click(cx, cy)
                    time.sleep(_rnd.uniform(0.1, 0.25))
                    logger.debug("[AngelOneAgent] Clicked client-ID field at (%.0f, %.0f)", cx, cy)
                else:
                    loc.click()
                break
            except Exception:
                continue
        self._human_type(page, client_id)

        self._idle_mouse(page)
        time.sleep(_rnd.uniform(0.4, 0.9))

        # Diagnose captcha mechanism before clicking Next (helps debug invisible-CAPTCHA issues)
        self._diagnose_captcha(page)

        # ── Step 1b: Click Next — retry if reCAPTCHA returns "Invalid Captcha" ─
        # Angel One uses reCAPTCHA Enterprise which probabilistically blocks bots.
        # When blocked it shows a toast/dialog saying "Invalid Captcha".
        # Strategy: dismiss the error and click Continue again (up to _MAX_CAPTCHA_RETRIES).
        for _attempt in range(_MAX_CAPTCHA_RETRIES):
            try:
                next_btn = page.locator(_SEL_NEXT_BTN).first
                next_btn.wait_for(state="visible", timeout=5_000)
                self._move_to(page, next_btn)
                time.sleep(_rnd.uniform(0.4, 0.9))
                next_btn.click()
                logger.info("[AngelOneAgent] Clicked Next/Continue (attempt %d)", _attempt + 1)
            except PWTimeout:
                logger.warning("[AngelOneAgent] 'Next' button not found – continuing")
                break

            # Wait briefly to see if a captcha error toast appears
            try:
                page.wait_for_selector(_SEL_CAPTCHA_ERR, state="visible", timeout=3_000)
                logger.warning(
                    "[AngelOneAgent] 'Invalid Captcha' on attempt %d — dismissing and retrying …",
                    _attempt + 1,
                )
                # Dismiss the error (click OK / Close button, or press Escape)
                try:
                    dismiss = page.locator(_SEL_ERR_DISMISS_BTN).first
                    dismiss.wait_for(state="visible", timeout=2_000)
                    dismiss.click()
                    logger.debug("[AngelOneAgent] Dismissed captcha error dialog")
                except PWTimeout:
                    page.keyboard.press("Escape")

                # Brief pause before retrying — slightly longer each round
                time.sleep(_rnd.uniform(1.5, 2.5) + _attempt * 0.5)

            except PWTimeout:
                # No captcha error appeared — Next was accepted, proceed
                logger.debug("[AngelOneAgent] No captcha error — Next accepted")
                break
        else:
            raise RuntimeError(
                f"reCAPTCHA blocked every attempt after {_MAX_CAPTCHA_RETRIES} retries. "
                "Try running the browser with a fresh persistent profile."
            )

        # ── Step 2: OTP or Password — whichever appears first ─────────────────
        # Actual Angel One flow: User ID → OTP → Password
        # BUT after first login, device is trusted for 24 hours → OTP skipped
        logger.info("[AngelOneAgent] Waiting to see OTP or Password field …")
        otp_appeared      = False
        password_appeared = False
        deadline_detect   = time.time() + 25
        while time.time() < deadline_detect:
            try:
                page.wait_for_selector(
                    f"{_SEL_OTP_FIELD}, {_SEL_OTP_DIGIT}",
                    state="visible", timeout=1_500,
                )
                otp_appeared = True
                logger.info("[AngelOneAgent] OTP screen detected")
                break
            except PWTimeout:
                pass
            try:
                page.wait_for_selector(_SEL_PASSWORD, state="visible", timeout=1_500)
                password_appeared = True
                logger.info("[AngelOneAgent] Password screen detected — device trusted, OTP skipped")
                break
            except PWTimeout:
                pass
            if "/auth" not in page.url:
                logger.info("[AngelOneAgent] Already redirected post-login")
                return {"url": page.url}

        if not otp_appeared and not password_appeared:
            # Neither field appeared — captcha probably blocked the page.
            # Save a screenshot so we can see what's on the screen.
            from pathlib import Path as _P
            _ss = str(_P("data") / "debug_login_step2.png")
            try:
                page.screenshot(path=_ss)
                logger.error("[AngelOneAgent] No OTP/Password field after 25s. Screenshot: %s  URL: %s", _ss, page.url)
            except Exception:
                logger.error("[AngelOneAgent] No OTP/Password field after 25s. URL: %s", page.url)
            raise RuntimeError(
                f"Login blocked — neither OTP nor Password field appeared after clicking Next. "
                f"Current URL: {page.url}  (check {_ss} for screenshot)"
            )

        if otp_appeared:
            # ── Resolve OTP from three sources (in priority order) ────────────
            # 1. Directly supplied via param
            # 2. Relay file written by EmailOTPAgent (use_email_otp=True)
            # 3. Console prompt fallback
            if otp_preset:
                otp_value = otp_preset
                logger.info("[AngelOneAgent] Using pre-supplied OTP")

            elif use_email_otp and owa_page is not None:
                logger.info(
                    "[AngelOneAgent] Scanning OWA tab for OTP (timeout=%ds, poll every 3s) …",
                    otp_timeout,
                )
                otp_value = None
                deadline  = time.time() + otp_timeout
                while time.time() < deadline:
                    otp_value = self._get_otp_from_owa_tab(owa_page)
                    if otp_value:
                        logger.info("[AngelOneAgent] ✓ OTP found in OWA tab: %s", otp_value)
                        break
                    logger.debug("[AngelOneAgent] Waiting for OTP … (%.0fs remaining)", deadline - time.time())
                    time.sleep(3)
                    # Refresh OWA tab to pick up new mail
                    try:
                        owa_page.reload(wait_until="domcontentloaded", timeout=10_000)
                    except Exception:
                        pass

                if not otp_value:
                    raise TimeoutError(f"No OTP found in OWA tab within {otp_timeout}s")

            else:
                # Console fallback
                print(
                    f"\n[AngelOneAgent] OTP sent to registered email/mobile.\n"
                    f"Enter OTP (you have {otp_timeout}s): ",
                    end="", flush=True,
                )
                import threading
                otp_value = None
                _event    = threading.Event()

                def _read_otp():
                    nonlocal otp_value
                    try:
                        otp_value = input().strip()
                    except Exception:
                        pass
                    _event.set()

                t = threading.Thread(target=_read_otp, daemon=True)
                t.start()
                _event.wait(timeout=otp_timeout)

                if not otp_value:
                    raise TimeoutError(f"OTP not provided within {otp_timeout}s")

            # Fill OTP – handle both single-field and multi-digit-box UIs
            logger.info("[AngelOneAgent] Typing OTP into page: %s", otp_value)
            self._fill_otp(page, otp_value)

            # Submit OTP
            try:
                submit_btn = page.locator(_SEL_OTP_SUBMIT).first
                submit_btn.wait_for(state="visible", timeout=5_000)
                self._move_to(page, submit_btn)
                time.sleep(_rnd.uniform(0.2, 0.5))
                submit_btn.click()
                logger.debug("[AngelOneAgent] Clicked OTP Submit")
            except PWTimeout:
                logger.warning("[AngelOneAgent] OTP Submit button not found – pressing Enter")
                page.keyboard.press("Enter")

            # ── Step 3: Password (appears after OTP is verified) ──────────────
            logger.info("[AngelOneAgent] Waiting for Password field after OTP …")
            try:
                pwd_loc = page.locator(_SEL_PASSWORD).first
                pwd_loc.wait_for(state="visible", timeout=20_000)
                logger.info("[AngelOneAgent] Password field appeared – entering password")
                self._move_to(page, pwd_loc)
                time.sleep(_rnd.uniform(0.15, 0.35))
                self._type_into(page, pwd_loc, password)
            except PWTimeout:
                raise RuntimeError("Password field did not appear after OTP entry")

            self._idle_mouse(page)
            time.sleep(_rnd.uniform(0.3, 0.7))

            # Click Login button
            try:
                login_btn = page.locator(_SEL_LOGIN_BTN).first
                login_btn.wait_for(state="visible", timeout=5_000)
                self._move_to(page, login_btn)
                time.sleep(_rnd.uniform(0.2, 0.5))
                login_btn.click()
                logger.debug("[AngelOneAgent] Clicked Login button (post-OTP)")
            except PWTimeout:
                logger.warning("[AngelOneAgent] Login button not found – pressing Enter")
                page.keyboard.press("Enter")

        else:
            # ── Device trusted — password appeared without OTP ─────────────────
            logger.info("[AngelOneAgent] Entering password (device trusted, no OTP) …")
            try:
                pwd_loc = page.locator(_SEL_PASSWORD).first
                pwd_loc.wait_for(state="visible", timeout=3_000)
                self._move_to(page, pwd_loc)
                time.sleep(_rnd.uniform(0.15, 0.35))
                self._type_into(page, pwd_loc, password)
            except PWTimeout:
                raise RuntimeError("Password field not found on page")

            self._idle_mouse(page)
            time.sleep(_rnd.uniform(0.3, 0.7))

            try:
                login_btn = page.locator(_SEL_LOGIN_BTN).first
                login_btn.wait_for(state="visible", timeout=5_000)
                self._move_to(page, login_btn)
                time.sleep(_rnd.uniform(0.2, 0.5))
                login_btn.click()
                logger.debug("[AngelOneAgent] Clicked Login button (trusted device)")
            except PWTimeout:
                logger.warning("[AngelOneAgent] Login button not found – pressing Enter")
                page.keyboard.press("Enter")

        # ── Step 4: Wait for post-login redirect ─────────────────────────────
        logger.info("[AngelOneAgent] Waiting for post-login redirect …")
        try:
            page.wait_for_url(
                lambda url: "/auth" not in url,
                timeout = 20_000,
            )
        except PWTimeout:
            raise RuntimeError(
                f"Login did not complete – still on {page.url!r}.  "
                "Check credentials / OTP."
            )

        logger.info("[AngelOneAgent] Post-login URL: %s", page.url)
        # Clean up relay file so a stale OTP can't be reused in a future session
        clear_otp()
        return {"url": page.url}

    # ── helpers ───────────────────────────────────────────────────────────────

    # ── human-behaviour helpers ───────────────────────────────────────────────

    @staticmethod
    def _idle_mouse(page) -> None:
        """
        Move the mouse to a few random positions on the page — simulates a
        human's eye wandering across the screen before they act.
        """
        import random
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]
        steps = random.randint(2, 4)
        for _ in range(steps):
            x = random.randint(int(w * 0.1), int(w * 0.9))
            y = random.randint(int(h * 0.1), int(h * 0.7))
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.1, 0.35))

    @staticmethod
    def _move_to(page, locator) -> None:
        """
        Move mouse to a locator through a random intermediate point so the
        path isn't a straight line (straight lines are a bot signature).
        """
        import random
        try:
            box = locator.bounding_box()
            if not box:
                locator.hover()
                return
            target_x = box["x"] + box["width"]  / 2
            target_y = box["y"] + box["height"] / 2
            vp = page.viewport_size or {"width": 1280, "height": 800}
            # Current position: somewhere random (we don't track it)
            mid_x = random.randint(int(vp["width"]  * 0.2), int(vp["width"]  * 0.8))
            mid_y = random.randint(int(vp["height"] * 0.2), int(vp["height"] * 0.6))
            page.mouse.move(mid_x, mid_y)
            time.sleep(random.uniform(0.05, 0.15))
            page.mouse.move(target_x, target_y)
        except Exception:
            locator.hover()

    @staticmethod
    def _human_click(page, selector: str) -> None:
        """Move to element via a waypoint, pause, then click."""
        import random
        from playwright.sync_api import TimeoutError as PWTimeout
        for sel in [s.strip() for s in selector.split(",")]:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=3_000)
                AngelOneAgent._move_to(page, loc)
                time.sleep(random.uniform(0.15, 0.4))
                loc.click()
                time.sleep(random.uniform(0.05, 0.15))
                return
            except PWTimeout:
                continue

    @staticmethod
    def _type_into(page, locator, value: str) -> None:
        """
        Reliably type into a specific locator:
          1. scroll into view
          2. focus the element explicitly
          3. triple-click to select any pre-filled text
          4. type character-by-character via press_sequentially (targets the
             locator directly, not the page keyboard focus)
        Falls back to page.keyboard.type() if press_sequentially unavailable.
        """
        import random as _rnd
        locator.scroll_into_view_if_needed()
        locator.focus()
        time.sleep(_rnd.uniform(0.1, 0.2))
        locator.triple_click()          # select all existing text
        time.sleep(_rnd.uniform(0.05, 0.1))
        try:
            locator.press_sequentially(value, delay=_rnd.randint(60, 140))
        except AttributeError:
            # Older Playwright — fall back to page keyboard
            page.keyboard.type(value, delay=_rnd.randint(60, 140))

    @staticmethod
    def _human_type(page, value: str) -> None:
        """
        Type a string with realistic variable keystroke timing.
        Occasionally pauses mid-word as if the user is thinking.
        """
        import random
        for i, ch in enumerate(value):
            # Rare mid-typing pause (thinking / distracted)
            if i > 0 and random.random() < 0.07:
                time.sleep(random.uniform(0.3, 0.9))
            delay = random.randint(60, 160)
            page.keyboard.type(ch, delay=delay)

    @staticmethod
    def _fill_first_visible(page, selector: str, value: str, label: str = "field") -> None:
        """Legacy wrapper used in a few places — delegates to _human_click + _human_type."""
        AngelOneAgent._human_click(page, selector)
        AngelOneAgent._human_type(page, value)
        logger.debug("[AngelOneAgent] Typed %s", label)

    @staticmethod
    def _fill_otp(page, otp: str) -> None:
        """
        Fill OTP – supports both:
          a) A single text input (fill entire string)
          b) Individual digit boxes (type one char per box)
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        import random as _rnd

        # Try single-field first
        try:
            field = page.locator(_SEL_OTP_FIELD).first
            field.wait_for(state="visible", timeout=3_000)
            field.hover()
            time.sleep(_rnd.uniform(0.1, 0.2))
            field.click()
            for ch in otp:
                page.keyboard.type(ch, delay=_rnd.randint(80, 180))
            logger.debug("[AngelOneAgent] Typed OTP into single field")
            return
        except PWTimeout:
            pass

        # Try individual digit boxes
        try:
            boxes = page.locator(_SEL_OTP_DIGIT).all()
            if boxes:
                for box, digit in zip(boxes, otp):
                    box.click()
                    page.keyboard.type(digit, delay=_rnd.randint(80, 180))
                logger.debug("[AngelOneAgent] Typed OTP into %d digit boxes", len(boxes))
                return
        except Exception as exc:
            logger.warning("[AngelOneAgent] _fill_otp digit-box attempt failed: %s", exc)

        raise RuntimeError("Could not locate OTP input field(s) on the page")

    # ── OWA helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _open_owa(page, username: str, password: str) -> None:
        """Open OWA in the given tab, log in if needed.

        Microsoft login flow:
          1. outlook.office.com  →  redirects to login.microsoftonline.com
          2. Account picker (prompt=select_account) — click existing tile or 'Use another account'
          3. Email input  →  Next
          4. Password input  →  Sign in
          5. 'Stay signed in?' → No
          6. Redirected back to outlook.office.com/mail/
        """
        from playwright.sync_api import TimeoutError as PWTimeout
        import random as _rnd

        page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=30_000)

        # Already logged in (persistent profile) — inbox visible
        if "outlook.office.com/mail" in page.url:
            try:
                page.wait_for_selector("div[role='option'], div[class*='mailList']", timeout=5_000)
                logger.info("[AngelOneAgent] OWA already authenticated via persistent profile")
                return
            except PWTimeout:
                pass

        # ── Step 1: Account picker ─────────────────────────────────────────────
        # Microsoft shows previously used accounts — click ours if present,
        # otherwise click 'Use another account' then type the email.
        try:
            page.wait_for_selector(
                f"[data-test-id='{username}'], div[title='{username}']",
                state="visible", timeout=5_000,
            )
            logger.info("[AngelOneAgent] OWA account picker: clicking existing tile for %s", username)
            page.locator(f"[data-test-id='{username}'], div[title='{username}']").first.click()
        except PWTimeout:
            # Account not in picker — click 'Use another account' then type email
            logger.info("[AngelOneAgent] OWA account picker: using 'Use another account'")
            try:
                other = page.locator(
                    "[data-test-id='otherTileText'], "
                    "div:has-text('Use another account'), "
                    "div:has-text('Sign in with a different account')"
                ).first
                other.wait_for(state="visible", timeout=5_000)
                other.click()
            except PWTimeout:
                pass   # No picker at all — maybe went straight to email input

            # ── Step 2: Email input ────────────────────────────────────────────
            try:
                email_field = page.locator("input[name='loginfmt'], input[type='email']").first
                email_field.wait_for(state="visible", timeout=8_000)
                email_field.click()
                time.sleep(_rnd.uniform(0.2, 0.4))
                page.keyboard.type(username, delay=_rnd.randint(60, 120))
                time.sleep(_rnd.uniform(0.3, 0.5))
                page.locator("input[type='submit'], button:has-text('Next')").first.click()
                logger.info("[AngelOneAgent] OWA email entered")
            except PWTimeout:
                logger.warning("[AngelOneAgent] OWA email field not found")

        # ── Step 3: Password ───────────────────────────────────────────────────
        try:
            pwd_field = page.locator("input[name='passwd'], input[type='password']").first
            pwd_field.wait_for(state="visible", timeout=10_000)
            AngelOneAgent._type_into(page, pwd_field, password)
            time.sleep(_rnd.uniform(0.3, 0.5))
            page.locator("input[type='submit'], button:has-text('Sign in')").first.click()
            logger.info("[AngelOneAgent] OWA password entered")
        except PWTimeout:
            logger.warning("[AngelOneAgent] OWA password field not found")
            return

        # ── Step 4: 'Stay signed in?' ──────────────────────────────────────────
        try:
            page.wait_for_selector("#idBtn_Back", state="visible", timeout=6_000)
            page.click("#idBtn_Back")   # 'No' — don't persist KMSI token
            logger.info("[AngelOneAgent] OWA dismissed 'Stay signed in'")
        except PWTimeout:
            pass

        # ── Step 5: Wait for inbox ─────────────────────────────────────────────
        try:
            page.wait_for_url(lambda u: "outlook.office.com/mail" in u, timeout=30_000)
            logger.info("[AngelOneAgent] OWA login complete")
        except PWTimeout:
            logger.warning("[AngelOneAgent] OWA did not reach inbox after login (URL: %s)", page.url)

    @staticmethod
    def _get_otp_from_owa_tab(owa_page) -> Optional[str]:
        """
        Scan the visible OWA inbox for an unread Angel One OTP email.
        Clicks the email open, extracts the 6-digit OTP, and returns it.
        """
        import re as _re
        _OTP = _re.compile(r"\b(\d{6})\b")
        _SENDERS  = ["angelone.in", "angelbroking.com", "angel-one.in"]
        _SUBJECTS = ["otp", "one time", "one-time", "verification", "login"]

        from playwright.sync_api import TimeoutError as PWTimeout

        try:
            rows = owa_page.locator("div[role='option']").all()
        except Exception:
            return None

        for row in rows:
            try:
                text = row.inner_text().lower()
            except Exception:
                continue

            if not (any(s in text for s in _SENDERS) or any(k in text for k in _SUBJECTS)):
                continue

            logger.info("[AngelOneAgent] OWA: potential OTP email — clicking to open")
            try:
                row.click()
                owa_page.wait_for_load_state("domcontentloaded", timeout=8_000)
            except Exception:
                continue

            body = ""
            for sel in [
                "div[role='main'] div[class*='bodyContainer']",
                "div[data-testid='message-body']",
                "div[role='main'] div[id*='UniqueMessageBody']",
                "div[role='main']",
            ]:
                try:
                    loc = owa_page.locator(sel).first
                    loc.wait_for(state="visible", timeout=4_000)
                    body = loc.inner_text()
                    if body.strip():
                        break
                except PWTimeout:
                    continue

            m = _OTP.search(body)
            if m:
                otp = m.group(1)
                logger.info("[AngelOneAgent] ✓ OTP extracted from OWA: %s  (body snippet: %r)", otp, body[:80])
                return otp

        return None

    @staticmethod
    def _angel_logout(page) -> None:
        """Log out from Angel One and wait for redirect to /auth."""
        from playwright.sync_api import TimeoutError as PWTimeout
        logger.info("[AngelOneAgent] Logging out …")
        try:
            # Try clicking the profile/logout menu
            for sel in [
                "button[aria-label*='profile' i]",
                "div[class*='userProfile']",
                "span[class*='userAvatar']",
                "button[class*='profile']",
            ]:
                try:
                    page.locator(sel).first.click(timeout=3_000)
                    break
                except Exception:
                    continue

            # Click logout item
            for sel in [
                "a:has-text('Logout')", "button:has-text('Logout')",
                "a:has-text('Sign out')", "button:has-text('Sign out')",
            ]:
                try:
                    page.locator(sel).first.click(timeout=3_000)
                    page.wait_for_url(lambda u: "/auth" in u, timeout=10_000)
                    logger.info("[AngelOneAgent] Logged out successfully")
                    return
                except Exception:
                    continue

        except Exception as exc:
            logger.warning("[AngelOneAgent] Logout click failed: %s", exc)

        # Fallback: navigate directly to logout URL
        try:
            page.goto("https://nxt.angelone.in/logout", timeout=10_000)
            logger.info("[AngelOneAgent] Navigated to logout URL")
        except Exception:
            pass

    # ── session persistence ───────────────────────────────────────────────────

    @staticmethod
    def _session_path(client_id: str) -> "Path":
        from pathlib import Path
        p = Path.home() / ".angelone" / f"session_{client_id}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _save_session(client_id: str, output: dict) -> None:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        path = AngelOneAgent._session_path(client_id)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "cookies":  output.get("cookies", []),
            "local_storage": output.get("local_storage", {}),
            "url":      output.get("url", ""),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("[AngelOneAgent] Session saved to %s", path)

    @staticmethod
    def _load_session(client_id: str) -> Optional[dict]:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        path = AngelOneAgent._session_path(client_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(payload["saved_at"])
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600
            if age_hours > 20:   # treat as expired after 20 hours
                logger.info("[AngelOneAgent] Saved session is %.1f hours old – will re-login", age_hours)
                return None
            logger.info("[AngelOneAgent] Found saved session (%.1f hours old)", age_hours)
            return payload
        except Exception as exc:
            logger.warning("[AngelOneAgent] Could not read saved session: %s", exc)
            return None

    @staticmethod
    def _try_resume_session(session: dict, headless: bool, client_id: str = "") -> Optional[dict]:
        """
        Load saved cookies into a fresh browser and verify the session is
        still valid by navigating to the post-login URL.
        Returns output dict on success, None if session has expired.
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            return None

        cookies = session.get("cookies", [])
        if not cookies:
            return None

        from pathlib import Path
        profile_dir = str(Path.home() / ".angelone" / f"browser_profile_{client_id}")

        with sync_playwright() as pw:
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir = profile_dir,
                    headless      = headless,
                    channel       = "msedge",
                    args          = ["--disable-blink-features=AutomationControlled"],
                )
            except Exception:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir = profile_dir,
                    headless      = headless,
                    args          = ["--disable-blink-features=AutomationControlled"],
                    user_agent    = (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
                    ),
                )
            page = context.new_page()

            try:
                page.goto("https://nxt.angelone.in/", wait_until="domcontentloaded", timeout=20_000)
                # If still on /auth the session expired
                if "/auth" in page.url:
                    logger.info("[AngelOneAgent] Session check: redirected to /auth – expired")
                    browser.close()
                    return None

                local_storage = page.evaluate(
                    "() => Object.fromEntries(Object.entries(localStorage))"
                )
                output = {
                    "cookies":       context.cookies(),
                    "local_storage": local_storage,
                    "url":           page.url,
                }
                context.close()
                return output

            except Exception as exc:
                logger.warning("[AngelOneAgent] Session resume check failed: %s", exc)
                context.close()
                return None

    @staticmethod
    def _diagnose_captcha(page) -> None:
        """
        Log all hidden inputs and captcha-related scripts on the page.
        Run this before submitting to identify what invisible-captcha mechanism
        the page uses (reCAPTCHA v3, hCaptcha, custom token, etc.)
        """
        info = page.evaluate("""() => {
            // All hidden inputs and their current values
            const hidden = Array.from(document.querySelectorAll('input[type="hidden"]'))
                .map(el => ({ name: el.name || el.id || '(unnamed)', value: el.value ? el.value.substring(0, 80) : '' }));

            // Any script tags whose src contains captcha/recaptcha keywords
            const scripts = Array.from(document.querySelectorAll('script[src]'))
                .map(s => s.src)
                .filter(s => /captcha|recaptcha|hcaptcha|turnstile|challenge/i.test(s));

            // grecaptcha / hcaptcha globals present?
            const globals = {
                grecaptcha:  typeof grecaptcha  !== 'undefined',
                hcaptcha:    typeof hcaptcha    !== 'undefined',
                turnstile:   typeof turnstile   !== 'undefined',
            };

            // Any element with a data-sitekey attribute
            const sitekeys = Array.from(document.querySelectorAll('[data-sitekey]'))
                .map(el => ({ tag: el.tagName, sitekey: el.getAttribute('data-sitekey') }));

            return { hidden, scripts, globals, sitekeys };
        }""")

        logger.info("[AngelOneAgent] ── Captcha Diagnosis ──────────────────────")
        logger.info("[AngelOneAgent] Hidden inputs : %s", info["hidden"])
        logger.info("[AngelOneAgent] Captcha scripts: %s", info["scripts"])
        logger.info("[AngelOneAgent] Globals present: %s", info["globals"])
        logger.info("[AngelOneAgent] Sitekeys found : %s", info["sitekeys"])
        logger.info("[AngelOneAgent] ─────────────────────────────────────────────")

    def _handle_captcha(self, page) -> bool:
        """
        Detect a captcha on the current page, solve it via Claude vision API,
        and fill the captcha input.

        Returns True if a captcha was found and handled, False if none present.
        Falls back to console input if Claude API is unavailable.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        # Quick check – is there a captcha input on this screen?
        try:
            captcha_input = page.locator(_SEL_CAPTCHA_INPUT).first
            captcha_input.wait_for(state="visible", timeout=2_000)
        except PWTimeout:
            logger.debug("[AngelOneAgent] No captcha input detected – skipping")
            return False

        logger.info("[AngelOneAgent] Captcha detected – solving with Claude vision …")

        # Screenshot just the captcha image element
        try:
            captcha_img = page.locator(_SEL_CAPTCHA_IMG).first
            captcha_img.wait_for(state="visible", timeout=3_000)
            img_bytes = captcha_img.screenshot()
        except (PWTimeout, Exception) as exc:
            logger.warning(
                "[AngelOneAgent] Captcha input found but could not screenshot captcha image: %s", exc
            )
            img_bytes = None

        if img_bytes:
            try:
                answer = self._claude_read_captcha(img_bytes)
                logger.info("[AngelOneAgent] Captcha answer from Claude: %r", answer)
                captcha_input.fill(answer)
                return True
            except Exception as exc:
                logger.warning("[AngelOneAgent] Claude captcha solve failed: %s", exc)

        # Fallback – ask user to type the captcha manually
        logger.warning("[AngelOneAgent] Falling back to manual captcha entry")
        print("\n[AngelOneAgent] Could not auto-solve captcha. Enter captcha text: ", end="", flush=True)
        try:
            answer = input().strip()
        except Exception:
            answer = ""
        if answer:
            captcha_input.fill(answer)
            return True
        logger.warning("[AngelOneAgent] No captcha answer provided – login may fail")
        return False

    @staticmethod
    def _claude_read_captcha(image_bytes: bytes) -> str:
        """
        Send a captcha screenshot to Claude and return the text it reads.
        Uses claude-haiku-4-5 for speed (simple OCR task).
        """
        import base64
        import anthropic

        client   = anthropic.Anthropic()
        img_b64  = base64.standard_b64encode(image_bytes).decode()

        response = client.messages.create(
            model      = "claude-haiku-4-5",
            max_tokens = 20,
            messages   = [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": "image/png",
                            "data":       img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a CAPTCHA image from a login form. "
                            "Read the characters shown and reply with ONLY the captcha text — "
                            "no explanation, no punctuation, just the characters as they appear."
                        ),
                    },
                ],
            }],
        )
        return next(b.text for b in response.content if b.type == "text").strip()
