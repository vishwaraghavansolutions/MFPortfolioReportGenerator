"""
scripts/build_benchmark_master.py
════════════════════════════════════════════════════════════════════════════════
Walks all three agent skills across every fund in India and stores the result
as a CSV, then uploads it to GCS winrich_shared/master/mf_benchmark_map.csv.

Flow
────
  Step 1  get_amc_list        → list of all AMC names
  Step 2  get_funds_by_amc    → for every AMC, collect all scheme codes
  Step 3  get_fund_benchmark  → for every scheme code, fetch meta + benchmark
  Step 4  save as  data/mf_benchmark_map.csv
  Step 5  upload to gs://winrich_shared/master/mf_benchmark_map.csv

Performance
───────────
  ~16 000 funds × 1 HTTP call each.  We use a ThreadPoolExecutor so many
  calls run in parallel (default: 20 workers).

Resilience
──────────
  A JSON checkpoint file (data/checkpoint.json) stores already-fetched scheme
  codes.  If the run is interrupted you can re-run and it will skip finished
  codes rather than starting from scratch.

Usage
─────
  python scripts/build_benchmark_master.py                  # full run, 20 workers
  python scripts/build_benchmark_master.py --workers 40     # more parallelism
  python scripts/build_benchmark_master.py --limit 200      # quick smoke-test
  python scripts/build_benchmark_master.py --resume         # skip already-done codes
  python scripts/build_benchmark_master.py --rebuild        # wipe checkpoint + CSV, start fresh
  python scripts/build_benchmark_master.py --via-agent      # delegate entirely to agent skill
  python scripts/build_benchmark_master.py --no-upload      # skip GCS upload
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# tqdm is optional – fall back to a plain counter if not installed
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# project imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.mf_benchmark_agent import (
    MutualFundBenchmarkAgent,
    _all_schemes,
    _get,
    _derive_benchmark,
    SCHEME_URL,
    _DEFAULT_MASTER_BUCKET,
    _DEFAULT_MASTER_PREFIX,
    _DEFAULT_MASTER_FILENAME,
    _get_gcs_agent,
)

# ── constants ─────────────────────────────────────────────────────────────────
DATA_DIR        = Path("data")
OUT_CSV         = DATA_DIR / "mf_benchmark_map.csv"
CHECKPOINT_FILE = DATA_DIR / "checkpoint.json"

COLUMNS = [
    "scheme_code",
    "scheme_name",
    "fund_house",
    "scheme_type",
    "scheme_category",
    "benchmark",
    "latest_nav",
    "nav_date",
    "fetched_at",
]


# ── checkpoint helpers ────────────────────────────────────────────────────────

def load_checkpoint() -> Dict[int, Dict]:
    """Return {scheme_code: row_dict} for previously fetched codes."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    return {}


def save_checkpoint(done: Dict[int, Dict]) -> None:
    CHECKPOINT_FILE.write_text(json.dumps(done, indent=2, ensure_ascii=False))


# ── per-scheme fetch ──────────────────────────────────────────────────────────

