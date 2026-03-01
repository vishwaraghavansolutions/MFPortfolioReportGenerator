from google.cloud import storage
import pandas as pd
from io import StringIO
from typing import List, Optional
import streamlit as st

class GCPCSVReader:

    def __init__(self, credentials_key: str = "gcp", bucket_name: str = "winrich"):
        self.credentials = st.secrets[credentials_key]
        self.client = storage.Client.from_service_account_info(self.credentials)
        self.bucket = self.client.bucket(bucket_name)
        self.bucket_name = bucket_name

    def read_csv(
        self,
        blob_path: str,
        parse_dates: Optional[List[str]] = None,
        dtype: Optional[dict] = None
    ) -> pd.DataFrame:
        """
        Reads a CSV from GCP bucket and returns a pandas DataFrame.

        blob_path: path inside bucket (e.g. Datawarehouse/MutualFunds/2026/01/01/file.csv)
        parse_dates: list of columns to parse as dates
        dtype: optional dtype overrides
        """
        blob = self.bucket.blob(blob_path)
        try:
            csv_bytes = blob.download_as_bytes()
        except Exception as e:
            return pd.DataFrame()
            
        csv_str = csv_bytes.decode("utf-8")

        df = pd.read_csv(
            StringIO(csv_str),
            parse_dates=parse_dates,
            dtype=dtype
        )
        return df