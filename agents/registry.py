# agents/registry.py
from typing import Dict, Optional

from agents.base               import Agent, AgentResponse, AgentStatus
from agents.mf_portfolio_agent import MFPortfolioAgent


# ── Skill metadata registry ────────────────────────────────────────────────────
# Each entry maps skill_id → UI metadata + the agent class that owns it.
# This is the single source of truth for the orchestrator UI (sidebar skill
# cards, NL parser, task dashboard) and for AgentRegistry routing.

SKILL_REGISTRY: Dict[str, dict] = {

    # ── File Operations ────────────────────────────────────────────────────────
    "read_file": {
        "name":        "Read File",
        "category":    "File Operations",
        "icon":        "📄",
        "description": "Read and display content of a file",
        "params":      ["file_path"],
        "agent":       "FileAgent",
        "example":     "Read the file report.csv",
    },
    "write_file": {
        "name":        "Write File",
        "category":    "File Operations",
        "icon":        "✏️",
        "description": "Write text content to a file",
        "params":      ["file_path", "content"],
        "agent":       "FileAgent",
        "example":     "Write 'Hello World' to output.txt",
    },
    "convert_file": {
        "name":        "Convert File",
        "category":    "File Operations",
        "icon":        "🔄",
        "description": "Convert a file from one format to another",
        "params":      ["source_path", "target_format"],
        "agent":       "FileAgent",
        "example":     "Convert data.csv to JSON format",
    },

    # ── Web & Scraping ─────────────────────────────────────────────────────────
    "web_search": {
        "name":        "Web Search",
        "category":    "Web & Scraping",
        "icon":        "🔍",
        "description": "Search the web for information on a topic",
        "params":      ["query"],
        "agent":       "WebAgent",
        "example":     "Search for latest AI news",
    },
    "scrape_url": {
        "name":        "Scrape URL",
        "category":    "Web & Scraping",
        "icon":        "🌐",
        "description": "Scrape and extract content from a webpage",
        "params":      ["url"],
        "agent":       "WebAgent",
        "example":     "Scrape https://example.com",
    },

    # ── Email & Notifications ──────────────────────────────────────────────────
    "send_email": {
        "name":        "Send Email",
        "category":    "Email & Notifications",
        "icon":        "📧",
        "description": "Send an email to a recipient",
        "params":      ["to", "subject", "body"],
        "agent":       "NotificationAgent",
        "example":     "Send email to john@example.com with subject 'Report Ready'",
    },
    "send_notification": {
        "name":        "Send Notification",
        "category":    "Email & Notifications",
        "icon":        "🔔",
        "description": "Send a system notification or alert",
        "params":      ["title", "message"],
        "agent":       "NotificationAgent",
        "example":     "Notify me when task is complete",
    },

    # ── Data Analysis ──────────────────────────────────────────────────────────
    "analyze_data": {
        "name":        "Analyze Data",
        "category":    "Data Analysis",
        "icon":        "📊",
        "description": "Perform statistical analysis on a dataset",
        "params":      ["file_path", "analysis_type"],
        "agent":       "DataAgent",
        "example":     "Analyze sales.csv and show summary statistics",
    },
    "generate_chart": {
        "name":        "Generate Chart",
        "category":    "Data Analysis",
        "icon":        "📈",
        "description": "Create a chart or visualization from data",
        "params":      ["file_path", "chart_type", "x_col", "y_col"],
        "agent":       "DataAgent",
        "example":     "Generate a bar chart of revenue by month from data.csv",
    },
    "summarize_report": {
        "name":        "Summarize Report",
        "category":    "Data Analysis",
        "icon":        "📋",
        "description": "AI-powered summary of a document or dataset",
        "params":      ["file_path"],
        "agent":       "DataAgent",
        "example":     "Summarize quarterly_report.pdf",
    },

    # ── MF Portfolio ───────────────────────────────────────────────────────────
    "list_customers": {
        "name":        "List Customers",
        "category":    "MF Portfolio",
        "icon":        "👥",
        "description": "Read the data-warehouse CSV and return all unique customer names",
        "params":      ["csv_path"],
        "agent":       "MFPortfolioAgent",
        "example":     "List all customers in the mutual fund data",
    },
    "load_portfolio_data": {
        "name":        "Load Portfolio",
        "category":    "MF Portfolio",
        "icon":        "📂",
        "description": "Load and enrich holdings for a single customer from the CSV",
        "params":      ["csv_path", "customer_name"],
        "agent":       "MFPortfolioAgent",
        "example":     "Load portfolio for Abhinav Kapoor",
    },
    "calculate_metrics": {
        "name":        "Calculate Metrics",
        "category":    "MF Portfolio",
        "icon":        "🧮",
        "description": "Compute allocation %, fund performance lists and AMC concentration",
        "params":      ["customer_df"],
        "agent":       "MFPortfolioAgent",
        "example":     "Calculate portfolio metrics for loaded customer",
    },
    "load_qoq_and_benchmarks": {
        "name":        "Load QoQ & Benchmarks",
        "category":    "MF Portfolio",
        "icon":        "📉",
        "description": "Fetch last-4-quarter GCS data, merge benchmark index returns, "
                       "and build the portfolio growth trend in one step",
        "params":      ["customer_name", "equity_funds", "bucket_name", "parquet_dir"],
        "agent":       "MFPortfolioAgent",
        "example":     "Load QoQ and benchmark data for Abhinav Kapoor",
    },
    "generate_ai_commentary": {
        "name":        "Generate AI Commentary",
        "category":    "MF Portfolio",
        "icon":        "🤖",
        "description": "Use Claude to write narrative commentary on the portfolio",
        "params":      ["portfolio_data"],
        "agent":       "MFPortfolioAgent",
        "example":     "Generate AI commentary for the portfolio report",
    },
    "generate_pdf_report": {
        "name":        "Generate PDF Report",
        "category":    "MF Portfolio",
        "icon":        "🚀",
        "description": "Assemble all portfolio data and render the full PDF report",
        "params":      ["customer_df", "metrics", "quarterly_returns",
                        "quarter_labels", "blended_return", "portfolio_trend",
                        "model_allocation", "company_name"],
        "agent":       "MFPortfolioAgent",
        "example":     "Generate PDF report for Abhinav Kapoor",
    },
}


