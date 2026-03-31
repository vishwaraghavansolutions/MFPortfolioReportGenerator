"""
agents/winrich_mf_data_agent.py
================================  
WinrichMFDataAgent — sole owner of the MutualFunds datawarehouse CSV on GCS.

All other agents call this agent to get customer data; they never touch
GCS, CSV paths, or credentials directly.

Authentication is handled by GCPCSVReader via st.secrets["gcp"] —
no credential plumbing needed here.

Skills
------
  list_customers          – return sorted list of all unique c_name values
  load_customer_portfolio – filter one customer's holdings from the datawarehouse
  get_resolved_path       – return the GCS URI that would be loaded (no download)

All skills accept these optional GCS params
-------------------------------------------
  bucket_name       : str       default "winrich"
  as_of_date        : datetime  default datetime.now()
  max_lookback_days : int       default 10

load_customer_portfolio also requires
--------------------------------------
  customer_name : str
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict

import pandas as pd

from agents.base import Agent, AgentResponse, AgentStatus
from utils.datawarehouse_loader import load_datawarehouse_csv, get_datawarehouse_path

log = logging.getLogger(__name__)

_DEFAULT_BUCKET = "winrich"


class WinrichMFDataAgent(Agent):
    """
    Single-responsibility agent: owns the MutualFunds datawarehouse CSV on GCS.

    Authentication is handled entirely by GCPCSVReader (st.secrets["gcp"]).

    Usage
    -----
        agent = WinrichMFDataAgent()

        # Fast sidebar population
        resp = agent.run("list_customers", {})
        customers = resp.output["all_customers"]

        # Full enriched portfolio for one customer
        resp = agent.run("load_customer_portfolio", {"customer_name": "Alice"})
        df = resp.output["customer_df"]
    """

    name = "WinrichMFDataAgent"

    def __init__(self):
        pass

    # ── Skill map ──────────────────────────────────────────────────────────────
    @property
    def skills(self) -> Dict[str, Callable]:
        return {
            "list_customers":          self._list_customers,
            "load_customer_portfolio": self._load_customer_portfolio,
            "get_resolved_path":       self._get_resolved_path,
        }

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    @staticmethod
    def _gcs_params(params: Dict[str, Any]) -> tuple:
        raw = params.get("as_of_date")
        if not raw:
            as_of_date = datetime.now()
        elif isinstance(raw, str):
            as_of_date = datetime.strptime(raw[:10], "%Y-%m-%d")
        else:
            as_of_date = raw
        return (
            params.get("bucket_name",       _DEFAULT_BUCKET),
            as_of_date,
            params.get("max_lookback_days", 10),
        )

    def _load_raw(
        self,
        bucket_name:       str,
        as_of_date:        datetime,
        max_lookback_days: int,
    ) -> pd.DataFrame:
        return load_datawarehouse_csv(
            bucket_name=bucket_name,
            as_of_date=as_of_date,
            max_lookback_days=max_lookback_days,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill: list_customers
    # ──────────────────────────────────────────────────────────────────────────
    def _list_customers(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Return a sorted list of all unique customer names in the datawarehouse.

        Output keys
        -----------
        all_customers  : list[str]
        total_records  : int
        resolved_path  : str
        """
        bucket, as_of, lookback = self._gcs_params(params)

        try:
            df = self._load_raw(bucket, as_of, lookback)
        except FileNotFoundError as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))
        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=f"GCS load error: {exc}")

        all_customers = sorted(df["c_name"].dropna().unique().tolist())

        try:
            resolved = get_datawarehouse_path(as_of, bucket, lookback)
        except Exception:
            resolved = f"gs://{bucket}/Datawarehouse/MutualFunds/…/mutualfunds.csv"

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "all_customers": all_customers,
                "total_records": len(df),
                "resolved_path": resolved,
            },
            metadata={"customer_count": len(all_customers)},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill: load_customer_portfolio
    # ──────────────────────────────────────────────────────────────────────────
    def _load_customer_portfolio(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Download the datawarehouse CSV, filter to one customer, and enrich
        with benchmark index data via SchemeLookup.

        Required params
        ---------------
        customer_name : str

        Output keys
        -----------
        customer_df       : pd.DataFrame
        selected_customer : str
        all_customers     : list[str]
        resolved_path     : str
        """
        customer_name = params.get("customer_name")
        if not customer_name:
            return AgentResponse(AgentStatus.FAILED, error="'customer_name' is required")

        bucket, as_of, lookback = self._gcs_params(params)

        try:
            df = self._load_raw(bucket, as_of, lookback)
        except FileNotFoundError as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))
        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=f"GCS load error: {exc}")

        all_customers = sorted(df["c_name"].dropna().unique().tolist())

        if customer_name not in all_customers:
            return AgentResponse(
                AgentStatus.FAILED,
                error=(
                    f"Customer '{customer_name}' not found. "
                    f"{len(all_customers)} customers available in this file."
                ),
                metadata={"available_count": len(all_customers)},
            )

        customer_df = df[df["c_name"] == customer_name].copy()

        try:
            resolved = get_datawarehouse_path(as_of, bucket, lookback)
        except Exception:
            resolved = f"gs://{bucket}/Datawarehouse/MutualFunds/…/mutualfunds.csv"

        log.debug(
            "WinrichMFDataAgent: loaded %d rows for '%s' from %s",
            len(customer_df), customer_name, resolved,
        )

        return AgentResponse(
            AgentStatus.SUCCESS,
            output={
                "customer_df":       customer_df,
                "selected_customer": customer_name,
                "all_customers":     all_customers,
                "resolved_path":     resolved,
            },
            metadata={"records": len(customer_df), "resolved_path": resolved},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Skill: get_resolved_path
    # ──────────────────────────────────────────────────────────────────────────
    def _get_resolved_path(self, params: Dict[str, Any]) -> AgentResponse:
        """
        Probe GCS and return the URI of the most recent datawarehouse CSV
        without downloading it.

        Output keys
        -----------
        resolved_path : str
        """
        bucket, as_of, lookback = self._gcs_params(params)
        try:
            path = get_datawarehouse_path(as_of, bucket, lookback)
            return AgentResponse(AgentStatus.SUCCESS, output={"resolved_path": path})
        except FileNotFoundError as exc:
            return AgentResponse(AgentStatus.FAILED, error=str(exc))
        except Exception as exc:
            return AgentResponse(AgentStatus.FAILED, error=f"GCS probe failed: {exc}")