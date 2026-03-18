import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(threadName)s: %(message)s",
)

from agents.nse_benchmark_agent import NSEBenchmarkAgent
from agents.base import AgentStatus

agent = NSEBenchmarkAgent()

# ── Option A: Download ALL Broad Market Indices for a year ────────────────────
#resp = agent.run("download_index_data", {"year": 2024})

# ── Option B: Download a specific subset ─────────────────────────────────────
resp = agent.run("download_index_data", {
     "year":    2011,
     "indices": ["NIFTY 50", "NIFTY 200","NIFTY 100", "NIFTY 500", 
                 "NIFTY MIDCAP 150", "NIFTY MIDCAP 50", "NIFTY MIDCAP 100",
                 "NIFTY SMALLCAP 250",  "NIFTY SMALLCAP 50", "NIFTY SMALLCAP 100", "NIFTY SMALLCAP 250",
                  "NIFTY LARGEMIDCAP 250"],
    })

if resp.status == AgentStatus.SUCCESS:
    print(f"Done!  {resp.output['total_rows']} rows")
    print(f"GCS  : {resp.output['gcs_uri']}")
    print(f"Indices fetched : {resp.output['indices_fetched']}")
elif resp.status.value == "retry":           # partial success
    print(f"Partial: {resp.output['total_rows']} rows uploaded")
    print(f"GCS  : {resp.output['gcs_uri']}")
    print(f"Failed : {resp.output['indices_failed']}")
else:
    print(f"FAILED: {resp.error}")
