import pandas as pd
import numpy as np
from datetime import datetime

def _prep_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw CAS DataFrame."""
    df = df.copy()

    # ── FIX 1: strip surrounding quotes AND whitespace from column names ──────
    # Handles "'s_name'" → "s_name" (quotes added by S3/CSV loader)
    df.columns = pd.Index([str(c).strip().strip("'\"") for c in df.columns])

    # ── FIX 2: strip whitespace from key string columns ───────────────────────
    for col in ['h_name', 'c_name', 's_name', 'foliono', 'Nature',
                'Email', 'Mobile', 'ReportDate']:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip().replace('nan', '')

    # ── Numeric coercions ─────────────────────────────────────────────────────
    for col in ['InvAmt', 'TotalInvAmt', 'CurValue', 'BalUnit', 'AvgCost',
                'CurNAV', 'DivAmt', 'NotionalGain', 'ActualGain',
                'FolioXIRR', 'NatureXIRR', 'ClientXIRR',
                'NatureAbs', 'ClientAbs', 'absReturn']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # ── Date coercion ─────────────────────────────────────────────────────────
    if 'ValueDate' in df.columns:
        df['ValueDate'] = pd.to_datetime(
            df['ValueDate'], 
            format="%Y-%m-%dT%H:%M:%S.%f",
            dayfirst=True, 
            errors='coerce')

    return df

def _classify(nature: str) -> str:
    n = str(nature).lower()
    if any(x in n for x in ['equity', 'elss', 'flexi', 'large', 'mid', 'small',
                              'multi cap', 'thematic', 'sectoral', 'index']):
        return 'Equity'
    if any(x in n for x in ['hybrid', 'balanced', 'baf', 'dynamic', 'multi-asset',
                              'aggressive', 'conservative']):
        return 'Hybrid'
    if any(x in n for x in ['debt', 'liquid', 'money market', 'overnight',
                              'ultra short', 'short dur', 'corporate bond',
                              'gilt', 'credit risk', 'banking']):
        return 'Debt'
    return 'Other'


def _scheme_xirr(grp: pd.DataFrame) -> float | None:
    """NatureXIRR if available, else value-weighted mean of FolioXIRR."""
    nature_xirr = grp['NatureXIRR'].dropna()
    if not nature_xirr.empty:
        return float(nature_xirr.iloc[0])
    valid = grp[['FolioXIRR', 'CurValue']].dropna()
    if not valid.empty and valid['CurValue'].sum() > 0:
        return float(
            (valid['FolioXIRR'] * valid['CurValue']).sum()
            / valid['CurValue'].sum()
        )
    return None


def _quarter_label(key: str) -> str:
    label_map = {
        'Q3_2023':  "Q1 FY23\n(Jan–Mar '23)",
        'Q6_2023':  "Q2 FY24\n(Apr–Jun '23)",
        'Q9_2023':  "Q3 FY24\n(Jul–Sep '23)",
        'Q12_2023': "Q4 FY24\n(Oct–Dec '23)",
        'Q3_2024':  "Q1 FY24\n(Jan–Mar '24)",
        'Q6_2024':  "Q2 FY25\n(Apr–Jun '24)",
        'Q9_2024':  "Q3 FY25\n(Jul–Sep '24)",
        'Q12_2024': "Q4 FY25\n(Oct–Dec '24)",
        'Q3_2025':  "Q1 FY25\n(Jan–Mar '25)",
        'Q6_2025':  "Q2 FY25\n(Apr–Jun '25)",
        'Q9_2025':  "Q3 FY25\n(Jul–Sep '25)",
        'Q12_2025': "Q4 FY25\n(Oct–Dec '25)",
        'Q3_2026':  "Q1 FY26\n(Jan–Mar '26)",   
        'Q6_2026':  "Q2 FY26\n(Apr–Jun '26)",
        'Q9_2026':  "Q3 FY26\n(Jul–Sep '26)",
        'Q12_2026': "Q4 FY26\n(Oct–Dec '26)",
    }
    return label_map.get(key, key)


def _mask_email(e: str) -> str:
    parts = e.split('@')
    if len(parts) == 2:
        return parts[0][0] + '*' * (len(parts[0]) - 1) + '@' + parts[1]
    return e


def _mask_mobile(m: str) -> str:
    m = str(m)
    return '*' * (len(m) - 4) + m[-4:] if len(m) >= 4 else m


def build_qoq_data(quarterly_dict: dict) -> dict:
    """
    Convert raw CAS quarterly dict into portfolio_data for PortfolioPDFGenerator.

    Parameters
    ----------
    quarterly_dict : dict
        { "Q12_2024": df, "Q3_2025": df, ... }

    Returns
    -------
    dict — portfolio_data ready for generate_portfolio_report()
    """

 # ── STEP 1: Drop empty quarters, then normalise ───────────────────────────
    quarterly_dict = {
        k: _prep_df(df)
        for k, df in quarterly_dict.items()
        if isinstance(df, pd.DataFrame) and not df.empty  # ← skip Q3_2025
    }

    if not quarterly_dict:
        raise ValueError("No valid quarters found after filtering empty DataFrames.")

    # Verify 's_name' exists in every surviving quarter
    for k, df in quarterly_dict.items():
        if 's_name' not in df.columns:
            raise ValueError(
                f"Quarter '{k}' still missing 's_name' after cleaning.\n"
                f"Columns: {df.columns.tolist()}"
            )

    # ── FIX: normalise ALL DataFrames upfront before any processing ───────────
    quarterly_dict = {k: _prep_df(df) for k, df in quarterly_dict.items()}
    #print(f"Quarters after cleaning: {list(quarterly_dict.keys())}")

    # ── Pick latest quarter as primary ────────────────────────────────────────
    sorted_keys = list(quarterly_dict.keys())
    #print(f"Sorted quarters: {sorted_keys}")
    latest_key  = sorted_keys[-1]
    df          = quarterly_dict[latest_key]   # already prepped

    # ── 1. Client meta ────────────────────────────────────────────────────────
    client_name = df['h_name'].iloc[0]
    email       = df['Email'].iloc[0]       if 'Email'      in df.columns else ''
    mobile      = df['Mobile'].iloc[0]      if 'Mobile'     in df.columns else ''
    report_date = df['ReportDate'].iloc[0]  if 'ReportDate' in df.columns \
                  else datetime.now().strftime('%B %d, %Y')

    # ── 2. Classify funds ─────────────────────────────────────────────────────
    df['_class'] = df['Nature'].apply(_classify)

    # ── 3. Portfolio totals ───────────────────────────────────────────────────
    total_value = df.groupby('foliono')['CurValue'].last().sum()
    total_inv   = df.groupby('foliono')['TotalInvAmt'].last().sum()
    class_value = df.groupby('_class')['CurValue'].sum()
    alloc       = (class_value / total_value * 100).to_dict()

    client_allocation = {
        'Equity': np.float64(alloc.get('Equity', 0.0)),
        'Hybrid': np.float64(alloc.get('Hybrid', 0.0)),
        'Debt':   np.float64(alloc.get('Debt',   0.0)),
    }
    model_allocation = client_allocation.copy()

    # ── 4. Summary ────────────────────────────────────────────────────────────
    summary = {
        'Client Name':           client_name,
        'Email':                 _mask_email(email),
        'Mobile':                _mask_mobile(mobile),
        'Report Date':           report_date,
        'Total Portfolio Value': f"₹{total_value:,.2f}",
        'Total Invested':        f"₹{total_inv:,.2f}",
        'Total Funds':           str(df['s_name'].nunique()),
        'Equity Allocation':     f"{alloc.get('Equity', 0):.2f}%",
        'Hybrid Allocation':     f"{alloc.get('Hybrid', 0):.2f}%",
        'Debt Allocation':       f"{alloc.get('Debt',   0):.2f}%",
        'Number of AMCs':        str(df['c_name'].nunique()),
    }

    # ── 5. Equity funds ───────────────────────────────────────────────────────
    equity_funds = []
    for s_name, grp in df[df['_class'] == 'Equity'].groupby('s_name', sort=False):
        equity_funds.append({
            'name':                  s_name,
            'xirr':                  _scheme_xirr(grp),
            'benchmark':             None,
            'benchmark_index':       None,
            'benchmark_return_1m':   None,
            'benchmark_return_3m':   None,
            'benchmark_return_1yr':  None,
            'benchmark_return_3yr':  None,
            'benchmark_return_5yr':  None,
        })

    # ── 6. Hybrid funds ───────────────────────────────────────────────────────
    hybrid_funds = []
    for s_name, grp in df[df['_class'] == 'Hybrid'].groupby('s_name', sort=False):
        hybrid_funds.append({'name': s_name, 'xirr': _scheme_xirr(grp)})

    # ── 7. Debt funds ─────────────────────────────────────────────────────────
    debt_funds = []
    for s_name, grp in df[df['_class'] == 'Debt'].groupby('s_name', sort=False):
        debt_funds.append({'name': s_name, 'xirr': _scheme_xirr(grp)})

    # ── 8. AMC concentration ──────────────────────────────────────────────────
    amc_concentration = df.groupby('c_name')['s_name'].nunique().to_dict()

    # ── 9. QoQ returns ────────────────────────────────────────────────────────
    q_labels       = []
    quarterly_rows = []
    blended_return = {}

    if len(sorted_keys) > 1:
        q_labels = [_quarter_label(k) for k in sorted_keys]

        # Build {scheme → {q0, q1, ..., ttm}}
        scheme_q_data: dict[str, dict] = {}

        for qi, qkey in enumerate(sorted_keys):
            qdf = quarterly_dict[qkey]   # already prepped — no copy needed

            for s_name, grp in qdf.groupby('s_name', sort=False):
                if s_name not in scheme_q_data:
                    scheme_q_data[s_name] = {}
                val = grp['NatureXIRR'].dropna()
                scheme_q_data[s_name][f'q{qi}'] = float(val.iloc[0]) \
                                                   if not val.empty else None

        # TTM = ClientXIRR from latest quarter
        ttm_map: dict[str, float | None] = {}
        for s_name, grp in df.groupby('s_name', sort=False):
            val = grp['ClientXIRR'].dropna()
            ttm_map[s_name] = float(val.iloc[0]) if not val.empty else None

        for s_name, q_returns in scheme_q_data.items():
            # Fill missing quarters with 0 (fund didn't exist that quarter)
            for qi in range(len(sorted_keys)):
                q_returns.setdefault(f'q{qi}', 0.0)
            q_returns['ttm'] = ttm_map.get(s_name)
            quarterly_rows.append({
                'name':         s_name,
                'is_benchmark': False,
                'returns':      q_returns,
            })

        # Blended return — value-weighted ClientXIRR per quarter
        for qi, qkey in enumerate(sorted_keys):
            qdf = quarterly_dict[qkey]
            valid = qdf[['ClientXIRR', 'CurValue']].dropna()
            if not valid.empty and valid['CurValue'].sum() > 0:
                blended_return[f'q{qi}'] = float(
                    (valid['ClientXIRR'] * valid['CurValue']).sum()
                    / valid['CurValue'].sum()
                )
            else:
                blended_return[f'q{qi}'] = None

        # TTM blended
        valid = df[['ClientXIRR', 'CurValue']].dropna()
        blended_return['ttm'] = float(
            (valid['ClientXIRR'] * valid['CurValue']).sum()
            / valid['CurValue'].sum()
        ) if not valid.empty and valid['CurValue'].sum() > 0 else None

    # ── Assemble ──────────────────────────────────────────────────────────────
    return {
        'client_name':       client_name,
        'report_date':       report_date,
        'summary':           summary,
        'client_allocation': client_allocation,
        'model_allocation':  model_allocation,
        'equity_funds':      equity_funds,
        'hybrid_funds':      hybrid_funds,
        'debt_funds':        debt_funds,
        'amc_concentration': amc_concentration,
        'quarter_labels':    q_labels,
        'quarterly_returns': quarterly_rows,
        'blended_return':    blended_return,
    }