def fetch_scheme(code: int) -> Optional[Dict[str, Any]]:
    """
    Fetch meta + derive benchmark for one scheme code.
    Returns a row dict on success, None on failure.
    """
    try:
        data = _get(SCHEME_URL.format(code=code))
        if data.get("status") != "SUCCESS":
            return None

        meta      = data.get("meta", {})
        nav_entry = data.get("data", [{}])[0]
        category  = meta.get("scheme_category", "")

        return {
            "scheme_code"    : meta.get("scheme_code", code),
            "scheme_name"    : meta.get("scheme_name", ""),
            "fund_house"     : meta.get("fund_house", ""),
            "scheme_type"    : meta.get("scheme_type", ""),
            "scheme_category": category,
            "benchmark"      : _derive_benchmark(category),
            "latest_nav"     : nav_entry.get("nav"),
            "nav_date"       : nav_entry.get("date"),
            "fetched_at"     : datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        print(f"\n  warning: scheme {code} failed: {exc}", file=sys.stderr)
        return None


# ── save + upload helpers ─────────────────────────────────────────────────────

def save_csv(rows: List[Dict], path: Path) -> None:
    """Save rows as CSV."""
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["scheme_code"] = pd.to_numeric(df["scheme_code"], errors="coerce").astype("Int64")
    df["latest_nav"]  = pd.to_numeric(df["latest_nav"],  errors="coerce")
    df.to_csv(path, index=False)
    print(f"  CSV saved -> {path}  ({len(df):,} rows, {path.stat().st_size/1024:.1f} KB)")


def clear_state() -> None:
    """Delete the checkpoint and any existing CSV so the next run is fully fresh."""
    for path in (CHECKPOINT_FILE, OUT_CSV):
        if path.exists():
            path.unlink()
            print(f"  Removed {path}")


def run_via_agent(limit: Optional[int], no_upload: bool) -> None:
    """
    Delegate the entire rebuild to agent.run('rebuild_benchmark_master').
    Simpler than the manual pipeline but lacks checkpoint/resume support.
    """
    from agents.base import AgentStatus
    DATA_DIR.mkdir(exist_ok=True)
    agent  = MutualFundBenchmarkAgent()
    params: Dict[str, Any] = {"mapping_file": str(OUT_CSV)}
    if limit:
        params["max_schemes"] = limit
    if no_upload:
        # The agent always uploads; override by monkey-patching the GCS call
        # is complex, so we just warn the user.
        print("  NOTE: --no-upload is ignored in --via-agent mode "
              "(agent always uploads to GCS).")

    print("\n  Calling agent.run('rebuild_benchmark_master') ...")
    resp = agent.run("rebuild_benchmark_master", params)

    if resp.status == AgentStatus.SUCCESS:
        print(f"\n  Done!  {resp.output['schemes_written']:,} schemes written")
        print(f"  Local : {resp.output['mapping_file']}")
        print(f"  GCS   : {resp.output['gcs_uri']}")
        if resp.output.get("errors"):
            print(f"  Fetch errors: {resp.output['errors']} "
                  "(see metadata for sample_errors)")
    else:
        sys.exit(f"\nERROR: rebuild_benchmark_master failed -> {resp.error}")


def upload_to_gcs(path: Path) -> str:
    """Upload the CSV to GCS. Returns the gcs_uri."""
    from agents.base import AgentStatus
    resp = _get_gcs_agent().run("upload_csv", {
        "file_path":   str(path),
        "filename":    _DEFAULT_MASTER_FILENAME,
        "bucket_name": _DEFAULT_MASTER_BUCKET,
        "prefix":      _DEFAULT_MASTER_PREFIX,
    })
    if resp.status != AgentStatus.SUCCESS:
        raise RuntimeError(resp.error)
    return resp.output["gcs_uri"]


# ── simple progress fallback ──────────────────────────────────────────────────

class PlainProgress:
    """Minimal tqdm-compatible progress reporter used when tqdm is absent."""
    def __init__(self, total: int):
        self.total  = total
        self.n      = 0
        self._start = time.time()

    def update(self, n: int = 1):
        self.n += n
        pct     = self.n / self.total * 100 if self.total else 0
        elapsed = time.time() - self._start
        rate    = self.n / elapsed if elapsed else 0
        eta     = (self.total - self.n) / rate if rate else 0
        print(
            f"\r  {self.n:>6}/{self.total}  ({pct:5.1f}%)  "
            f"{rate:5.1f} req/s  ETA {eta:5.0f}s   ",
            end="", flush=True,
        )

    def close(self):
        print()


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(workers: int, limit: Optional[int], resume: bool, rebuild: bool, no_upload: bool) -> None:
    DATA_DIR.mkdir(exist_ok=True)

    if rebuild:
        print("\n[0/5]  Rebuilding: clearing checkpoint and existing CSV ...")
        clear_state()

    agent = MutualFundBenchmarkAgent()

    # Step 1: AMC list
    print("\n[1/5]  Fetching AMC list ...")
    r1 = agent.run("get_amc_list_from_mfapi", {})
    if r1.status.value != "success":
        sys.exit(f"ERROR: get_amc_list failed -> {r1.error}")

    amcs = r1.output["amcs"]
    print(f"       {len(amcs)} AMCs found")

    # Step 2: collect all scheme codes from master list (single HTTP call)
    print("\n[2/5]  Collecting scheme codes for every AMC ...")
    master    = _all_schemes()
    all_codes: List[int] = []

    for amc in amcs:
        amc_lower = amc.lower()
        codes = [
            s["schemeCode"]
            for s in master
            if s.get("schemeName", "").lower().startswith(amc_lower)
        ]
        all_codes.extend(codes)

    # De-duplicate while preserving order
    all_codes = list(dict.fromkeys(all_codes))
    print(f"       {len(all_codes):,} unique scheme codes collected")

    if limit:
        all_codes = all_codes[:limit]
        print(f"       (limited to first {limit} for this run)")

    # Resume: skip already-fetched codes
    done: Dict[int, Dict] = {}
    if resume:
        done = load_checkpoint()
        skipped   = [c for c in all_codes if c in done]
        all_codes = [c for c in all_codes if c not in done]
        print(f"       Resuming: {len(skipped):,} already done, "
              f"{len(all_codes):,} remaining")

    # Step 3: parallel fetch
    print(f"\n[3/5]  Fetching benchmark data  (workers={workers}) ...")

    pbar = (tqdm(total=len(all_codes), unit="fund", ncols=80)
            if HAS_TQDM else PlainProgress(total=len(all_codes)))

    errors           = 0
    CHECKPOINT_EVERY = 500

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_scheme, code): code for code in all_codes}
        for i, future in enumerate(as_completed(futures), 1):
            code = futures[future]
            row  = future.result()
            if row:
                done[code] = row
            else:
                errors += 1
            pbar.update(1)

            if i % CHECKPOINT_EVERY == 0:
                save_checkpoint(done)

    pbar.close()
    save_checkpoint(done)
    print(f"\n       Done.  {len(done):,} succeeded, {errors:,} errors")

    # Step 4: save CSV
    rows = list(done.values())
    rows = [{col: r.get(col) for col in COLUMNS} for r in rows]
    rows.sort(key=lambda r: (r.get("fund_house") or "", r.get("scheme_name") or ""))

    print("\n[4/5]  Saving CSV ...")
    save_csv(rows, OUT_CSV)

    # Step 5: upload to GCS
    gcs_uri = "skipped (--no-upload)"
    if not no_upload:
        print(f"\n[5/5]  Uploading to GCS ...")
        try:
            gcs_uri = upload_to_gcs(OUT_CSV)
            print(f"  GCS  -> {gcs_uri}")
        except Exception as exc:
            print(f"  WARNING: GCS upload failed: {exc}", file=sys.stderr)
            print(f"  CSV is still available locally at {OUT_CSV}")
    else:
        print("\n[5/5]  GCS upload skipped (--no-upload)")

    print(f"""
+--------------------------------------------------+
|  Pipeline complete                               |
|  AMCs          : {len(amcs):<5}                          |
|  Total schemes : {len(rows):<5}                          |
|  Errors        : {errors:<5}                          |
|  CSV           : {str(OUT_CSV):<34}|
|  GCS           : {gcs_uri[:34]:<34}|
+--------------------------------------------------+
""")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch all Indian MF benchmark data, save as CSV, upload to GCS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/build_benchmark_master.py                   full run, 20 workers
  python scripts/build_benchmark_master.py --limit 100       smoke-test
  python scripts/build_benchmark_master.py --workers 40      more parallelism
  python scripts/build_benchmark_master.py --resume          skip already-done codes
  python scripts/build_benchmark_master.py --rebuild         wipe checkpoint + CSV, full fresh run
  python scripts/build_benchmark_master.py --via-agent       delegate to agent skill (no checkpoint)
  python scripts/build_benchmark_master.py --no-upload       CSV only, skip GCS
""",
    )
    parser.add_argument("--workers",   type=int, default=20,
                        help="Parallel HTTP workers (default: 20)")
    parser.add_argument("--limit",     type=int, default=None,
                        help="Only process first N schemes (testing)")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume",    action="store_true",
                      help="Skip schemes already saved in checkpoint")
    mode.add_argument("--rebuild",   action="store_true",
                      help="Delete checkpoint + existing CSV then run fully fresh")
    mode.add_argument("--via-agent", action="store_true",
                      help="Delegate entirely to agent.run('rebuild_benchmark_master')")

    parser.add_argument("--no-upload", action="store_true",
                        help="Save CSV locally but skip GCS upload")
    args = parser.parse_args()

    if args.via_agent:
        run_via_agent(limit=args.limit, no_upload=args.no_upload)
    else:
        run(
            workers=args.workers,
            limit=args.limit,
            resume=args.resume,
            rebuild=args.rebuild,
            no_upload=args.no_upload,
        )
