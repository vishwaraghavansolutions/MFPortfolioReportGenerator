"""
NSE Index Dashboard PDF Extractor
----------------------------------
Reads all PDF files from data/pdfs/ folder, extracts index data tables
(Broad Market, Sectoral, Strategy, and Thematic indices), and saves
the combined data as a Parquet file in the data/ folder.

Requirements:
    pip install pdfplumber pandas pyarrow

Usage:
    python extract_index_dashboard.py
"""

import os
import re
import pdfplumber
import pandas as pd
from pathlib import Path


# ── Column schema ─────────────────────────────────────────────────────────────
COLUMNS = [
    "index_name",
    "return_1m",
    "return_3m",
    "return_1yr",
    "return_3yr",
    "return_5yr",
    "volatility_1yr",
    "beta_1yr",
    "correlation_1yr",
    "r2_1yr",
    "pe",
    "pb",
    "dividend_yield",
]

NUMERIC_COLS = COLUMNS[1:]   # everything except index_name

# ── Section headers that appear in the PDF ────────────────────────────────────
SECTION_LABELS = {
    "Broad Market Indices",
    "Sectoral Indices",
    "Strategy Indices",
    "Thematic Indices",
}

# Rows that are purely header / meta text and must be skipped
SKIP_PATTERNS = re.compile(
    r"^(index name|returns|volatility|beta|correlation|r\^?2|p/e|p/b|dividend"
    r"|1m|3m|1 yr|3 yr|5 yr|based on|returns for|p/e,|index returns|-\s*returns"
    r"|- index|-\s*p/e)",
    re.IGNORECASE,
)


def clean_value(val: str) -> str:
    """Strip whitespace / stray characters from a cell value."""
    if val is None:
        return ""
    return val.strip().replace(",", "")


def is_data_row(row: list) -> bool:
    """
    Return True when the row looks like an actual index data row:
    - at least 13 cells
    - first cell is a non-empty string that is NOT a header keyword
    - second cell looks like a numeric value (could start with '-')
    """
    if not row or len(row) < 13:
        return False
    first = clean_value(row[0])
    if not first or SKIP_PATTERNS.match(first):
        return False
    # second column should be numeric-ish
    second = clean_value(row[1]).replace("-", "").replace(".", "")
    if not second.isdigit():
        return False
    return True


def detect_section(row: list) -> str | None:
    """Return the section name if this row is a section header, else None."""
    if not row:
        return None
    first = clean_value(row[0])
    for label in SECTION_LABELS:
        if first.lower() == label.lower():
            return label
    return None


def parse_row(row: list, section: str, source_file: str) -> dict:
    """Convert a raw table row into a structured dict."""
    cells = [clean_value(c) for c in row[:13]]
    # Pad to 13 if fewer columns were extracted
    while len(cells) < 13:
        cells.append("")

    record = {"section": section, "source_file": source_file}
    for col, val in zip(COLUMNS, cells):
        record[col] = val
    return record


def to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Cast numeric columns; '-' and empty strings become NaN."""
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col].replace({"": None, "-": None}), errors="coerce")
    return df


def extract_from_pdf(pdf_path: str) -> list[dict]:
    """Open one PDF and extract all index rows across all pages."""
    records = []
    current_section = "Unknown"

    with pdfplumber.open(pdf_path) as pdf:
        filename = Path(pdf_path).name
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    # Check for section header
                    sec = detect_section(row)
                    if sec:
                        current_section = sec
                        continue
                    # Check for data row
                    if is_data_row(row):
                        rec = parse_row(row, current_section, filename)
                        records.append(rec)

    return records


def process_all_pdfs(input_dir: str, output_dir: str) -> None:
    """
    Iterate over every PDF in input_dir, extract data, and write a single
    Parquet file to output_dir/index_dashboard.parquet.
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_path.glob("*.pdf"))
    if not pdf_files:
        print(f"[WARN] No PDF files found in: {input_path.resolve()}")
        return

    all_records = []
    for pdf_file in pdf_files:
        print(f"[INFO] Processing: {pdf_file.name}")
        rows = extract_from_pdf(str(pdf_file))
        print(f"       → {len(rows)} rows extracted")
        all_records.extend(rows)

    if not all_records:
        print("[WARN] No data extracted from any PDF.")
        return

    df = pd.DataFrame(all_records)
    df = to_numeric(df)

    # Reorder columns for clarity
    col_order = ["source_file", "section"] + COLUMNS
    df = df[col_order]

    out_file = output_path / "index_dashboard.parquet"
    df.to_parquet(out_file, index=False, engine="pyarrow")
    print(f"\n[OK] Saved {len(df)} rows → {out_file.resolve()}")
    print(df.dtypes)
    print(df.head(5).to_string())


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    INPUT_DIR  = "data/pdfs"   # PDFs are read from here
    OUTPUT_DIR = "data"        # Parquet is written here

    process_all_pdfs(INPUT_DIR, OUTPUT_DIR)