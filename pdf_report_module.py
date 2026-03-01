"""
PDF Report Generator Module
Standalone module for generating portfolio analysis PDFs
Can be called from any page in the application
"""

import os
import streamlit as st
from datetime import datetime
import pandas as pd
from pdf_generator import MFPortfolioPDFGenerator


class PDFReportModule:
    """Modular PDF report generator that can be called from anywhere"""
    
    def __init__(self, company_name="Winrich Professional Services"):
        self.company_name = company_name
        self.reports_dir = "reports"
        os.makedirs(self.reports_dir, exist_ok=True)
    
    def generate_report(self, customer_data, report_config=None):
        """
        Generate PDF report for a customer
        
        Args:
            customer_data: Dict containing customer portfolio data
            report_config: Optional dict with report customization settings
            
        Returns:
            tuple: (pdf_path, success, error_message)
        """
        try:
            # Extract customer info
            customer_name = customer_data.get('customer_name', 'Unknown')
            
            # Generate filename
            safe_name = customer_name.replace(' ', '_').replace('/', '_')
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            pdf_filename = f"portfolio_report_{safe_name}_{timestamp}.pdf"
            pdf_path = os.path.join(self.reports_dir, pdf_filename)
            
            # Prepare report data
            report_data = self._prepare_report_data(customer_data, report_config)
            
            # Generate PDF
            generator = MFPortfolioPDFGenerator(company_name=self.company_name)
            generator.generate_report(report_data, pdf_path)
            
            return pdf_path, True, None
            
        except Exception as e:
            return None, False, str(e)
    
    def _prepare_report_data(self, customer_data, report_config):
        """Prepare data structure for PDF generation"""
        
        # Get configuration or use defaults
        config = report_config or {}
        
        report_data = {
            'customer_name': customer_data.get('customer_name', 'Unknown'),
            'total_value': customer_data.get('total_value', '₹0'),
            'equity_pct': customer_data.get('equity_pct', '0%'),
            'hybrid_pct': customer_data.get('hybrid_pct', '0%'),
            'debt_pct': customer_data.get('debt_pct', '0%'),
            'total_funds': customer_data.get('total_funds', 0),
            'num_amcs': customer_data.get('num_amcs', 0),
            'summary_df': customer_data.get('summary_df'),
            'comparison_df': customer_data.get('comparison_df'),
            'market_cap_df': customer_data.get('market_cap_df'),
            'equity_perf_df': customer_data.get('equity_perf_df'),
            'hybrid_perf_df': customer_data.get('hybrid_perf_df'),
            'ranking_history': customer_data.get('ranking_history', []),
            'amc_concentration': customer_data.get('amc_concentration'),
            'observations': customer_data.get('observations', [])
        }
        
        return report_data
    
    def show_generation_ui(self, customer_data, key_prefix="pdf"):
        """
        Display PDF generation UI in Streamlit
        
        Args:
            customer_data: Customer portfolio data
            key_prefix: Unique key prefix for Streamlit widgets
        """
        st.subheader("📄 Generate PDF Report")
        
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.write(f"**Customer:** {customer_data.get('customer_name', 'Unknown')}")
            st.write(f"**Portfolio Value:** {customer_data.get('total_value', '₹0')}")
        
        with col2:
            if st.button("🔄 Generate PDF", key=f"{key_prefix}_generate"):
                with st.spinner("Generating PDF report..."):
                    pdf_path, success, error = self.generate_report(customer_data)
                    
                    if success:
                        st.success("✅ PDF generated successfully!")
                        
                        # Provide download button
                        with open(pdf_path, "rb") as pdf_file:
                            st.download_button(
                                label="📥 Download PDF Report",
                                data=pdf_file,
                                file_name=os.path.basename(pdf_path),
                                mime="application/pdf",
                                key=f"{key_prefix}_download"
                            )
                        
                        st.info(f"📁 Report saved to: `{pdf_path}`")
                    else:
                        st.error(f"❌ Error generating PDF: {error}")


def create_pdf_generator_widget(customer_data, company_name=None):
    """
    Convenience function to create PDF generation widget
    
    Usage:
        from pdf_report_module import create_pdf_generator_widget
        
        customer_data = {...}
        create_pdf_generator_widget(customer_data)
    """
    company = company_name or "Winrich Professional Services"
    module = PDFReportModule(company_name=company)
    module.show_generation_ui(customer_data)


# Batch PDF Generation
class BatchPDFGenerator:
    """Generate PDFs for multiple customers at once"""
    
    def __init__(self, company_name="Winrich Professional Services"):
        self.module = PDFReportModule(company_name=company_name)
    
    def generate_batch(self, customers_data_list):
        """
        Generate PDFs for multiple customers
        
        Args:
            customers_data_list: List of customer data dictionaries
            
        Returns:
            List of tuples: [(customer_name, pdf_path, success, error), ...]
        """
        results = []
        
        for customer_data in customers_data_list:
            customer_name = customer_data.get('customer_name', 'Unknown')
            pdf_path, success, error = self.module.generate_report(customer_data)
            results.append((customer_name, pdf_path, success, error))
        
        return results
    
    def show_batch_ui(self, customers_data_list):
        """Display batch PDF generation UI"""
        st.subheader("📚 Batch PDF Generation")
        
        st.write(f"**Total Customers:** {len(customers_data_list)}")
        
        if st.button("🚀 Generate All PDFs"):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            results = []
            for idx, customer_data in enumerate(customers_data_list):
                customer_name = customer_data.get('customer_name', 'Unknown')
                status_text.text(f"Generating PDF {idx + 1}/{len(customers_data_list)}: {customer_name}")
                
                pdf_path, success, error = self.module.generate_report(customer_data)
                results.append((customer_name, pdf_path, success, error))
                
                progress_bar.progress((idx + 1) / len(customers_data_list))
            
            status_text.empty()
            progress_bar.empty()
            
            # Show results
            success_count = sum(1 for _, _, success, _ in results if success)
            st.success(f"✅ Generated {success_count}/{len(results)} PDFs successfully")
            
            # Show details
            with st.expander("📋 View Details"):
                for customer_name, pdf_path, success, error in results:
                    if success:
                        st.success(f"✅ {customer_name}: `{pdf_path}`")
                    else:
                        st.error(f"❌ {customer_name}: {error}")


if __name__ == "__main__":
    # Example usage
    print("PDF Report Module - Use by importing into your Streamlit app")
    print("\nExample:")
    print("  from pdf_report_module import create_pdf_generator_widget")
    print("  create_pdf_generator_widget(customer_data)")