import streamlit as st
import pandas as pd

st.set_page_config(page_title="Mutual Fund Rank & Score")
st.title("📊 Mutual Fund Rank & Score Finder")

# Excel file path inside repo
FILE_PATH = "data/MutualFund_Final_Output 16.xlsx"

try:
    # Load Excel once
    df = pd.read_excel(FILE_PATH)

    FUND_COL = "Fund Name"
    RANK_COL = "Rank"
    SCORE_COL = "Score"

    fund_name = st.text_input("Enter Fund Name")

    if fund_name:
        result = df[df[FUND_COL].str.lower().str.strip() == fund_name.lower().strip()]

        if not result.empty:
            st.success("Fund Found ✅")
            st.metric("Rank", result.iloc[0][RANK_COL])
            st.metric("Score", result.iloc[0][SCORE_COL])

            # 🔽 Download button
            with open(FILE_PATH, "rb") as file:
                st.download_button(
                    label="⬇️ Download Full Excel File",
                    data=file,
                    file_name="MutualFund_Final_Output_16.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        else:
            st.warning("Fund not found")

except FileNotFoundError:
    st.error("Excel file not found. Please check file path and name.")
except Exception as e:
    st.error(f"Error loading file: {e}")
