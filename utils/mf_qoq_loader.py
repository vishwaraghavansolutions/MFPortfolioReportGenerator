from typing import Optional
from utils.gcs_csv_reader import GCPCSVReader
from datetime import datetime
from dateutil.relativedelta import relativedelta

class DateHelper:
    @staticmethod
    def rolling_dates(base_date: datetime):
        return {
            "1M": base_date - relativedelta(months=1),
            "6M": base_date - relativedelta(months=6),
            "1Y": base_date - relativedelta(years=1),
            "3Y": base_date - relativedelta(years=3),
        }

    QUARTER_ENDS = [(3,31), (6,30), (9,30), (12,31)]

    @staticmethod
    def last_4_quarter_ends(base_date: datetime):
        q_dates = []
        year = base_date.year
        month = base_date.month

        # find the most recent quarter end
        for m, d in reversed(DateHelper.QUARTER_ENDS):
            if month >= m:
                q_dates.append(datetime(year, m, d))
                break

        # if none matched, go to previous year's Q4
        if not q_dates:
            q_dates.append(datetime(year - 1, 12, 31))

        # add previous 3 quarters
        while len(q_dates) < 4:
            last = q_dates[-1]
            prev = last - relativedelta(months=3)
            q_dates.append(datetime(prev.year, prev.month, prev.day))

        return q_dates
    
class PortfolioDataLoader:
    def __init__(self, bucket_name: str):
        self.reader = GCPCSVReader("gcp", bucket_name=bucket_name)

    def _path_for_date(self, dt: datetime):

        if dt.year == 2026 and dt.month == 3:
            return "Datawarehouse/MutualFunds/2026/02/26/mutualfunds.csv"
        
        if dt.year == 2025 and dt.month == 3:
            return "Datawarehouse/MutualFunds/2025/03/31/mutualfunds.csv"
        
        return f"Datawarehouse/MutualFunds/{dt.year}/{dt.month:02d}/{dt.day:02d}/mutualfunds.csv"

    def load_for_date(self, dt: datetime, customer: Optional[str] = None):
        path = self._path_for_date(dt)
        df = self.reader.read_csv(path)
        print(path, df.shape)

        if customer:
            if df.empty:
                return df
            df = df[df["h_name"] == customer]

        return df

    def load_rolling(self, base_date: datetime, customer: Optional[str] = None):
        dates = DateHelper.rolling_dates(base_date)
        result = {}

        for label, dt in dates.items():
            result[label] = self.load_for_date(dt, customer)

        return result

    def load_last_4_quarters(self, base_date: datetime, customer: Optional[str] = None):
        q_dates = DateHelper.last_4_quarter_ends(base_date)
        result = {}

        for dt in q_dates:
            key = f"Q{dt.month}_{dt.year}"
            result[key] = self.load_for_date(dt, customer)

        return result