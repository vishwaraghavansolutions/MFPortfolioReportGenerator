"""
AI Market Signals Generator
Uses Anthropic Claude API to generate market insights and narratives
"""

import os
import streamlit as st

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class AIMarketSignalsGenerator:
    """Generate market signals and narratives using Claude API"""
    
    def __init__(self):
        """Initialize AI generator with Anthropic client"""
        self.client = None
        
        if ANTHROPIC_AVAILABLE:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                try:
                    self.client = anthropic.Anthropic(api_key=api_key)
                except Exception as e:
                    st.error(f"Failed to initialize Anthropic client: {e}")
    
    def generate_market_narrative(self, market_data):
        """
        Generate market narrative from performance data
        
        Args:
            market_data: Dict with 'year' and 'indices' (dict of index: return%)
            
        Returns:
            str: Professional market narrative
        """
        if self.client:
            try:
                return self._generate_ai_narrative(market_data)
            except Exception as e:
                st.warning(f"AI generation failed: {e}. Using template.")
                return self._generate_template_narrative(market_data)
        else:
            return self._generate_template_narrative(market_data)
    
    def generate_portfolio_observations(self, portfolio_data):
        """
        Generate portfolio observations
        
        Args:
            portfolio_data: Dict with allocation percentages and models
            
        Returns:
            list: List of observation strings
        """
        if self.client:
            try:
                return self._generate_ai_observations(portfolio_data)
            except Exception as e:
                st.warning(f"AI generation failed: {e}. Using template.")
                return self._generate_template_observations(portfolio_data)
        else:
            return self._generate_template_observations(portfolio_data)
    
    def _generate_ai_narrative(self, market_data):
        """Generate narrative using Claude Sonnet 4"""
        
        indices_text = "\n".join([
            f"- {name}: {perf}%" 
            for name, perf in market_data.get('indices', {}).items()
        ])
        
        year = market_data.get('year', 'CY 2025')
        
        prompt = f"""Generate a concise, professional market narrative (2-3 sentences) for a client portfolio report based on these {year} Indian equity market returns:

{indices_text}

Requirements:
1. Highlight which segments outperformed/underperformed
2. Provide brief context on market dynamics
3. Mention 5-year CAGR perspective (~12.45%)
4. Be factual, professional, suitable for clients
5. No recommendations or opinions

Format as a flowing paragraph."""
        
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return message.content[0].text.strip()
    
    def _generate_ai_observations(self, portfolio_data):
        """Generate observations using Claude Sonnet 4"""
        
        prompt = f"""Analyze this portfolio and generate 3-5 factual observations for a client report:

Portfolio:
- Equity: {portfolio_data.get('equity_pct', 0):.2f}% (Target: {portfolio_data.get('equity_model', 65):.2f}%)
- Hybrid: {portfolio_data.get('hybrid_pct', 0):.2f}% (Target: {portfolio_data.get('hybrid_model', 20):.2f}%)
- Debt: {portfolio_data.get('debt_pct', 0):.2f}% (Target: {portfolio_data.get('debt_model', 15):.2f}%)
- AMCs: {portfolio_data.get('num_amcs', 0)}
- Max in single AMC: {portfolio_data.get('max_amc_holdings', 0)} funds

Format each as: **Category**: Description
Be factual, mention specific numbers, 1-2 sentences each."""
        
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )
        
        text = message.content[0].text.strip()
        
        # Parse observations
        observations = []
        for line in text.split('\n'):
            line = line.strip().lstrip('0123456789.- ')
            if '**' in line and ':' in line:
                observations.append(line)
        
        return observations if observations else self._generate_template_observations(portfolio_data)
    
    def _generate_template_narrative(self, market_data):
        """Template-based narrative (fallback)"""
        
        indices = market_data.get('indices', {})
        year = market_data.get('year', 'CY 2025')
        
        nifty50 = indices.get('Nifty 50', 0)
        nifty500 = indices.get('Nifty 500', 0)
        midcap = indices.get('Nifty Midcap 150', 0)
        smallcap = indices.get('Nifty Smallcap 250', 0)
        
        # Analyze trend
        if nifty50 > midcap and nifty50 > smallcap:
            trend = "Large caps outperformed smaller segments"
            context = "reflecting a market that rewarded earnings visibility and balance-sheet strength"
        elif smallcap > nifty50 > 0:
            trend = "Small caps led market performance"
            context = "as investors sought growth opportunities"
        else:
            trend = "Markets showed mixed performance"
            context = "with selective opportunities across segments"
        
        return f"""For context, Indian equity markets in {year} saw {trend}, with Nifty 50 delivering {nifty50:.2f}% returns. The market dynamics {context}. The Nifty 500 TRI delivered a 5-year CAGR of approximately 12.45% as of December 2025."""
    
    def _generate_template_observations(self, portfolio_data):
        """Template-based observations (fallback)"""
        
        observations = []
        
        # Equity analysis
        equity_pct = portfolio_data.get('equity_pct', 0)
        equity_model = portfolio_data.get('equity_model', 65)
        equity_diff = equity_pct - equity_model
        
        if abs(equity_diff) > 5:
            if equity_diff > 0:
                observations.append(
                    f"**Equity tilt**: Equity allocation ({equity_pct:.2f}%) is {equity_diff:.2f}% above the illustrative model ({equity_model:.2f}%), consistent with a growth-oriented profile."
                )
            else:
                observations.append(
                    f"**Equity underweight**: Equity allocation ({equity_pct:.2f}%) is {abs(equity_diff):.2f}% below the illustrative model ({equity_model:.2f}%)."
                )
        
        # Debt analysis
        debt_pct = portfolio_data.get('debt_pct', 0)
        debt_model = portfolio_data.get('debt_model', 15)
        
        if debt_pct < debt_model - 5:
            observations.append(
                f"**Debt underweight**: Debt at {debt_pct:.2f}% is notably below the model ({debt_model:.2f}%). Hybrid funds partially compensate with internal debt allocations."
            )
        
        # AMC diversification
        num_amcs = portfolio_data.get('num_amcs', 0)
        if num_amcs > 0:
            if num_amcs >= 7:
                observations.append(f"**AMC diversification**: Portfolio spread across {num_amcs} AMCs, providing strong manager diversification.")
            elif num_amcs < 5:
                observations.append(f"**AMC concentration**: Portfolio concentrated in {num_amcs} AMCs, limiting manager diversification.")
        
        # AMC overlap
        max_amc = portfolio_data.get('max_amc_holdings', 0)
        if max_amc >= 3:
            observations.append(f"**AMC overlap**: {max_amc} funds from the same AMC may create overlapping stock holdings.")
        
        return observations


