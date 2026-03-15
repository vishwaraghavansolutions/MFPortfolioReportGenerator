"""
agents/

Public API
──────────
    from agents import MutualFundBenchmarkAgent, IndexAgent

    mf_agent    = MutualFundBenchmarkAgent()
    index_agent = IndexAgent()
"""
from .mf_benchmark_agent import MutualFundBenchmarkAgent
from .index_agent        import IndexAgent

__all__ = ["MutualFundBenchmarkAgent", "IndexAgent"]