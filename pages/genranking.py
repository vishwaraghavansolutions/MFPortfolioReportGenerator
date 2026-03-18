import streamlit as st
import pandas as pd

# ----------------------------------
# 1. Configuration & Metadata Maps
# ----------------------------------
st.set_page_config(page_title="MF Custom Scorer", layout="wide")

# TITLE AND REFRESH BUTTON ROW
col_title, col_refresh = st.columns([0.9, 0.1])
with col_title:
    st.title("📊 Mutual Fund Rank & Score Finder")
with col_refresh:
    if st.button("🔄 Refresh"):
        st.rerun()

params_info = {
    "AUM": "higher", "TER": "lower", "PE": "lower", "PB": "lower",
    "Top 3 Holdings": "lower", "Top 5 Holdings": "lower", "Top 10 Holdings": "lower",
    "Sharpe": "higher", "Sortino": "higher", "St Dev": "lower",
    "Inception": "higher", "Age in yrs": "higher"
}

scheme_category_map = {
    "Equity Scheme": ["Contra Fund", "Dividend Yield Fund", "ELSS", "Focused Fund", "Large Cap Fund", "Large & Mid Cap Fund", "Mid Cap Fund", "Multi Cap Fund", "Sectoral / Thematic", "Small Cap Fund", "Value Fund", "Flexi Cap Fund", "Index Fund"],
    "Debt Scheme": ["Banking and PSU Fund", "Corporate Bond Fund", "Credit Risk Fund", "Dynamic Bond", "Floater Fund", "Gilt Fund", "Liquid Fund", "Money Market Fund", "Short Duration Fund"],
    "Hybrid Scheme": ["Arbitrage Fund", "Balanced Hybrid Fund", "Conservative Hybrid Fund", "Dynamic Asset Allocation or Balanced Advantage", "Equity Savings"],
}

# ----------------------------------
# 2. Load Data
# ----------------------------------
@st.cache_data
def load_data():
    raw_df = pd.read_csv("Ranked_master.csv")
    raw_df.columns = raw_df.columns.str.strip()
    
    # Metadata for UI display
    metadata_rows = raw_df.iloc[0:2].copy()
    funds_df = raw_df.iloc[2:].copy()
    
    for col in ['Scheme Type', 'Scheme Category', 'Plan']:
        if col in funds_df.columns:
            funds_df[col] = funds_df[col].astype(str).str.strip()

    cols_to_fix = list(params_info.keys()) + ['Score']
    for col in cols_to_fix:
        if col in funds_df.columns:
            funds_df[col] = pd.to_numeric(funds_df[col], errors='coerce').fillna(0)
    
    return funds_df, metadata_rows

df_master, df_metadata = load_data()

# ----------------------------------
# 3. Selection UI (Step 1)
# ----------------------------------
st.markdown("### Step 1: Select Fund Category & Plan")
col1, col2, col3 = st.columns(3)

with col1:
    st_type = st.selectbox("Scheme Type", ["Select"] + list(scheme_category_map.keys()), key="global_type")

with col2:
    if st_type != "Select":
        all_cats = scheme_category_map[st_type]
        st_cat = st.multiselect("Scheme Categories", all_cats, default=all_cats, key="global_cat")
    else:
        st_cat = st.multiselect("Scheme Categories", ["Select Type First"], disabled=True)

with col3:
    if st_type != "Select":
        available_plans = sorted(df_master['Plan'].unique().tolist())
        st_plan = st.selectbox("Plan", ["All"] + available_plans, key="global_plan")
    else:
        st_plan = st.selectbox("Plan", ["Select Type First"], disabled=True)

st.divider()

