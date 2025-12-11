import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# --- CONFIGURATION ---
st.set_page_config(page_title="IRA Grid Simulator", layout="wide")

# --- CUSTOM CSS FOR READABILITY ---
st.markdown("""
    <style>
        /* Change the global font to a clean sans-serif */
        html, body, [class*="css"] {
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important;
        }
        /* Make headers stand out more */
        h1, h2, h3 {
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important;
            font-weight: 700 !important;
        }
    </style>
""", unsafe_allow_html=True)

# --- 1. LOAD DATA ---
@st.cache_data
def load_data():
    # Load the "Digital Twin" of SC Housing
    try:
        df_meta = pd.read_csv("sc_resstock_metadata.csv")
    except FileNotFoundError:
        st.error("⚠️ Metadata CSV not found. Run 'pull_resstock_data.py' first.")
        return pd.DataFrame(), pd.DataFrame()

    # Load the "Archetype" Load Profile (The single house timeseries)
    try:
        df_ts = pd.read_csv("archetype_profile.csv")
        df_ts['timestamp'] = pd.to_datetime(df_ts['timestamp'])
        
        # Filter to the "Cold Snap" week for the visual (Jan 16-19, 2018)
        start_date = '2018-01-16'
        end_date = '2018-01-19'
        mask = (df_ts['timestamp'] >= start_date) & (df_ts['timestamp'] <= end_date)
        df_ts = df_ts.loc[mask]
    except FileNotFoundError:
        st.error("⚠️ Archetype CSV not found. Run 'analyze_single_home.py' first.")
        return pd.DataFrame(), pd.DataFrame()

    return df_meta, df_ts

df_meta, df_archetype = load_data()

# --- 2. SIDEBAR CONTROLS ---
st.sidebar.title("Grid Stress Controls")

# A. County Filter
# We map the raw county names to a cleaner list
if not df_meta.empty:
    # Ensure we sort only valid values (drop NaNs)
    counties = sorted(df_meta['in.county'].dropna().unique())
    selected_county = st.sidebar.selectbox("Select County / Feeder", counties)
    
    # Filter metadata for this county
    county_homes = df_meta[df_meta['in.county'] == selected_county]
else:
    st.sidebar.warning("No data loaded")
    county_homes = pd.DataFrame()

# B. The "What-If" Slider
adoption_rate = st.sidebar.slider(
    "Heat Pump Adoption Rate (%)", 
    min_value=0, 
    max_value=100, 
    value=20,
    help="Percent of gas-heated homes switching to electric."
)

st.sidebar.markdown("---")
st.sidebar.info(
    """
    **Simulation Logic:**
    1. Identify Gas-Heated Homes (Target Market).
    2. Apply Adoption Rate.
    3. Inject simulated Heat Pump load (COP 3.0).
    4. Calculate new aggregate Feeder Load.
    """
)

# --- 3. MAIN DASHBOARD ---
st.title("IRA Electrification Impact Simulator")
st.markdown(f"**Feeder Analysis:** {selected_county} (South Carolina)")

if not county_homes.empty and not df_archetype.empty:
    
    # --- CALCULATIONS ---
    # 1. Identify "Addressable Market" (Gas Homes)
    # Check column names (handling variation between NREL releases)
    heat_col = 'in.heating_fuel' if 'in.heating_fuel' in county_homes.columns else 'in.hvac_heating_type'
    
    # Filter for Gas (Natural Gas, Propane, etc.)
    if heat_col in county_homes.columns:
        gas_homes = county_homes[county_homes[heat_col].str.contains('Gas|Propane', case=False, na=False)]
    else:
        st.error(f"Could not find heating column. Available: {county_homes.columns}")
        gas_homes = pd.DataFrame()

    total_gas_homes = len(gas_homes)
    
    # 2. Number of Converts
    num_converts = int(total_gas_homes * (adoption_rate / 100))
    
    # 3. Scale the Loads (Math: Single Home * Number of Homes)
    # We divide by 1000 to convert kWh -> MWh (Megawatts)
    
    # Baseline: The existing load of ALL gas homes in this county
    # (Using the archetype's electric load as the average)
    baseline_curve = (df_archetype['out.electricity.total.energy_consumption'] * total_gas_homes) / 1000
    
    # Added Load: The curve of the NEW heat pumps
    # (We calculated 'hp_added_load' in the previous script)
    added_curve = (df_archetype['hp_added_load'] * num_converts) / 1000
    
    # Total New Load
    new_total_curve = baseline_curve + added_curve
    
    # Peak Analysis
    old_peak = baseline_curve.max()
    new_peak = new_total_curve.max()
    peak_growth = new_peak - old_peak
    pct_increase = (peak_growth / old_peak) * 100 if old_peak > 0 else 0

    # --- TOP METRICS ROW ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Eligible Gas Homes", f"{total_gas_homes:,}")
    c2.metric("Projected Installs", f"{num_converts:,}", border=True)
    c3.metric("New Peak Load (MW)", f"{new_peak:.2f}", delta=f"+{peak_growth:.2f} MW")
    c4.metric("Grid Stress Increase", f"{pct_increase:.1f}%", delta_color="inverse")

    # --- THE CHART ---
    st.subheader("Winter Storm Simulation (Jan 16-19)")
    
    fig = go.Figure()

    # Blue Area (Current State)
    fig.add_trace(go.Scatter(
        x=df_archetype['timestamp'],
        y=baseline_curve,
        mode='lines',
        name='Current Grid Load',
        line=dict(color='#1f77b4', width=2),
        fill='tozeroy'
    ))

    # Red Line (Future State)
    fig.add_trace(go.Scatter(
        x=df_archetype['timestamp'],
        y=new_total_curve,
        mode='lines',
        name=f'Projected Load ({adoption_rate}% Adoption)',
        line=dict(color='#d62728', width=3, dash='solid')
    ))

    fig.update_layout(
        height=500,
        hovermode="x unified",
        yaxis_title="Aggregate Load (Megawatts)",
        xaxis_title="Time",
        legend=dict(y=1.1, orientation="h")
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # --- RISK TABLE (Robust Fix) ---
    st.markdown("### High-Priority Intervention List")
    st.caption("These homes match the 'High Income + Gas Heat' profile (Free Rider Risk).")
    
    # CHECK: Does the income column actually exist?
    high_risk = pd.DataFrame()
    
    if 'in.income' in gas_homes.columns:
        # If yes, filter for High Income ($100k+ or $200k+)
        # Using a regex to catch different income bin labels
        high_risk = gas_homes[gas_homes['in.income'].str.contains('100|200', regex=True, na=False)]
        
        if not high_risk.empty:
            st.success(f"Identified {len(high_risk)} high-income households for targeting.")
        else:
            st.info("No high-income households found in this subset.")
            high_risk = gas_homes # Fallback to showing all gas homes
    else:
        # If no, show a warning and skip the income filter
        st.warning("⚠️ Income data not available for this region. Showing all eligible gas homes.")
        high_risk = gas_homes

    # Display clean table
    display_cols = ['bldg_id', 'in.city', 'in.sqft', 'in.vintage', 'in.income', 'in.heating_fuel']
    
    # Only try to display columns that actually exist
    valid_cols = [c for c in display_cols if c in high_risk.columns]
    
    st.dataframe(
        high_risk[valid_cols].head(50),
        use_container_width=True,
        hide_index=True
    )

else:
    st.write("Waiting for data...")