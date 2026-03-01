"""
Portfolio Analysis Page - Streamlit
Loads mutual fund data from CSV and generates portfolio reports
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from pages.config_editor import ConfigEditor
from utils.mf_portfolio_pdf_generator  import MFPortfolioPDFGenerator, generate_ai_commentary
from utils.pdf_utils import format_currency_indian
import re
import os
import utils.navbar as navbar
from utils.Indices_lookup import SchemeLookup
from utils.customer_portfolio import get_customer_portfolio
from utils.mf_qoq_loader import PortfolioDataLoader
from utils.build_qoq_data import build_qoq_data
from utils.benchmark_utils import (
    load_benchmark_returns,
    build_quarterly_returns_with_benchmarks,
    _QUARTER_MONTH_MAP
)


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
            'benchmark': row['NatureXIRR'],
            'benchmark_index': row.get('benchmark_index', 0),
            'benchmark_return_1m': row.get('benchmark_return_1m', 0),
            'benchmark_return_3m': row.get('benchmark_return_3m', 0),
            'benchmark_return_1yr': row.get('benchmark_return_1yr', 0),
            'benchmark_return_3yr': row.get('benchmark_return_3yr', 0),
            'benchmark_return_5yr': row.get('benchmark_return_5yr', 0)
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

@st.cache_data
def _load_bench(parquet_dir: str) -> pd.DataFrame:
    """Load all index_dashboard parquet files and combine into one DataFrame."""
    files = {
        'Q3_2025':  'Index_Dashboard_MAR2025.parquet',
        'Q6_2025':  'Index_Dashboard_JUN2025.parquet',
        'Q9_2025':  'Index_Dashboard_SEP2025.parquet',
        'Q12_2025': 'Index_Dashboard_DEC2025.parquet',
        'Q3_2024':  'Index_Dashboard_MAR2024.parquet',
        'Q6_2024':  'Index_Dashboard_JUN2024.parquet',
        'Q9_2024':  'Index_Dashboard_SEP2024.parquet',
        'Q12_2024': 'Index_Dashboard_DEC2024.parquet',
    }

    dfs = []
    for qkey, filename in files.items():
        path = os.path.join(parquet_dir, filename)
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path)
        df['index_name'] = df['index_name'].astype(str).str.strip()
        # Inject year/month from filename if not already in the file
        if 'year' not in df.columns or 'month' not in df.columns:
            year, month = _QUARTER_MONTH_MAP[qkey]
            df['year']  = year
            df['month'] = month
        else:
            df['year']  = pd.to_numeric(df['year'],  errors='coerce').astype('Int64')
            df['month'] = pd.to_numeric(df['month'], errors='coerce').astype('Int64')
        dfs.append(df)

    if not dfs:
        raise FileNotFoundError(f"No index_dashboard parquet files found in: {parquet_dir}")

    combined = pd.concat(dfs, ignore_index=True)
    st.write(f"Loaded {len(dfs)} benchmark files — {len(combined)} index rows total")
    return combined

def main():
    st.title("📊 Portfolio Analysis & PDF Generator")
    navbar.navbar()
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
    lookup = SchemeLookup()
    customer_df = get_customer_portfolio(df, selected_customer, lookup=lookup)
    metrics = calculate_portfolio_metrics(customer_df)
    
    # Display portfolio summary
    st.header(f"Portfolio: {selected_customer}")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Value", f"{format_currency_indian(metrics['total_value'])}")
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
            editor = ConfigEditor()

            st.write(f"Use user values: {editor.config_data['use_custom_thresholds']}")
            if editor.config_data['use_custom_thresholds']:
                model_debt = metrics['allocation']['Debt']
                model_equity = metrics['allocation']['Equity']
                model_hybrid = metrics['allocation']['Hybrid']
            else:
                st.write("**Model Allocation (Editable)**")
                model_equity = st.number_input("Model Equity %", 0.0, 100.0, editor.config_data['default_model_allocation']['Equity'], key="model_eq")
                model_hybrid = st.number_input("Model Hybrid %", 0.0, 100.0, editor.config_data['default_model_allocation']['Balance (Hybrid)'], key="model_hy")
                model_debt = st.number_input("Model Debt %", 0.0, 100.0, editor.config_data['default_model_allocation']['Debt'], key="model_dt")
    
    with tab3:
        st.subheader("Fund Performance")
        
        if metrics['equity_funds']:
            st.write("**Equity Funds**")
            equity_perf = pd.DataFrame(metrics['equity_funds'])
            st.write(equity_perf.head(5))
            equity_perf.columns = ['Fund Name', 'XIRR %', 'Category XIRR %', 'Benchmark Index', '1M Return %', '3M Return %', '1Y Return %', '3Y Return %', '5Y Return %'  ]
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

                    loader = PortfolioDataLoader(bucket_name="winrich")
                    raw_qoq = loader.load_last_4_quarters(datetime.now(), customer=selected_customer)
                    st.write("Loaded QoQ data for last 4 quarters")
                    st.write("### QoQ Raw Data Debug")
                    qoq_data = {k: df for k, df in raw_qoq.items()
                                if isinstance(df, pd.DataFrame) and not df.empty}

                    if len(qoq_data) >= 2:
                        qoq = build_qoq_data(qoq_data)
                        # Only take these 3 keys — never overwrite the rest
                        portfolio_data['quarter_labels']    = qoq['quarter_labels']
                        portfolio_data['quarterly_returns'] = qoq['quarterly_returns']
                        portfolio_data['blended_return']    = qoq['blended_return']                

                    # ── After building portfolio_data, add these before generate_report ──────────

                    # A) Portfolio growth chart data (from quarterly_dict)
                    # ── Quarter label helper — inline in your Streamlit file ─────────────────────
                    _QUARTER_LABEL_MAP = {
                        'Q3_2023':  "Q1 FY23 (Jan-Mar '23)",
                        'Q6_2023':  "Q2 FY23 (Apr-Jun '23)",
                        'Q9_2023':  "Q3 FY23 (Jul-Sep '23)",
                        'Q12_2023': "Q4 FY23 (Oct-Dec '23)",
                        'Q3_2024':  "Q1 FY24 (Jan-Mar '24)",
                        'Q6_2024':  "Q2 FY24 (Apr-Jun '24)",
                        'Q9_2024':  "Q3 FY24 (Jul-Sep '24)",
                        'Q12_2024': "Q4 FY24 (Oct-Dec '24)",
                        'Q3_2025':  "Q1 FY25 (Jan-Mar '25)",
                        'Q6_2025':  "Q2 FY25 (Apr-Jun '25)",
                        'Q9_2025':  "Q3 FY25 (Jul-Sep '25)",
                        'Q12_2025': "Q4 FY25 (Oct-Dec '25)",
                        'Q3_2026':  "Q1 FY26 (Jan-Mar '26)",
                    }

                    # ── Build portfolio_trend from qoq_data ───────────────────────────────────────
                    def _sort_quarter_keys(keys):
                        def _key(k):
                            parts = k.split('_')
                            month = int(parts[0].replace('Q', ''))
                            year  = int(parts[1])
                            return year * 100 + month
                        return sorted(keys, key=_key)

                    portfolio_data['portfolio_trend'] = []
                    for k in _sort_quarter_keys(qoq_data.keys()):
                        df = qoq_data[k].copy()
                        df.columns        = [str(c).strip().strip("'\"") for c in df.columns]
                        df['TotalInvAmt'] = pd.to_numeric(df['TotalInvAmt'], errors='coerce').fillna(0)
                        df['CurValue']    = pd.to_numeric(df['CurValue'],    errors='coerce').fillna(0)
                        df['foliono']     = df['foliono'].astype(str).str.strip()

                        # ── FIX: last value per folio to avoid double-counting ───────────────────
                        folio_latest  = df.groupby('foliono').last().reset_index()
                        total_invested = float(folio_latest['TotalInvAmt'].sum())
                        total_current  = float(folio_latest['CurValue'].sum())

                        portfolio_data['portfolio_trend'].append({
                            'label':    _QUARTER_LABEL_MAP.get(k, k),
                            'invested': total_invested,
                            'current':  total_current,
                        })                    
                    benchmark_df = _load_bench("data")

                    # Sorted quarter keys matching your quarter_labels order
                    quarter_keys = sorted(qoq_data.keys())   # oldest → newest

                    # Rebuild quarterly_returns with real benchmark rows
                    portfolio_data['quarterly_returns'] = build_quarterly_returns_with_benchmarks(
                        portfolio_data,
                        benchmark_df,
                        quarter_keys,
                    )

                    # Verify
                    for row in portfolio_data['quarterly_returns']:
                        print(f"{'[BENCH]' if row['is_benchmark'] else '[FUND] '} "
                            f"{row['name'][:40]:40s} {row['returns']}")
                    
                    # C) Ensure quarter_labels are sorted oldest → newest
                    # Your loader returns ['Q12_2025','Q9_2025','Q6_2025','Q3_2025']
                    # After filtering and building QoQ, verify:
                    st.write("quarter_labels:", portfolio_data.get('quarter_labels'))
                    # Should read left-to-right: oldest quarter first 
                    st.write("### Portfolio Data Debug")
                    print("Index names in parquet:")
                    print(benchmark_df['index_name'].unique().tolist())

                    print("\nBenchmark indices in equity_funds:")
                    print([f.get('benchmark_index') for f in portfolio_data.get('equity_funds', [])])
                    st.write(f"**Keys:** {list(portfolio_data.keys())}")
                    st.write(f"**client_name:** {portfolio_data.get('client_name')}")
                    st.write(f"**summary keys:** {list(portfolio_data.get('summary', {}).keys())}")

                    st.write(f"**equity_funds count:** {len(portfolio_data.get('equity_funds', []))}")
                    if portfolio_data.get('equity_funds'):
                        st.write(f"**equity_funds[0]:** {portfolio_data['equity_funds'][0]}")

                    st.write(f"**hybrid_funds count:** {len(portfolio_data.get('hybrid_funds', []))}")

                    st.write(f"**client_allocation:** {portfolio_data.get('client_allocation')}")
                    st.write(f"**model_allocation:** {portfolio_data.get('model_allocation')}")

                    st.write(f"**amc_concentration:** {portfolio_data.get('amc_concentration')}")

                    st.write(f"**quarter_labels:** {portfolio_data.get('quarter_labels')}")
                    st.write(f"**quarterly_returns count:** {len(portfolio_data.get('quarterly_returns', []))}")
                    if portfolio_data.get('quarterly_returns'):
                        st.write(f"**quarterly_returns[0]:** {portfolio_data['quarterly_returns'][0]}")

                    st.write(f"**blended_return:** {portfolio_data.get('blended_return')}")
                    st.write(f"quarter_labels: {portfolio_data.get('quarter_labels')}")
                    st.write(f"quarterly_returns count: {len(portfolio_data.get('quarterly_returns', []))}")
                    st.write(f"blended_return: {portfolio_data.get('blended_return')}")
                    # Generate PDF
                    filename = f"portfolio_report_{selected_customer.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    #from utils import mf_portfolio_pdf_generator as _mod

                    #st.write("File:", _mod.__file__)
                    #st.write("Class methods:", [m for m in dir(_mod.MFPortfolioPDFGenerator) 
                    #         if not m.startswith('__')])
                    generator = MFPortfolioPDFGenerator(company_name)
                                        # B) AI commentary
                    with st.spinner("Generating AI commentary..."):
                        try:
                            #portfolio_data['commentary'] = generate_ai_commentary(portfolio_data)
                            st.success("Commentary generated")
                        except Exception as e:
                            st.warning(f"Commentary skipped: {e}")

                    output_file = generator.generate_report(portfolio_data, filename)
                    
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