"""
Enhanced Login Page
Integrates with PDF Report Module
"""

import streamlit as st
import hashlib
from pdf_report_module import create_pdf_generator_widget


# Simple user database (replace with real auth system)
USERS = {
    "admin": {
        "password": hashlib.sha256("admin123".encode()).hexdigest(),
        "role": "admin",
        "name": "Administrator"
    },
    "advisor": {
        "password": hashlib.sha256("advisor123".encode()).hexdigest(),
        "role": "advisor",
        "name": "Financial Advisor"
    }
}


def check_credentials(username, password):
    """Verify user credentials"""
    if username in USERS:
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        if USERS[username]["password"] == hashed_password:
            return True, USERS[username]
    return False, None


def show_login_page():
    """Display login page"""
    
    st.title("🔐 Portfolio Analysis Login")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("---")
        
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            
            submit = st.form_submit_button("Login", use_container_width=True)
            
            if submit:
                if not username or not password:
                    st.error("Please enter both username and password")
                else:
                    success, user_data = check_credentials(username, password)
                    
                    if success:
                        # Set session state
                        st.session_state["logged_in"] = True
                        st.session_state["username"] = username
                        st.session_state["role"] = user_data["role"]
                        st.session_state["name"] = user_data["name"]
                        
                        st.success(f"✅ Welcome {user_data['name']}!")
                        st.rerun()
                    else:
                        st.error("❌ Invalid credentials")
        
        st.markdown("---")
        
        with st.expander("ℹ️ Demo Credentials"):
            st.code("""
Admin Account:
Username: admin
Password: admin123

Advisor Account:
Username: advisor
Password: advisor123
            """)


def show_home_page():
    """Display home page after login"""
    
    st.title("📊 Portfolio Analysis System")
    
    st.write(f"Welcome, **{st.session_state.get('name', 'User')}**!")
    
    # Quick actions
    st.markdown("---")
    st.subheader("Quick Actions")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("📈 Portfolio Analysis", use_container_width=True):
            st.switch_page("pages/portfolio_analysis.py")
    
    with col2:
        if st.button("📄 Generate PDF Reports", use_container_width=True):
            st.info("Select customer in Portfolio Analysis page")
    
    with col3:
        if st.button("⚙️ Settings", use_container_width=True):
            if st.session_state.get("role") == "admin":
                st.switch_page("pages/config_editor.py")
            else:
                st.warning("Admin access required")
    
    # Recent reports
    st.markdown("---")
    st.subheader("📁 Recent Reports")
    
    import os
    import glob
    from datetime import datetime
    
    reports_dir = "reports"
    if os.path.exists(reports_dir):
        pdf_files = glob.glob(os.path.join(reports_dir, "*.pdf"))
        pdf_files.sort(key=os.path.getmtime, reverse=True)
        
        if pdf_files[:5]:  # Show last 5
            for pdf_path in pdf_files[:5]:
                filename = os.path.basename(pdf_path)
                modified_time = datetime.fromtimestamp(os.path.getmtime(pdf_path))
                
                col1, col2, col3 = st.columns([3, 2, 1])
                
                with col1:
                    st.text(filename)
                
                with col2:
                    st.text(modified_time.strftime("%Y-%m-%d %H:%M"))
                
                with col3:
                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "📥",
                            data=f,
                            file_name=filename,
                            mime="application/pdf",
                            key=f"download_{filename}"
                        )
        else:
            st.info("No reports generated yet")
    else:
        st.info("No reports directory found")
    
    # Navigation
    st.markdown("---")
    st.subheader("📑 Available Pages")
    
    pages = [
        ("📊 Portfolio Analysis", "Main portfolio analysis dashboard"),
        ("📄 PDF Reports", "Generate and manage PDF reports"),
        ("⚙️ Configuration", "Edit system configuration (Admin only)"),
        ("🤖 AI Market Signals", "Generate market insights with AI"),
    ]
    
    for title, description in pages:
        st.markdown(f"**{title}**")
        st.write(description)
        st.markdown("")
    
    # Logout
    st.markdown("---")
    if st.button("🚪 Logout"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


def main():
    """Main function"""
    
    # Check if logged in
    if not st.session_state.get("logged_in", False):
        show_login_page()
    else:
        show_home_page()


if __name__ == "__main__":
    main()