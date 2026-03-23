"""
utils/auth.py  
-------------
Shared authentication guard for all Streamlit pages.

Usage (at the very top of each page, after st.set_page_config):

    from utils.auth import require_login
    require_login()
"""

import streamlit as st


def require_login() -> None:
    """
    Redirect to login.py if the user is not authenticated.
    Call this at the top of every page (after set_page_config).
    """
    if not st.session_state.get("logged_in", False):
        st.switch_page("login.py")
