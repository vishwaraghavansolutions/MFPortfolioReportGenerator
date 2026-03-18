"""
agents/

Public API
──────────
    from agents import MutualFundBenchmarkAgent, IndexAgent, NSEBenchmarkAgent

    mf_agent    = MutualFundBenchmarkAgent()
    index_agent = IndexAgent()
    nse_agent   = NSEBenchmarkAgent()
"""
from .mf_benchmark_agent   import MutualFundBenchmarkAgent
from .index_agent          import IndexAgent
from .nse_benchmark_agent  import NSEBenchmarkAgent
from .angelone_cdp_agent   import AngelOneCDPAgent

__all__ = ["MutualFundBenchmarkAgent", "IndexAgent", "NSEBenchmarkAgent", "AngelOneCDPAgent"]