# ── Agent Registry ─────────────────────────────────────────────────────────────
class AgentRegistry:
    """
    Instantiates every agent once and routes skill_id → agent.run().
    All agents are stateless; it is safe to keep a single instance per process.
    """

    def __init__(self):
        self._agents: Dict[str, Agent] = {
            "MFPortfolioAgent":  MFPortfolioAgent(),
        }

    # ── Routing ────────────────────────────────────────────────────────────────

    def get_agent_for_skill(self, skill_id: str) -> Optional[Agent]:
        """Return the agent instance responsible for *skill_id*, or None."""
        meta = SKILL_REGISTRY.get(skill_id)
        if not meta:
            return None
        return self._agents.get(meta["agent"])

    def execute(self, skill_id: str, params: dict) -> AgentResponse:
        """Look up the correct agent and call agent.run(skill_id, params)."""
        agent = self.get_agent_for_skill(skill_id)
        if not agent:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"No agent registered for skill '{skill_id}'",
            )
        return agent.run(skill_id, params)

    # ── Introspection ──────────────────────────────────────────────────────────

    def all_agents(self) -> Dict[str, Agent]:
        return self._agents

    def skills_for_agent(self, agent_name: str) -> Dict[str, dict]:
        """Return all skill metadata entries owned by *agent_name*."""
        return {
            sid: meta
            for sid, meta in SKILL_REGISTRY.items()
            if meta["agent"] == agent_name
        }

    def skills_by_category(self) -> Dict[str, Dict[str, dict]]:
        """Return skill metadata grouped by category."""
        grouped: Dict[str, Dict[str, dict]] = {}
        for sid, meta in SKILL_REGISTRY.items():
            grouped.setdefault(meta["category"], {})[sid] = meta
        return grouped


# ── Module-level singleton ─────────────────────────────────────────────────────
agent_registry = AgentRegistry()