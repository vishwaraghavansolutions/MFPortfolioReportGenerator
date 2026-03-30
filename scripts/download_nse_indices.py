import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(threadName)s: %(message)s",
)

from agents.nse_benchmark_agent import NSEBenchmarkAgent
from agents.base import AgentStatus

# ── Year — pass as CLI arg, defaults to current year ─────────────────────────
# Usage:
#   python scripts/download_nse_indices.py        → current year
#   python scripts/download_nse_indices.py 2024   → full year 2024
today       = date.today()
year        = int(sys.argv[1]) if len(sys.argv) > 1 else today.year
start_date  = date(year, 1, 1)
end_date    = today if year == today.year else date(year, 12, 31)

print(f"Year: {year}  |  {start_date} → {end_date}")
print(f"GCS target: gs://winrich_shared/Master/{year}_Index_Master.csv")
print()

agent = NSEBenchmarkAgent()

resp = agent.run("download_index_data", {
    "year":           year,
    "start_date":     start_date.isoformat(),
    "end_date":       end_date.isoformat(),
    "indices": [
        "NIFTY 50", "NIFTY 100", "NIFTY 200", "NIFTY 500",
        "NIFTY INDIA FPI 150", "NIFTY LARGEMIDCAP 250", "NIFTY MICROCAP 250",
        "NIFTY MIDCAP 150", "NIFTY MIDCAP 50", "NIFTY MIDCAP 100", "NIFTY MIDCAP SELECT",
        "NIFTY NEXT 50",
        "NIFTY SMALLCAP 250", "NIFTY SMALLCAP 50", "NIFTY SMALLCAP 100", "NIFTY SMALLCAP 500",
        "NIFTY TOTAL MARKET",
    ],
    "output_dir":     f"data/{year}_index_master",
    "headless":       False,
    "bucket":         "winrich_shared",
    "folder":         "Master",
    "force_download": False,
})

# Skipped: "NIFTY MIDSMALLCAP 400", "NIFTY SMALLCAP 400 50:50"

if resp.status == AgentStatus.SUCCESS:
    print(f"Done!  {resp.output['total_rows']} rows")
    print(f"GCS  : {resp.output['gcs_uri']}")
    print(f"Indices fetched : {resp.output['indices_fetched']}")
elif resp.status.value == "retry":
    print(f"Partial: {resp.output['total_rows']} rows uploaded")
    print(f"GCS  : {resp.output['gcs_uri']}")
    print(f"Failed : {resp.output['indices_failed']}")
else:
    print(f"FAILED: {resp.error}")
