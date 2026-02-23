import pandas as pd
import numpy as np

# -----------------------------
# Utility: Ensure unique columns
# -----------------------------
def make_unique(columns):
    seen = {}
    new_cols = []
    for col in columns:
        col = str(col).strip()
        if col == "" or col.lower() in ("nan", "none"):
            col = "Unnamed"
        if col not in seen:
            seen[col] = 0
            new_cols.append(col)
        else:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
    return new_cols


# -----------------------------
# Combine Title + Subtitle rows
# -----------------------------
def combine_title_subtitle(row1, row2):
    combined = []
    for a, b in zip(row1, row2):
        a = str(a).strip()
        b = str(b).strip()

        if a == "" or a.lower() in ("nan", "none"):
            a = "Unnamed"
        if b == "" or b.lower() in ("nan", "none"):
            b = "Unnamed"

        combined.append(f"{a}_{b}")
    return combined

def rename_block(columns, block_keyword, labels):
    """
    Find the column containing block_keyword and rename the next len(labels) columns.
    """
    cols = list(columns)

    # Find block header
    start = next((i for i, c in enumerate(cols) if block_keyword.lower() in str(c).lower()), None)
    if start is None:
        return cols

    # Rename next N columns
    for offset, label in enumerate(labels, start=1):
        idx = start + offset
        if idx < len(cols):
            cols[idx] = label

    return cols

# -----------------------------
# Fix Returns block
# -----------------------------
RETURN_PERIODS = ["1M", "3M", "1Yr", "3Yr", "5Yr"]
def fix_returns_block(columns):
    labels = ["Returns_1M", "Returns_3M", "Returns_1Yr", "Returns_3Yr", "Returns_5Yr"]
    cols = list(columns)

    # Find the block header
    start = next((i for i, c in enumerate(cols) if "Returns" in str(c)), None)
    if start is None:
        return cols

    # Rename next 5 columns
    for offset, label in enumerate(labels, start=1):
        idx = start + offset
        if idx < len(cols):
            cols[idx] = label

    return cols


def fix_vol_block(columns):
    labels = ["Volatility(%)", "Beta", "Correlation", "R2"]
    cols = list(columns)

    start = next((i for i, c in enumerate(cols) if "Volatility" in str(c)), None)
    if start is None:
        return cols

    for offset, label in enumerate(labels, start=1):
        idx = start + offset
        if idx < len(cols):
            cols[idx] = label

    return cols


def fix_pe_pb_div_block(columns):
    labels = ["P/E", "P/B", "DividendYield"]
    cols = list(columns)

    start = next((i for i, c in enumerate(cols)
                  if "P/E" in str(c) or "Dividend" in str(c)), None)
    if start is None:
        return cols

    for offset, label in enumerate(labels, start=1):
        idx = start + offset
        if idx < len(cols):
            cols[idx] = label

    return cols

# -----------------------------
# Main header normalizer
# -----------------------------
def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    cleaned_frames = []

    for _, group in df.groupby("__page__"):
        group = group.reset_index(drop=True)

        # Detect header row
        header_idx = None
        for i, row in group.iterrows():
            if "Index" in " ".join(str(x) for x in row.values):
                header_idx = i
                break
        
        # Promote header rows
        if header_idx is None:
            title_row = group.iloc[0]
            subtitle_row = group.iloc[1]
            group.columns = combine_title_subtitle(title_row, subtitle_row)
            group = group.iloc[2:]
        else:
            title_row = group.iloc[header_idx]
            subtitle_row = group.iloc[header_idx + 1]
            group.columns = combine_title_subtitle(title_row, subtitle_row)
            group = group.iloc[header_idx + 2:]

        # --- FIX BLOCKS BY POSITION ---
        cols = list(group.columns)

        # Returns block
        cols = rename_block(cols, "Returns", 
            ["Returns_1M", "Returns_3M", "Returns_1Yr", "Returns_3Yr", "Returns_5Yr"]
        )

        # Volatility block
        cols = rename_block(cols, "Volatility", 
            ["Volatility(%)", "Beta", "Correlation", "R2"]
        )

        # P/E P/B Dividend block
        cols = rename_block(cols, "P/E", 
            ["P/E", "P/B", "DividendYield"]
        )

        group.columns = make_unique(cols)

        cleaned_frames.append(group)

    final_df = pd.concat(cleaned_frames, ignore_index=True)

    # Drop empty columns ONLY NOW
    #final_df = final_df.loc[:, final_df.notna().any()]
    final_df.columns = make_unique(final_df.columns)

    return final_df