# ----------------------------------
# 4. Processing & Displaying Original Data
# ----------------------------------
if st_type != "Select" and st_cat:
    mask = (df_master['Scheme Type'] == st_type) & (df_master['Scheme Category'].isin(st_cat))
    if st_plan != "All":
        mask = mask & (df_master['Plan'] == st_plan)
        
    base_funds = df_master[mask].copy()

    if not base_funds.empty:
        # Calculate Ranks for UI
        base_funds = base_funds.sort_values(by=["Scheme Category", "Score"], ascending=[True, False])
        base_funds['Rank'] = base_funds.groupby('Scheme Category')['Score'].rank(ascending=False, method='first').astype(int)
        
        # UI Display Table (Metadata included)
        display_meta = df_metadata.copy()
        display_meta['Rank'] = ["", ""]
        final_display = pd.concat([display_meta, base_funds], axis=0)
        
        all_cols = list(final_display.columns)
        new_col_order = ['Rank'] + [c for c in all_cols if c not in ['Rank', 'Score']] + ['Score']
        final_display = final_display[new_col_order]
        
        st.subheader(f"📍 Original Rankings ({len(st_cat)} Categories)")
        st.dataframe(final_display, use_container_width=True, hide_index=True)
        
        # Download Original (Metadata EXCLUDED)
        st.download_button(
            label="⬇️ Download Original Data (Clean CSV)",
            data=base_funds[new_col_order].to_csv(index=False),
            file_name="Original_Rankings_Clean.csv",
            mime="text/csv",
            key="btn_orig_top"
        )

        st.divider()

        # ----------------------------------
        # 5. Modify Weightages Section
        # ----------------------------------
        st.subheader("⚖️ Custom Score Calculation")
        
        if "editor_visible" not in st.session_state:
            st.session_state.editor_visible = False

        if st.button("🔧 Modify Weights & Filter Scope"):
            st.session_state.editor_visible = not st.session_state.editor_visible

        if st.session_state.editor_visible:
            edit_col1, edit_col2 = st.columns(2)
            with edit_col1:
                st.multiselect("Edit Category Scope", all_cats, default=st_cat, key="edit_cat")
            with edit_col2:
                plans_list = ["All"] + available_plans
                p_idx = plans_list.index(st_plan) if st_plan in plans_list else 0
                st.selectbox("Edit Plan Scope", plans_list, index=p_idx, key="edit_plan")

            # Weight Inputs
            user_weights = {}
            w_cols = st.columns(4)
            for i, param in enumerate(params_info.keys()):
                with w_cols[i % 4]:
                    # Use weight from CSV row 1 as default
                    def_val = int(float(df_metadata.iloc[1][param])) if param in df_metadata.columns else 0
                    user_weights[param] = st.number_input(f"{param} Weight", 0, 100, def_val, key=f"w_{param}")
            
            user_sum = sum(user_weights.values())
            if user_sum == 100:
                st.success(f"✅ Total Weightage: {user_sum}/100")
                if st.button("🚀 Calculate New Ranks"):
                    c_mask = (df_master['Scheme Type'] == st_type)
                    if st.session_state.edit_cat:
                        c_mask &= (df_master['Scheme Category'].isin(st.session_state.edit_cat))
                    if st.session_state.edit_plan != "All":
                        c_mask &= (df_master['Plan'] == st.session_state.edit_plan)
                    
                    calc_df = df_master[c_mask].copy()

                    def apply_formula(row):
                        score = 0
                        for p, w in user_weights.items():
                            if params_info[p] == "higher": score += (row[p] * w)
                            else: score -= (row[p] * w)
                        return score

                    calc_df['Score'] = calc_df.apply(apply_formula, axis=1)
                    calc_df = calc_df.sort_values(by=["Scheme Category", "Score"], ascending=[True, False])
                    calc_df['Rank'] = calc_df.groupby('Scheme Category')['Score'].rank(ascending=False, method='first').astype(int)
                    
                    # --- FIXED METADATA LOGIC ---
                    cust_meta = df_metadata.copy()
                    # Instead of assigning a whole series, we update specific weight columns
                    for param, weight in user_weights.items():
                        if param in cust_meta.columns:
                            # Use .at to update the specific cell (Row 1, Column Param)
                            cust_meta.at[cust_meta.index[1], param] = weight
                    
                    cust_meta['Rank'] = ["", ""]
                    # -----------------------------

                    st.session_state.custom_display_df = pd.concat([cust_meta, calc_df], axis=0)[new_col_order]
                    # Store CLEAN data for download (excludes metadata)
                    st.session_state.custom_clean_df = calc_df[new_col_order]
            else:
                st.warning(f"⚠️ Total weight must be 100.")

            # --- DISPLAY CUSTOM RESULTS ---
            if "custom_display_df" in st.session_state:
                st.write("---")
                st.subheader("📊 Custom Results Preview")
                st.dataframe(st.session_state.custom_display_df, use_container_width=True, hide_index=True)
                
                st.download_button(
                    label="⬇️ Download These Custom Ranks (Clean CSV)",
                    data=st.session_state.custom_clean_df.to_csv(index=False),
                    file_name="Custom_Modified_Clean.csv",
                    mime="text/csv",
                    key="btn_custom_bottom"
                )
elif st_type != "Select" and not st_cat:
    st.warning("⚠️ Select at least one category.")