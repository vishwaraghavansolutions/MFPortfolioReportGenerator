import io
import json
import pandas as pd
import boto3
from datetime import datetime
from .storage_adapter import StorageAdapter
from botocore.exceptions import ClientError
import streamlit as st


class S3Storage(StorageAdapter):
    """AWS S3 implementation of StorageAdapter."""

    def __init__(self, aws_access_key: str, aws_secret_key: str, region: str = "us-east-1"):
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region
        )

    # -----------------------------
    # Helpers
    # -----------------------------
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
        try:
            self.s3.head_object(Bucket=bucket, Key=path)
            return True
        except self.s3.exceptions.ClientError:
            return False

    # -----------------------------
    # READ
    # -----------------------------
    def read_csv(self, bucket: str, path: str, **kwargs) -> pd.DataFrame:
        
        obj = self.s3.get_object(Bucket=bucket, Key=path)
        return pd.read_csv(io.BytesIO(obj["Body"].read()), **kwargs)

    def read_parquet(self, bucket: str, path: str, **kwargs) -> pd.DataFrame:
        try:
            obj = self.s3.get_object(Bucket=bucket, Key=path)
        except Exception as e:
            if "NoSuchKey" in str(e):
                return pd.DataFrame()
        return pd.read_parquet(io.BytesIO(obj["Body"].read()), **kwargs)

    def bucket_and_key_exist(self, bucket_name, key_name):    
        # Check if bucket exists
        try:
            self.s3.head_bucket(Bucket=bucket_name)
        except ClientError as e:
            print(f"Bucket check failed: {e}")
            return False

        # Check if key exists
        try:
            self.s3.head_object(Bucket=bucket_name, Key=key_name)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                print(f"Key '{key_name}' not found in bucket '{bucket_name}'")
                return False
            else:
                print(f"Key check failed: {e}")
                return False

    def get_file(self, bucket: str, key: str) -> str:
        """Download file content from S3 as string"""
        if self.bucket_and_key_exist(bucket, key):
            try:
                obj = self.s3.get_object(Bucket=bucket, Key=key)
                return obj['Body'].read().decode('utf-8')
            except Exception as e:
                st.error(f"Error fetching file: {e}")
                return ""
        else: 
            return ""

    def read_json(self, bucket: str, key: str) -> dict:
        """Download and parse JSON file from S3"""
        if self.bucket_and_key_exist(bucket, key):
            content = self.get_file(bucket, key)
            try:
                return json.loads(content)
            except json.JSONDecodeError:
               return {}
        else:
            return {}
    # -----------------------------
    # WRITE
    # -----------------------------
    def write_csv(self, df: pd.DataFrame, bucket: str, path: str,
                  versioned: bool = False, overwrite: bool = False, **kwargs):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: s3://{bucket}/{path}")

        buffer = io.StringIO()
        df.to_csv(buffer, index=False, **kwargs)

        self.s3.put_object(
            Bucket=bucket,
            Key=path,
            Body=buffer.getvalue(),
            ContentType="text/csv"
        )

    def write_parquet(self, df: pd.DataFrame, bucket: str, path: str,
                      versioned: bool = False, overwrite: bool = False, **kwargs):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: s3://{bucket}/{path}")

        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False, **kwargs)

        self.s3.put_object(
            Bucket=bucket,
            Key=path,
            Body=buffer.getvalue(),
            ContentType="application/octet-stream"
        )

    def write_json(self, data, bucket: str, path: str,
                   versioned: bool = False, overwrite: bool = False):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: s3://{bucket}/{path}")

        json_str = json.dumps(data, indent=2)

        self.s3.put_object(
            Bucket=bucket,
            Key=path,
            Body=json_str,
            ContentType="application/json"
        )

    def write_excel(self, df: pd.DataFrame, bucket: str, path: str,
                    versioned: bool = False, overwrite: bool = False):

        if versioned:
            path = self._versioned_path(path)
        elif not overwrite and self.file_exists(bucket, path):
            raise FileExistsError(f"File already exists: s3://{bucket}/{path}")

        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)

        self.s3.put_object(
            Bucket=bucket,
            Key=path,
            Body=buffer.getvalue(),
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )