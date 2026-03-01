import pandas as pd
from pathlib import Path

# ── Quarter → (year, month) mapping ──────────────────────────────────────────
_QUARTER_MONTH_MAP = {
    'Q3_2025':  (2025, 3),    # Q4 FY25 → March 2025
    'Q6_2025':  (2025, 6),    # Q1 FY26 → June 2025
    'Q9_2025':  (2025, 9),    # Q2 FY26 → September 2025
    'Q12_2025': (2025, 12),   # Q3 FY26 → December 2025
    'Q3_2024':  (2024, 3),
    'Q6_2024':  (2024, 6),
    'Q9_2024':  (2024, 9),
    'Q12_2024': (2024, 12),
}


def load_benchmark_returns(parquet_path: str) -> pd.DataFrame:
    """
    Load the index_dashboard parquet and return a clean DataFrame
    indexed by (index_name, year, month).
    """
    df = pd.read_parquet(parquet_path)
    df['index_name'] = df['index_name'].astype(str).str.strip()
    df['year']       = pd.to_numeric(df['year'],  errors='coerce').astype('Int64')
    df['month']      = pd.to_numeric(df['month'], errors='coerce').astype('Int64')
    return df


def get_benchmark_quarterly_returns(
    benchmark_df: pd.DataFrame,
    index_name:   str,
    quarter_keys: list[str],   # e.g. ['Q6_2025', 'Q9_2025', 'Q12_2025']
) -> dict:
    """
    Look up return_1m for each quarter key from the parquet.
    Returns {q0: val, q1: val, ..., ttm: None}

    Uses return_1m as the single-month return representing that quarter snapshot.
    TTM is left empty (None) as requested.
    """
    returns = {}

    for i, qkey in enumerate(quarter_keys):
        year_month = _QUARTER_MONTH_MAP.get(qkey)
        if not year_month:
            returns[f'q{i}'] = None
            continue

        year, month = year_month
        row = benchmark_df[
            (benchmark_df['index_name'].str.lower() == index_name.lower()) &
            (benchmark_df['year']  == year) &
            (benchmark_df['month'] == month)
        ]

        if row.empty:
            # Try fuzzy match — index names sometimes differ slightly
            mask = (
                benchmark_df['index_name'].str.lower().str.contains(
                    index_name.lower().split()[0], na=False)  # match first word
                & (benchmark_df['year']  == year)
                & (benchmark_df['month'] == month)
            )
            row = benchmark_df[mask]

        returns[f'q{i}'] = float(row['return_1m'].iloc[0]) if not row.empty else None

    returns['ttm'] = None   # leave TTM empty as requested
    return returns


def build_quarterly_returns_with_benchmarks(
    portfolio_data:  dict,
    benchmark_df:    pd.DataFrame,
    quarter_keys:    list[str],   # sorted oldest → newest, e.g. ['Q6_2025','Q9_2025','Q12_2025']
) -> list[dict]:
    """
    Rebuild quarterly_returns with benchmark rows interleaved after each fund.

    Parameters
    ----------
    portfolio_data : dict   — full portfolio_data with equity_funds, quarterly_returns
    benchmark_df   : pd.DataFrame — loaded from index_dashboard.parquet
    quarter_keys   : list[str]   — sorted quarter keys matching quarter_labels order

    Returns
    -------
    list[dict] — enriched quarterly_returns ready for portfolio_data['quarterly_returns']
    """

    # Build fund name → benchmark_index lookup from equity_funds
    bench_index_lookup: dict[str, str] = {}
    for f in portfolio_data.get('equity_funds', []):
        if f.get('benchmark_index'):
            bench_index_lookup[f['name']] = f['benchmark_index']
    for f in portfolio_data.get('hybrid_funds', []):
        if f.get('benchmark_index'):
            bench_index_lookup[f['name']] = f['benchmark_index']

    def _find_benchmark_index(fund_name: str) -> str | None:
        """Match fund name to benchmark_index via equity_funds lookup."""
        for eq_name, b_index in bench_index_lookup.items():
            n1 = fund_name[:35].lower()
            n2 = eq_name[:35].lower()
            if n1 in n2 or n2 in n1:
                return b_index
        return None

    enriched = []
    for entry in portfolio_data.get('quarterly_returns', []):
        if entry.get('is_benchmark'):
            continue   # drop old benchmark rows — rebuilding fresh

        enriched.append(entry)   # fund row unchanged

        b_index = _find_benchmark_index(entry.get('name', ''))
        if not b_index:
            continue   # no benchmark found for this fund — skip

        bench_returns = get_benchmark_quarterly_returns(
            benchmark_df, b_index, quarter_keys)

        enriched.append({
            'name':         b_index,
            'is_benchmark': True,
            'returns':      bench_returns,
        })

    return enriched