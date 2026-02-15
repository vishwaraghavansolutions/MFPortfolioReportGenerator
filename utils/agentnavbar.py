import streamlit as st

def agentnavbar():
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
       st.write(" ")

    with cols[1]:
       st.write(" ")

    with cols[2]:
        if st.button("📊 Agent Analytics", key="nav_analytics"):
            st.switch_page("pages/agent_analytics.py")

    with cols[3]:
        if st.button("📊 Agent AI Coach", key="nav_coach"):
            st.switch_page("pages/agent_coach.py")

    with cols[4]:
        if st.button("🚪 Logout", key="nav_logout"):
            st.switch_page("login.py")