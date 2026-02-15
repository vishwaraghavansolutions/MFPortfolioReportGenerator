import io
import json
from numpy import dtype
import pandas as pd
from google.cloud import storage
import streamlit as st
from datetime import datetime
from .storage_adapter import StorageAdapter


class GCSStorage(StorageAdapter):
    """Google Cloud Storage implementation of StorageAdapter."""

    def __init__(self, credentials_key: str = "gcp_service_account"):
        self.credentials = st.secrets[credentials_key]
        self.client = storage.Client.from_service_account_info(self.credentials)

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _get_blob(self, bucket: str, path: str):
        return self.client.bucket(bucket).blob(path)

    def _versioned_path(self, path: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if "." in path:
            base, ext = path.rsplit(".", 1)
            return f"{base}_{ts}.{ext}"
        return f"{path}_{ts}"

    # -----------------------------
    # File existence
    # -----------------------------
    def file_exists(self, bucket: str, path: str) -> bool:
        return self._get_blob(bucket, path).exists()

    # ---------------------------------------------------------
    # SAFE DATETIME PARSER
    # ---------------------------------------------------------
    @staticmethod
    def safe_parse_datetime(value):
        if value is None:
            return pd.NaT

        value = str(value).strip()
        if value == "" or value.lower() in ("none", "nan", "nat"):
            return pd.NaT

        # Try known formats first
        formats = [
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y %H:%M:%S",
        ]

        for fmt in formats:
            try:
                return pd.to_datetime(value, format=fmt)
            except Exception:
                pass

        # Fallback: let pandas try
        try:
            return pd.to_datetime(value, errors="coerce")
        except Exception:
            return pd.NaT
    # ---------------------------------------------------------
    # MAIN CSV READER
    # ---------------------------------------------------------
    def read_csv(self, bucket: str, path: str, **kwargs) -> pd.DataFrame:

        #client = storage.Client()
        #bucket = client.bucket(bucket)
        #blob = bucket.blob(path)

        #data = blob.download_as_bytes()  # <-- IMPORTANT
        #st.write(data)
        blob = self._get_blob(bucket, path)
        raw = blob.download_as_bytes()
        # -----------------------------------------------------
        # 1. Normalize raw bytes
        # -----------------------------------------------------
        clean = (
            raw.replace(b"\r\n", b"\n")
            .replace(b"\r", b"\n")
            .replace(b"\x00", b"")     # remove null bytes
        )

        # Strip BOM
        if clean.startswith(b"\xef\xbb\xbf"):
            clean = clean[3:]

        # Ensure final newline
        if not clean.endswith(b"\n"):
            clean += b"\n"

        # Decode safely
        text = clean.decode("utf-8", errors="replace")
        # -----------------------------------------------------
        # 2. Parse CSV
        # -----------------------------------------------------
        df = pd.read_csv(
            io.StringIO(text),
            engine="python",
            on_bad_lines="skip"
        )

        # Drop ghost columns created by malformed rows
        df = df.dropna(axis=1, how="all")
        # -----------------------------------------------------
        # 3. Enforce STRING columns
        # -----------------------------------------------------
        string_cols = [
            "ticket_id",
            "customer_id",
            "customer_name",
            "product_name",
            "message_from",
            "msg_content",
            "status",
            "posted_date",
            "msg_datetime",
            "closed_date",
        ]

        for col in string_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        # -----------------------------------------------------
        # 4. Create PARSED DATETIME columns
        # -----------------------------------------------------
        if "posted_date" in df.columns:
            df["dt_posted_date"] = df["posted_date"].apply(self.safe_parse_datetime)

        if "msg_datetime" in df.columns:
            df["dt_msg_datetime"] = df["msg_datetime"].apply(self.safe_parse_datetime)

        if "closed_date" in df.columns:
            df["dt_closed_date"] = df["closed_date"].apply(self.safe_parse_datetime)

        return df

    def read_parquet(self, bucket: str, path: str, **kwargs) -> pd.DataFrame:
        blob = self._get_blob(bucket, path)
        data = blob.download_as_bytes()
        return pd.read_parquet(io.BytesIO(data), **kwargs)

    # -----------------------------
    # WRITE
    # -----------------------------
    def write_csv(self, df: pd.DataFrame, bucket: str, path: str,
                  versioned: bool = False, overwrite: bool = False, **kwargs):

        print(f"Writing CSV to gs://{bucket}/{path}")

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: gs://{bucket}/{path}")

        buffer = io.StringIO()
        df.to_csv(buffer, index=False, lineterminator="\n", **kwargs)

        csv_text = buffer.getvalue()
        # 🔥 Remove trailing commas from every line
        cleaned_lines = []
        for line in csv_text.splitlines():
            cleaned_lines.append(line.rstrip(","))

        cleaned_csv = "\n".join(cleaned_lines) + "\n"
        blob = self._get_blob(bucket, path)
        st.write("cleaned csv size:", cleaned_csv[-100:])
        blob.upload_from_string(cleaned_csv, content_type="text/csv")
        raw = blob.download_as_bytes()
        st.write(raw[-100:])
        st.write("raw size after write:", len(raw))
        return

    def write_parquet(self, df: pd.DataFrame, bucket: str, path: str,
                      versioned: bool = False, overwrite: bool = False, **kwargs):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: gs://{bucket}/{path}")

        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False, **kwargs)

        blob = self._get_blob(bucket, path)
        blob.upload_from_string(buffer.getvalue(), content_type="application/octet-stream")

    def write_json(self, data, bucket: str, path: str,
                   versioned: bool = False, overwrite: bool = False):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: gs://{bucket}/{path}")

        json_str = json.dumps(data, indent=2)

        blob = self._get_blob(bucket, path)
        blob.upload_from_string(json_str, content_type="application/json")

    def write_excel(self, df: pd.DataFrame, bucket: str, path: str,
                    versioned: bool = False, overwrite: bool = False):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: gs://{bucket}/{path}")

        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)

        blob = self._get_blob(bucket, path)
        blob.upload_from_string(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )