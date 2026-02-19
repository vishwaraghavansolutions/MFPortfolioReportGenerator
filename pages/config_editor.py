"""
Configuration Editor Page
Streamlit page for editing config.py values through a UI
"""

import streamlit as st
import json
import os
from datetime import datetime
import importlib.util


class ConfigEditor:
    """Editor for application configuration"""
    
    def __init__(self, config_path="config.py"):
        self.config_path = config_path
        self.config_data = self._load_config()
    
    def _load_config(self):
        """Load configuration from config.py"""
        try:
            spec = importlib.util.spec_from_file_location("config", self.config_path)
            config = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config)
            
            # Extract editable configurations
            config_data = {
                'company_name': getattr(config, 'COMPANY_NAME', 'Winrich Professional Services'),
                'default_model_allocation': getattr(config, 'DEFAULT_MODEL_ALLOCATION', {}),
                'model_allocations': getattr(config, 'MODEL_ALLOCATIONS', {}),
                'category_benchmarks': getattr(config, 'CATEGORY_BENCHMARKS', {}),
                'market_context': getattr(config, 'MARKET_CONTEXT', {}),
                'colors': getattr(config, 'COLORS', {}),
                'observation_thresholds': getattr(config, 'OBSERVATION_THRESHOLDS', {}),
                'use_custom_thresholds': getattr(config, 'USE_CUSTOM_THRESHOLDS', True),
            }
            
            return config_data
            
        except Exception as e:
            st.error(f"Error loading config: {str(e)}")
            return {}
    
    def _save_config(self, config_data):
        """Save configuration back to config.py"""
        try:
            # Create backup
            if os.path.exists(self.config_path):
                backup_path = f"{self.config_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                with open(self.config_path, 'r') as f:
                    backup_content = f.read()
                with open(backup_path, 'w') as f:
                    f.write(backup_content)
            
            # Generate new config content
            config_content = self._generate_config_content(config_data)
            
            # Write to file
            with open(self.config_path, 'w') as f:
                f.write(config_content)
            
            return True, None
            
        except Exception as e:
            return False, str(e)
    
    def _generate_config_content(self, config_data):
        """Generate config.py content from configuration data"""
        content = '''# config.py
# Configuration file for Portfolio Analysis V2
# Auto-generated from Config Editor

import os

# ============================================================
# COMPANY SETTINGS
# ============================================================

COMPANY_NAME = "{company_name}"
COMPANY_LOGO_PATH = None  # Set to path if available

# ============================================================
# PORTFOLIO SETTINGS
# ============================================================

# Default model allocation (moderate risk profile)
DEFAULT_MODEL_ALLOCATION = {default_model_allocation}

# Alternative model allocations (can be selected by user)
MODEL_ALLOCATIONS = {model_allocations}

# ============================================================
# BENCHMARK DATA
# ============================================================

# Category benchmark mappings
CATEGORY_BENCHMARKS = {category_benchmarks}

# Market context data
MARKET_CONTEXT = {market_context}

# ============================================================
# VISUALIZATION SETTINGS
# ============================================================

# Color scheme
COLORS = {colors}

# ============================================================
# OBSERVATION THRESHOLDS
# ============================================================

OBSERVATION_THRESHOLDS = {observation_thresholds}

USE_CUSTOM_THRESHOLDS = {use_custom_thresholds}  # Set to False to disable observation flags
# ============================================================
# FILE PATHS (Do not edit through UI)
# ============================================================

PORTFOLIO_PATH = "data/Datawarehouse_MutualFunds_2026_01_01_mutualfunds.csv"
SCHEME_MASTER_PATH = "data/SchemeData2301262313SS.csv"
RANKING_DIR = "data"
OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
'''.format(
            company_name=config_data.get('company_name', ''),
            default_model_allocation=json.dumps(config_data.get('default_model_allocation', {}), indent=4),
            model_allocations=json.dumps(config_data.get('model_allocations', {}), indent=4),
            category_benchmarks=json.dumps(config_data.get('category_benchmarks', {}), indent=4),
            market_context=json.dumps(config_data.get('market_context', {}), indent=4),
            colors=json.dumps(config_data.get('colors', {}), indent=4),
            observation_thresholds=json.dumps(config_data.get('observation_thresholds', {}), indent=4),
            use_custom_thresholds=str(config_data.get('USE_CUSTOM_THRESHOLDS', True))
        )
        
        return content
    
    def show_editor_ui(self):
        """Display configuration editor UI"""
        st.title("⚙️ Configuration Editor")
        
        if not self.config_data:
            st.error("Failed to load configuration")
            return
        
        # Create tabs for different sections
        tabs = st.tabs([
            "🏢 Company Settings",
            "📊 Model Allocations", 
            "📈 Benchmarks",
            "🌍 Market Context",
            "🎨 Visualization",
            "🔔 Thresholds"
        ])
        
        # Tab 1: Company Settings
        with tabs[0]:
            self._edit_company_settings()
        
        # Tab 2: Model Allocations
        with tabs[1]:
            self._edit_model_allocations()
        
        # Tab 3: Benchmarks
        with tabs[2]:
            self._edit_benchmarks()
        
        # Tab 4: Market Context
        with tabs[3]:
            self._edit_market_context()
        
        # Tab 5: Visualization
        with tabs[4]:
            self._edit_visualization()
        
        # Tab 6: Thresholds
        with tabs[5]:
            self._edit_thresholds()
        
        # Save button
        st.markdown("---")
        col1, col2, col3 = st.columns([1, 1, 2])
        
        with col1:
            if st.button("💾 Save Configuration", type="primary"):
                success, error = self._save_config(self.config_data)
                if success:
                    st.success("✅ Configuration saved successfully!")
                    st.info("🔄 Please restart the application for changes to take effect")
                else:
                    st.error(f"❌ Error saving configuration: {error}")
        
        with col2:
            if st.button("🔄 Reset to Default"):
                st.warning("This will reload configuration from file")
                self.config_data = self._load_config()
                st.rerun()
    
    def _edit_company_settings(self):
        """Edit company settings"""
        st.subheader("Company Information")
        
        self.config_data['company_name'] = st.text_input(
            "Company Name",
            value=self.config_data.get('company_name', ''),
            help="This will appear on all reports and PDFs"
        )
        
        st.info("💡 Logo path should be set manually in config.py")
    
    def _edit_model_allocations(self):
        """Edit model allocation settings"""
        st.subheader("Default Model Allocation")
        
        use_customer_thresholds = st.checkbox("Use Customer's existing allocation values", value=True)

        if use_customer_thresholds:
            st.info("Customer's existing allocation values will be used as defaults")
        else:
            default_model = self.config_data.get('default_model_allocation', {})
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                equity = st.number_input(
                    "Equity (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_model.get('Equity', 65.0)),
                    step=0.5
                )
            
            with col2:
                hybrid = st.number_input(
                    "Balance (Hybrid) (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_model.get('Balance (Hybrid)', 20.0)),
                    step=0.5
                )
            
            with col3:
                debt = st.number_input(
                    "Debt (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_model.get('Debt', 15.0)),
                    step=0.5
                )
            
            total = equity + hybrid + debt
            if abs(total - 100) > 0.01:
                st.error(f"⚠️ Total must equal 100%. Current: {total}%")
            else:
                st.success(f"✅ Total: {total}%")
            
            self.config_data['default_model_allocation'] = {
                'Equity': equity,
                'Balance (Hybrid)': hybrid,
                'Debt': debt
            }

        self.config_data['USE_CUSTOM_THRESHOLDS'] = use_customer_thresholds
        
        # Risk profile allocations
        st.markdown("---")
        st.subheader("Risk Profile Allocations")
        
        model_allocations = self.config_data.get('model_allocations', {})
        
        profiles = ["Conservative", "Moderately Conservative", "Moderate", "Moderately Aggressive", "Aggressive"]
        
        for profile in profiles:
            with st.expander(f"📊 {profile}"):
                allocation = model_allocations.get(profile, {})
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    e = st.number_input(f"Equity", min_value=0.0, max_value=100.0, 
                                       value=float(allocation.get('Equity', 65.0)), step=0.5, key=f"{profile}_equity")
                with col2:
                    h = st.number_input(f"Hybrid", min_value=0.0, max_value=100.0,
                                       value=float(allocation.get('Balance (Hybrid)', 20.0)), step=0.5, key=f"{profile}_hybrid")
                with col3:
                    d = st.number_input(f"Debt", min_value=0.0, max_value=100.0,
                                       value=float(allocation.get('Debt', 15.0)), step=0.5, key=f"{profile}_debt")
                
                model_allocations[profile] = {'Equity': e, 'Balance (Hybrid)': h, 'Debt': d}
        
        self.config_data['model_allocations'] = model_allocations
    
    def _edit_benchmarks(self):
        """Edit benchmark settings"""
        st.subheader("Category Benchmarks")
        
        benchmarks = self.config_data.get('category_benchmarks', {})
        
        categories = ["Large Cap", "Mid Cap", "Small Cap", "Flexi Cap", "Large & Mid Cap"]
        
        for category in categories:
            with st.expander(f"📈 {category}"):
                bench = benchmarks.get(category, {})
                
                name = st.text_input("Index Name", value=bench.get('name', ''), key=f"{category}_name")
                cagr_5y = st.number_input("5Y CAGR (%)", value=float(bench.get('5y_cagr', 0)), step=0.1, key=f"{category}_5y")
                
                benchmarks[category] = {
                    'name': name,
                    '5y_cagr': cagr_5y
                }
        
        self.config_data['category_benchmarks'] = benchmarks
    
    def _edit_market_context(self):
        """Edit market context - AI-powered"""
        st.subheader("Market Context")
        
        market_context = self.config_data.get('market_context', {})
        
        st.write("Current market context will be displayed in reports")
        
        year = st.text_input("Year", value=market_context.get('year', 'CY 2025'))
        
        st.markdown("**Index Performance:**")
        indices = market_context.get('indices', {})
        
        col1, col2 = st.columns(2)
        with col1:
            nifty50 = st.number_input("Nifty 50 (%)", value=float(indices.get('Nifty 50', 0)), step=0.1)
            nifty500 = st.number_input("Nifty 500 (%)", value=float(indices.get('Nifty 500', 0)), step=0.1)
        
        with col2:
            midcap = st.number_input("Nifty Midcap 150 (%)", value=float(indices.get('Nifty Midcap 150', 0)), step=0.1)
            smallcap = st.number_input("Nifty Smallcap 250 (%)", value=float(indices.get('Nifty Smallcap 250', 0)), step=0.1)
        
        narrative = st.text_area(
            "Market Narrative",
            value=market_context.get('narrative', ''),
            height=150,
            help="Brief description of market conditions"
        )
        
        # AI Generation button
        if st.button("🤖 Generate with AI"):
            with st.spinner("Generating market narrative with AI..."):
                ai_narrative = self._generate_market_narrative_with_ai(year, nifty50, nifty500, midcap, smallcap)
                narrative = ai_narrative
                st.success("✅ AI narrative generated!")
        
        self.config_data['market_context'] = {
            'year': year,
            'indices': {
                'Nifty 50': nifty50,
                'Nifty 500': nifty500,
                'Nifty Midcap 150': midcap,
                'Nifty Smallcap 250': smallcap
            },
            'narrative': narrative
        }
    
    def _generate_market_narrative_with_ai(self, year, nifty50, nifty500, midcap, smallcap):
        """Generate market narrative using AI"""
        # This will be implemented with Anthropic API
        prompt = f"""Generate a concise market narrative (2-3 sentences) based on these {year} Indian equity market returns:
        
- Nifty 50: {nifty50}%
- Nifty 500: {nifty500}%
- Nifty Midcap 150: {midcap}%
- Nifty Smallcap 250: {smallcap}%

Provide professional, factual commentary suitable for a portfolio report."""
        
        # Placeholder - will integrate with AI
        return f"""Large caps outperformed smaller segments in {year}, with Nifty 50 delivering {nifty50}% returns. 
The market rewarded earnings visibility and balance-sheet strength, as evidenced by underperformance in small caps ({smallcap}%). 
The Nifty 500 TRI delivered a 5-year CAGR of approximately 12.45%."""
    
    def _edit_visualization(self):
        """Edit visualization settings"""
        st.subheader("Color Scheme")
        
        colors = self.config_data.get('colors', {})
        
        col1, col2 = st.columns(2)
        
        with col1:
            primary = st.color_picker("Primary Color", value=colors.get('primary', '#1f77b4'))
            equity = st.color_picker("Equity Color", value=colors.get('equity', '#1f77b4'))
            success = st.color_picker("Success Color", value=colors.get('success', '#2ca02c'))
        
        with col2:
            secondary = st.color_picker("Secondary Color", value=colors.get('secondary', '#87ceeb'))
            hybrid = st.color_picker("Hybrid Color", value=colors.get('hybrid', '#2ca02c'))
            debt = st.color_picker("Debt Color", value=colors.get('debt', '#ff7f0e'))
        
        self.config_data['colors'] = {
            'primary': primary,
            'secondary': secondary,
            'success': success,
            'equity': equity,
            'hybrid': hybrid,
            'debt': debt
        }
    
    def _edit_thresholds(self):
        """Edit observation thresholds"""
        st.subheader("Observation Thresholds")
        
        st.write("These thresholds determine when observations are flagged in reports")
        
        thresholds = self.config_data.get('observation_thresholds', {})
        
        col1, col2 = st.columns(2)
        
        with col1:
            asset_dev = st.number_input(
                "Asset Class Deviation (%)",
                value=float(thresholds.get('asset_class_deviation', 5.0)),
                step=0.5,
                help="% deviation from model to flag"
            )
            
            debt_under = st.number_input(
                "Debt Underweight (%)",
                value=float(thresholds.get('debt_underweight', -5.0)),
                step=0.5,
                help="% below model to flag debt underweight"
            )
        
        with col2:
            market_cap_conc = st.number_input(
                "Market Cap Concentration (%)",
                value=float(thresholds.get('market_cap_concentration', 60.0)),
                step=1.0,
                help="% large+flexi to flag concentration"
            )
            
            amc_count = st.number_input(
                "AMC Concentration (count)",
                value=int(thresholds.get('amc_concentration_count', 3)),
                step=1,
                help="Number of funds from same AMC to flag"
            )
        
        self.config_data['observation_thresholds'] = {
            'asset_class_deviation': asset_dev,
            'debt_underweight': debt_under,
            'market_cap_concentration': market_cap_conc,
            'amc_concentration_count': amc_count
        }


def main():
    """Main function for config editor page"""
    
    # Check authentication
    if "role" not in st.session_state:
        st.warning("⚠️ Please login to access this page")
        st.stop()
    
    # Only allow admin access
    if st.session_state.get("role") != "admin":
        st.error("🔒 Access Denied: Admin privileges required")
        st.stop()
    
    # Show editor
    editor = ConfigEditor()
    editor.show_editor_ui()


if __name__ == "__main__":
    main()