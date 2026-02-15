import streamlit as st

def navbar():
    st.markdown("""
        <style>
            .nav-container {
                display: flex;
                gap: 25px;
                padding: 12px 20px;
                background-color: #ffffff;
                border-bottom: 1px solid #e0e0e0;
                font-size: 17px;
                font-weight: 500;
            }
            .nav-item {
                cursor: pointer;
                color: #333;
            }
            .nav-item:hover {
                color: #007bff;
            }
        </style>
    """, unsafe_allow_html=True)

    cols = st.columns([1,1,1,1,1])

    with cols[0]:
        if st.button("🏠 Home", key="nav_home"):
            st.switch_page("pages/admin_home.py")

    with cols[1]:
        if st.button("👥 Manage Agents", key="nav_agents"):
            st.switch_page("pages/admin_agents.py")

    with cols[2]:
        if st.button("🧩 Map Product to Agents", key="nav_resolver"):
            st.switch_page("pages/queue_resolver.py")

    with cols[3]:
        if st.button("📊 Analytics", key="nav_analytics"):
            st.switch_page("pages/TicketAnalytics.py")

    with cols[4]:
        if st.button("🚪 Logout", key="nav_logout"):
            st.switch_page("login.py")