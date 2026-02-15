"""
Portfolio Analysis Page - Streamlit
Loads mutual fund data from CSV and generates portfolio reports
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from pdf_generator import PortfolioPDFGenerator
from utils.pdf_utils import format_currency_indian
import re

scheme_df = pd.read_csv('data/SchemeData2301262313SS.csv')
scheme_df.columns = scheme_df.columns.str.strip()

def clean_fund_name(fund_name):
    """
    Remove plan type suffixes from fund names
    Example: "Canara Robeco Large Cap Fund - Regular Growth" -> "Canara Robeco Large Cap Fund"
    """
    patterns = [
        r'\s*-\s*Regular.*$',
        r'\s*-\s*Direct.*$',
        r'\s*-\s*Growth.*$',
        r'\s*-\s*IDCW.*$',
        r'\s*-\s*Dividend.*$',
        r'\s*\(.*\)$',
    ]
    
    cleaned = fund_name
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()

def get_amc_for_fund(fund_name, scheme_df):
    """
    Lookup AMC name for a given fund name
    
    Args:
        fund_name: Name of the fund from customer holdings
        scheme_df: DataFrame with scheme data
        
    Returns:
        AMC name (cleaned) or 'Unknown' if not found
    """
    # Try to find matching scheme
    cleaned_name = clean_fund_name(fund_name)
    match = scheme_df[scheme_df['Scheme Name'].str.contains(cleaned_name, case=False, na=False, regex=False)]
    if not match.empty:
        amc = match.iloc[0]['AMC']
        # Clean AMC name (remove "Limited", "Ltd", etc.)
        amc_cleaned = amc.replace(' Limited', '').replace(' Ltd', '').replace(' Pvt.', '').strip()
        return amc_cleaned
    
    return 'Unknown'

def load_mutual_fund_data(csv_path):
    """Load and process mutual fund data from CSV"""
    df = pd.read_csv(csv_path)
    return df


def get_customer_portfolio(df, customer_name):
    """Get portfolio data for a specific customer"""
    customer_df = df[df['c_name'] == customer_name].copy()
    return customer_df


def calculate_portfolio_metrics(customer_df):
    """Calculate portfolio metrics from customer data"""
    
    # Asset allocation by Nature
    total_value = customer_df['CurValue'].sum()
    
    equity_value = customer_df[customer_df['Nature'] == 'Equity']['CurValue'].sum()
    balance_value = customer_df[customer_df['Nature'] == 'Balance']['CurValue'].sum()
    debt_value = customer_df[customer_df['Nature'] == 'Debt']['CurValue'].sum()
    
    allocation = {
        'Equity': (equity_value / total_value * 100) if total_value > 0 else 0,
        'Hybrid': (balance_value / total_value * 100) if total_value > 0 else 0,
        'Debt': (debt_value / total_value * 100) if total_value > 0 else 0
    }
    
    # Fund details
    equity_funds = []
    for _, row in customer_df[customer_df['Nature'] == 'Equity'].iterrows():
        equity_funds.append({
            'name': row['s_name'],
            'xirr': row['FolioXIRR'],
            'benchmark': row['NatureXIRR']
        })
    
    hybrid_funds = []
    for _, row in customer_df[customer_df['Nature'] == 'Balance'].iterrows():
        hybrid_funds.append({
            'name': row['s_name'],
            'xirr': row['FolioXIRR']
        })
    
    # AMC concentration
    # AMC concentration (CORRECTED)
    amc_data = {}
    unmatched_funds = []

    for _, row in customer_df.iterrows():
        fund_name = row['s_name']  # Your fund name column
        # Lookup AMC from scheme data
        amc_name = get_amc_for_fund(fund_name, scheme_df)
        if amc_name != 'Unknown':
            amc_data[amc_name] = amc_data.get(amc_name, 0) + 1
        else:
            unmatched_funds.append(fund_name)

    return {
        'total_value': total_value,
        'allocation': allocation,
        'equity_funds': equity_funds,
        'hybrid_funds': hybrid_funds,
        'amc_concentration': amc_data,
        'num_funds': len(customer_df)
    }


def main():
    st.title("📊 Portfolio Analysis & PDF Generator")
    
    # File uploader
    st.sidebar.header("📁 Data Source")
    #csv_file = st.sidebar.file_uploader("Upload Mutual Fund CSV", type=['csv'])
    
    # if csv_file is None:
    #     st.info("👈 Please upload the mutual fund CSV file from the sidebar")
    #     st.markdown("""
    #     ### Expected CSV Format:
    #     - h_name (AMC Name)
    #     - c_name (Customer Name)
    #     - s_name (Scheme Name)
    #     - Nature (Equity/Balance/Debt)
    #     - CurValue (Current Value)
    #     - FolioXIRR (Fund XIRR)
    #     - NatureXIRR (Category XIRR)
    #     """)
    #     return
    
    # Load data
    try:
        csv_file = 'data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv'  # Replace with your actual file path
        df = load_mutual_fund_data(csv_file)
        st.sidebar.success(f"✅ Loaded {len(df)} records")
    except Exception as e:
        st.error(f"Error loading CSV: {str(e)}")
        return
    
    # Customer selection
    st.sidebar.header("👤 Select Customer")
    customers = sorted(df['c_name'].unique())
    selected_customer = st.sidebar.selectbox("Customer Name", customers)
    
    if not selected_customer:
        st.warning("Please select a customer")
        return
    
    # Get customer portfolio
    customer_df = get_customer_portfolio(df, selected_customer)
    metrics = calculate_portfolio_metrics(customer_df)
    
    # Display portfolio summary
    st.header(f"Portfolio: {selected_customer}")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Value", f"₹{format_currency_indian(metrics['total_value'])}")
    with col2:
        st.metric("Total Funds", metrics['num_funds'])
    with col3:
        st.metric("Equity %", f"{metrics['allocation']['Equity']:.1f}%")
    with col4:
        st.metric("AMCs", len(metrics['amc_concentration']))
    
    # Tabs for detailed view
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Holdings", "📈 Allocations", "💼 Performance", "⚙️ Settings"])
    
    with tab1:
        st.subheader("Current Holdings")
        display_df = customer_df[['h_name', 's_name', 'Nature', 'BalUnit', 'CurValue', 'FolioXIRR', 'absReturn']].copy()
        display_df.columns = ['AMC', 'Scheme', 'Type', 'Units', 'Current Value', 'XIRR %', 'Abs Return %']
        display_df['Current Value'] = display_df['Current Value'].apply(lambda x: f"₹{x:,.2f}")
        st.dataframe(display_df, use_container_width=True)
    
    with tab2:
        st.subheader("Asset Allocation")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Current Allocation**")
            alloc_df = pd.DataFrame({
                'Asset Class': ['Equity', 'Hybrid', 'Debt'],
                'Percentage': [
                    metrics['allocation']['Equity'],
                    metrics['allocation']['Hybrid'],
                    metrics['allocation']['Debt']
                ]
            })
            st.dataframe(alloc_df, use_container_width=True)
        
        with col2:
            st.write("**Model Allocation (Editable)**")
            model_equity = st.number_input("Model Equity %", 0.0, 100.0, 65.0, key="model_eq")
            model_hybrid = st.number_input("Model Hybrid %", 0.0, 100.0, 20.0, key="model_hy")
            model_debt = st.number_input("Model Debt %", 0.0, 100.0, 15.0, key="model_dt")
    
    with tab3:
        st.subheader("Fund Performance")
        
        if metrics['equity_funds']:
            st.write("**Equity Funds**")
            equity_perf = pd.DataFrame(metrics['equity_funds'])
            equity_perf.columns = ['Fund Name', 'XIRR %', 'Category XIRR %']
            st.dataframe(equity_perf, use_container_width=True)
        
        if metrics['hybrid_funds']:
            st.write("**Hybrid Funds**")
            hybrid_perf = pd.DataFrame(metrics['hybrid_funds'])
            hybrid_perf.columns = ['Fund Name', 'XIRR %']
            st.dataframe(hybrid_perf, use_container_width=True)
    
    with tab4:
        st.subheader("Report Settings")
        company_name = st.text_input("Company Name", "Winrich Professional Services")
        
        st.write("**Summary Information (Editable)**")
        summary_text = st.text_area(
            "Additional Notes",
            "Portfolio analysis based on latest NAV data.",
            height=100
        )
    
    # Generate PDF Button
    st.markdown("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        if st.button("🚀 Generate PDF Report", use_container_width=True, type="primary"):
            try:
                with st.spinner("Generating professional PDF report..."):
                    
                    # Get email and mobile from first record
                    first_row = customer_df.iloc[0]
                    def mask_phone(phone):
                        digits = ''.join(filter(str.isdigit, phone))
                        return "*" * (len(digits) - 4) + digits[-4:]
                    
                    def mask_email(email):
                        user, domain = email.split("@")
                        masked_user = user[0] + "*" * (len(user) - 1)
                        return masked_user + "@" + domain

                    email = mask_email(first_row.get('Email', '').strip())
                    mobile = mask_phone(first_row.get('Mobile', ''))
                    
                    # Prepare portfolio data
                    portfolio_data = {
                        'client_name': selected_customer,
                        'report_date': datetime.now().strftime('%B %d, %Y'),
                        'summary': {
                            'Client Name': selected_customer,
                            'Email': email,
                            'Mobile': str(mobile),
                            'Report Date': datetime.now().strftime('%B %d, %Y'),
                            'Total Portfolio Value': format_currency_indian(metrics['total_value']),
                            'Total Funds': str(metrics['num_funds']),
                            'Equity Allocation': f"{metrics['allocation']['Equity']:.2f}%",
                            'Hybrid Allocation': f"{metrics['allocation']['Hybrid']:.2f}%",
                            'Debt Allocation': f"{metrics['allocation']['Debt']:.2f}%",
                            'Number of AMCs': str(len(metrics['amc_concentration']))
                        },
                        'client_allocation': {
                            'Equity': metrics['allocation']['Equity'],
                            'Hybrid': metrics['allocation']['Hybrid'],
                            'Debt': metrics['allocation']['Debt']
                        },
                        'model_allocation': {
                            'Equity': model_equity,
                            'Hybrid': model_hybrid,
                            'Debt': model_debt
                        },
                        'equity_funds': metrics['equity_funds'],
                        'hybrid_funds': metrics['hybrid_funds'],
                        'amc_concentration': metrics['amc_concentration']
                    }
                    
                    # Generate PDF
                    filename = f"portfolio_report_{selected_customer.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    generator = PortfolioPDFGenerator(company_name)
                    output_file = generator.generate_portfolio_report(portfolio_data, filename)
                    
                    # Success message
                    st.success("✅ PDF Report Generated Successfully!")
                    
                    # Download button
                    with open(output_file, "rb") as pdf_file:
                        st.download_button(
                            label="📥 Download PDF Report",
                            data=pdf_file,
                            file_name=filename,
                            mime="application/pdf",
                            use_container_width=True
                        )
                    
                    # Summary
                    st.info(f"""
                    **Report Summary:**
                    - Client: {selected_customer}
                    - Total Value: ₹{metrics['total_value']:,.0f}
                    - Equity Funds: {len(metrics['equity_funds'])}
                    - Hybrid Funds: {len(metrics['hybrid_funds'])}
                    - Total AMCs: {len(metrics['amc_concentration'])}
                    """)
                    
            except Exception as e:
                st.error(f"❌ Error generating PDF: {str(e)}")
                with st.expander("See error details"):
                    st.exception(e)


if __name__ == "__main__":
    main()