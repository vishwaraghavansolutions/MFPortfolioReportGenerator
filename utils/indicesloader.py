import os
import pandas as pd
from utils.safe_browser_download import browser_like_download
from utils.indicesextractor import normalize_headers
import pdfplumber
import io

PARQUET_PATH = "data/indices.parquet"
PDF_FOLDER = "data/pdfs"
  

def extract_all_tables(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Extracts all tables from all pages of the PDF and returns a combined DataFrame.
    Each table gets a __page__ column so headers can be normalized per page.
    """
    all_tables = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()

            for table in tables:
                df = pd.DataFrame(table)

                # Drop empty rows/columns
                #df = df.dropna(how="all").dropna(how="all", axis=1)

                # Keep only meaningful tables
                if df.shape[1] > 1:
                    df["__page__"] = page_num
                    all_tables.append(df)

    if not all_tables:
        raise ValueError("No tables found in PDF")

    return pd.concat(all_tables, ignore_index=True)

def ensure_pdf_folder():
    if not os.path.exists(PDF_FOLDER):
        os.makedirs(PDF_FOLDER)

def get_pdf_filename(month, year):
    return f"Index_Dashboard_{month.upper()}{year}.pdf"

def get_pdf_url(month, year):
    return f"https://www.niftyindices.com/Index_Dashboard/Index_Dashboard_{month.upper()}{year}.pdf"

def load_or_update_data(month: str, year: int):
    ensure_pdf_folder()

    key = f"{month}-{year}"
    pdf_filename = get_pdf_filename(month, year)
    pdf_path = os.path.join(PDF_FOLDER, pdf_filename)

    # 1️⃣ Check if parquet already contains this month
    if os.path.exists(PARQUET_PATH):
        df = pd.read_parquet(PARQUET_PATH)
        if key in df["__month__"].unique():
            return df[df["__month__"] == key], df
    else:
        df = pd.DataFrame()

    # 2️⃣ Check if PDF exists locally
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    else:
        # 3️⃣ Download PDF from web
        url = get_pdf_url(month, year)
        pdf_bytes = browser_like_download(url)

        # Save locally for future use
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

    # 4️⃣ Extract & normalize
    raw_df = extract_all_tables(pdf_bytes)
    cleaned_df = normalize_headers(raw_df)
    cleaned_df["__month__"] = key

    # 5️⃣ Append to parquet
    if df.empty:
        cleaned_df.to_parquet(PARQUET_PATH, index=False)
        return cleaned_df, cleaned_df
    else:
        final_df = pd.concat([df, cleaned_df], ignore_index=True)
        #final_df.to_parquet(PARQUET_PATH, index=False)
        return cleaned_df, final_df