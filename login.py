"""
login.py
--------
WinRich — Entry point / Landing page.

• Shows login form when not authenticated.
• Shows the landing dashboard with page cards when authenticated.
"""

from __future__ import annotations
import hashlib
import streamlit as st

st.set_page_config(
    page_title="WinRich — Home",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Credentials ───────────────────────────────────────────────────────────────
# Replace with a proper auth backend / secrets store in production.
_USERS: dict[str, dict] = {
    "admin": {
        "password": hashlib.sha256("admin123".encode()).hexdigest(),
        "role": "admin",
        "name": "Administrator",
    },
    "advisor": {
        "password": hashlib.sha256("advisor123".encode()).hexdigest(),
        "role": "advisor",
        "name": "Financial Advisor",
    },
}


def _check_credentials(username: str, password: str):
    user = _USERS.get(username)
    if user and user["password"] == hashlib.sha256(password.encode()).hexdigest():
        return True, user
    return False, None


# ── Page cards ────────────────────────────────────────────────────────────────
_PAGES = [
    {
        "icon": "📊",
        "title": "Portfolio Analysis",
        "desc": "Generate quarterly performance reports, AI commentary, and PDF exports for individual clients.",
        "path": "pages/generate_portfolio_analysis.py",
        "roles": ["admin", "advisor"],
    },
    {
        "icon": "📨",
        "title": "Report Campaign",
        "desc": "Bulk-generate and email PDF reports to all clients. Monitor progress and resume from checkpoints.",
        "path": "pages/manage_mfreport_campaign.py",
        "roles": ["admin", "advisor"],
    },
    {
        "icon": "🏆",
        "title": "Fund Rankings",
        "desc": "Browse and score mutual funds across categories using WinRich's proprietary ranking model.",
        "path": "pages/view winrich fund ranks.py",
        "roles": ["admin", "advisor"],
    },
    {
        "icon": "🔗",
        "title": "Benchmark Mapping",
        "desc": "View and manage the mapping between mutual funds and their benchmark indices.",
        "path": "pages/view_fund__benchmark_mapping.py",
        "roles": ["admin", "advisor"],
    },
    {
        "icon": "📈",
        "title": "NSE Indices",
        "desc": "Download, manage, and explore NSE index constituent data.",
        "path": "pages/view_NSE_indices.py",
        "roles": ["admin", "advisor"],
    },
    {
        "icon": "⚙️",
        "title": "Configuration",
        "desc": "Edit application settings, email templates, and system parameters.",
        "path": "pages/manage_config.py",
        "roles": ["admin"],
    },
]

_CARD_CSS = """
<style>
.wr-card {
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    padding: 22px 20px 18px 20px;
    background: #fafafa;
    height: 160px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: box-shadow 0.15s;
}
.wr-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.10); }
.wr-card-icon  { font-size: 28px; line-height: 1; }
.wr-card-title { font-size: 15px; font-weight: 700; color: #1a1a1a; }
.wr-card-desc  { font-size: 12px; color: #555; line-height: 1.4; }
</style>
"""


def _show_login() -> None:
    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("## 🏦 WinRich Professional Services")
        st.markdown("##### MF Portfolio Report Platform")
        st.markdown("---")
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Login", use_container_width=True):
                if not username or not password:
                    st.error("Please enter both username and password.")
                else:
                    ok, user = _check_credentials(username, password)
                    if ok:
                        st.session_state["logged_in"] = True
                        st.session_state["username"] = username
                        st.session_state["role"] = user["role"]
                        st.session_state["name"] = user["name"]
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")


def _show_home() -> None:
    role = st.session_state.get("role", "advisor")
    name = st.session_state.get("name", "User")

    # ── Top bar ────────────────────────────────────────────────────────────────
    col_title, col_user, col_logout = st.columns([6, 2, 1])
    with col_title:
        st.markdown("## 🏦 WinRich — Portfolio Platform")
    with col_user:
        st.markdown(
            f"<div style='text-align:right;padding-top:12px;color:#555'>👤 {name}</div>",
            unsafe_allow_html=True,
        )
    with col_logout:
        if st.button("🚪 Logout", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    st.divider()
    st.markdown(_CARD_CSS, unsafe_allow_html=True)

    # ── Page cards ─────────────────────────────────────────────────────────────
    visible = [p for p in _PAGES if role in p["roles"]]
    cols = st.columns(3)
    for i, page in enumerate(visible):
        with cols[i % 3]:
            # Card HTML (display only)
            st.markdown(
                f"""<div class="wr-card">
                    <div class="wr-card-icon">{page['icon']}</div>
                    <div class="wr-card-title">{page['title']}</div>
                    <div class="wr-card-desc">{page['desc']}</div>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top:4px'></div>", unsafe_allow_html=True)
            if st.button(f"Open {page['title']}", key=f"card_{i}", use_container_width=True):
                st.switch_page(page["path"])
        # Add a small vertical gap between rows
        if (i + 1) % 3 == 0:
            st.markdown("<br>", unsafe_allow_html=True)


# ── Entry point ───────────────────────────────────────────────────────────────
if st.session_state.get("logged_in", False):
    _show_home()
else:
    _show_login()
