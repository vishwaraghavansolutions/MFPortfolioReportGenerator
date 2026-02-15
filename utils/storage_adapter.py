from abc import ABC, abstractmethod
import pandas as pd


class StorageAdapter(ABC):
    """Abstract interface for cloud/local storage backends."""

    # -------- File existence --------
    @abstractmethod
    def file_exists(self, bucket: str, path: str) -> bool:
        pass

    # -------- Read operations --------
    @abstractmethod
    def read_csv(self, bucket: str, path: str, **kwargs) -> pd.DataFrame:
        pass

    @abstractmethod
    def read_parquet(self, bucket: str, path: str, **kwargs) -> pd.DataFrame:
        pass

    # -------- Write operations --------
    @abstractmethod
    def write_csv(self, df: pd.DataFrame, bucket: str, path: str,
                  versioned: bool = False, overwrite: bool = False, **kwargs):
        pass

    @abstractmethod
    def write_parquet(self, df: pd.DataFrame, bucket: str, path: str,
                      versioned: bool = False, overwrite: bool = False, **kwargs):
        pass

    @abstractmethod
    def write_json(self, data, bucket: str, path: str,
                   versioned: bool = False, overwrite: bool = False):
        pass

    @abstractmethod
    def write_excel(self, df: pd.DataFrame, bucket: str, path: str,
                    versioned: bool = False, overwrite: bool = False):
        pass