import pandas as pd

df = pd.read_parquet("data/Index_Dashboard_JUN2025.parquet")
print(df.shape)
print(df.dtypes)
print(df.head(3).to_string())
print(df.columns.tolist())