class AIMarketSignalsUI:
    """UI for AI market signals"""
    
    def __init__(self):
        self.generator = AIMarketSignalsGenerator()
        self._show_status()
    
    def _show_status(self):
        """Show AI status"""
        if not ANTHROPIC_AVAILABLE:
            st.error("❌ Anthropic SDK not installed")
            st.code("pip install anthropic", language="bash")
            st.warning("Using template-based generation")
        elif not self.generator.client:
            st.warning("⚠️ ANTHROPIC_API_KEY not set")
            st.info("Set environment variable or add to .streamlit/secrets.toml")
            st.warning("Using template-based generation")
        else:
            st.success("✅ AI Ready (Claude Sonnet 4)")
    
    def show_market_narrative_generator(self):
        """Market narrative UI"""
        st.subheader("📈 Market Narrative Generator")
        
        year = st.text_input("Year", value="CY 2025")
        
        col1, col2 = st.columns(2)
        with col1:
            nifty50 = st.number_input("Nifty 50 (%)", value=10.30, step=0.1)
            nifty500 = st.number_input("Nifty 500 (%)", value=6.29, step=0.1)
        with col2:
            midcap = st.number_input("Nifty Midcap 150 (%)", value=5.09, step=0.1)
            smallcap = st.number_input("Nifty Smallcap 250 (%)", value=-7.22, step=0.1)
        
        if st.button("✨ Generate Narrative", type="primary"):
            market_data = {
                'year': year,
                'indices': {
                    'Nifty 50': nifty50,
                    'Nifty 500': nifty500,
                    'Nifty Midcap 150': midcap,
                    'Nifty Smallcap 250': smallcap
                }
            }
            
            with st.spinner("🤖 Generating with Claude..."):
                narrative = self.generator.generate_market_narrative(market_data)
                st.success("✅ Generated!")
                st.text_area("Narrative", narrative, height=150)
                st.code(narrative)
    
    def show_observation_generator(self, portfolio_data):
        """Portfolio observations UI"""
        st.subheader("🔍 Portfolio Observations")
        
        if st.button("✨ Generate Observations", type="primary"):
            with st.spinner("🤖 Analyzing with Claude..."):
                observations = self.generator.generate_portfolio_observations(portfolio_data)
                st.success(f"✅ Generated {len(observations)} observations")
                
                for idx, obs in enumerate(observations, 1):
                    st.markdown(f"{idx}. {obs}")
                
                st.code("\n".join([f"• {obs}" for obs in observations]))


def main():
    """Main page"""
    st.title("🤖 AI Market Signals")
    st.caption("Powered by Claude Sonnet 4")
    
    if "role" not in st.session_state:
        st.warning("⚠️ Please login")
        st.stop()
    
    ui = AIMarketSignalsUI()
    
    tabs = st.tabs(["📈 Market Narrative", "🔍 Observations", "ℹ️ Setup"])
    
    with tabs[0]:
        ui.show_market_narrative_generator()
    
    with tabs[1]:
        with st.form("portfolio_form"):
            col1, col2 = st.columns(2)
            with col1:
                equity_pct = st.number_input("Equity %", value=70.0)
                hybrid_pct = st.number_input("Hybrid %", value=20.0)
                debt_pct = st.number_input("Debt %", value=10.0)
            with col2:
                equity_model = st.number_input("Equity Model %", value=65.0)
                hybrid_model = st.number_input("Hybrid Model %", value=20.0)
                debt_model = st.number_input("Debt Model %", value=15.0)
            
            num_amcs = st.number_input("AMCs", value=8, step=1)
            max_amc = st.number_input("Max in AMC", value=3, step=1)
            
            if st.form_submit_button("Analyze"):
                portfolio_data = {
                    'equity_pct': equity_pct, 'equity_model': equity_model,
                    'hybrid_pct': hybrid_pct, 'hybrid_model': hybrid_model,
                    'debt_pct': debt_pct, 'debt_model': debt_model,
                    'num_amcs': num_amcs, 'max_amc_holdings': max_amc
                }
                ui.show_observation_generator(portfolio_data)
    
    with tabs[2]:
        st.markdown("""
### Setup

**Install:**
```bash
pip install anthropic
```

**Set API Key:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

**Get Key:** https://console.anthropic.com/

**Status:**
        """)
        
        if ANTHROPIC_AVAILABLE:
            st.success("✅ SDK installed")
        else:
            st.error("❌ SDK not installed")
        
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            st.success(f"✅ API Key: {api_key[:10]}...{api_key[-4:]}")
        else:
            st.error("❌ API Key not set")


if __name__ == "__main__":
    main()