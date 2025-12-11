import pandas as pd
import s3fs
import pyarrow.parquet as pq
import matplotlib.pyplot as plt
import seaborn as sns
import sys

# --- CONFIGURATION ---
BASE_BUCKET = "oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2021/resstock_amy2018_release_1"
BY_STATE_ROOT = f"{BASE_BUCKET}/timeseries_individual_buildings/by_state"

def get_correct_timeseries_path(fs, root_path, state_abbr):
    """
    NREL sometimes swaps folder order. This function hunts for the correct path.
    """
    # Option A: state=SC / upgrade=0
    path_a = f"{root_path}/state={state_abbr}/upgrade=0"
    # Option B: upgrade=0 / state=SC
    path_b = f"{root_path}/upgrade=0/state={state_abbr}"
    
    if fs.exists(path_a):
        return path_a
    elif fs.exists(path_b):
        return path_b
    else:
        # Debugging: Show what DOES exist
        print(f"⚠️ Could not find standard path. Listing {root_path}:")
        print(fs.ls(root_path)[:5])
        return None

def main():
    print("--- Single Home Grid Stress Test (Auto-Fix) ---")
    
    # 1. Load Metadata
    try:
        df_meta = pd.read_csv("sc_resstock_metadata.csv")
        print(f"Loaded metadata for {len(df_meta)} homes.")
    except FileNotFoundError:
        print("❌ Error: 'sc_resstock_metadata.csv' not found. Run the pull script first.")
        sys.exit(1)

    # 2. Pick a "Target" (Gas Heat, > 2500 sqft)
    # Using specific column names we verified in the last step
    df_meta['bldg_id'] = df_meta['bldg_id'].astype(str) # Ensure string for matching
    
    # Check available columns
    heat_col = 'in.heating_fuel' if 'in.heating_fuel' in df_meta.columns else 'in.hvac_heating_type'
    sqft_col = 'in.sqft' if 'in.sqft' in df_meta.columns else 'in.geometry_floor_area'

    targets = df_meta[
        (df_meta[heat_col].str.contains('Gas', na=False, case=False)) &
        (df_meta[sqft_col] > 2500)
    ]

    if targets.empty:
        print("❌ No targets found. Try lowering the sqft filter.")
        sys.exit(1)

    target_home = targets.iloc[0]
    bldg_id = target_home['bldg_id']
    
    print(f"🎯 Target Selected: Building {bldg_id}")
    print(f"   Size: {target_home[sqft_col]} sqft")
    print(f"   Heat: {target_home[heat_col]}")

    # 3. Connect to S3
    fs = s3fs.S3FileSystem(anon=True)
    
    print(f"\n🔍 Auto-detecting S3 folder structure...")
    ts_path = get_correct_timeseries_path(fs, BY_STATE_ROOT, 'SC')
    
    if not ts_path:
        print("❌ Critical Error: Could not locate timeseries folder structure.")
        sys.exit(1)
        
    print(f"✅ Verified Path: {ts_path}")

    # 4. Find the File (Using a Flexible Glob)
    # Note: NREL files are often named 'bldg_id-0.parquet' OR 'bldg_id-0.parquet'
    # We use a wildcard search to capture either.
    print(f"🔎 Searching for file matching *{bldg_id}* ...")
    file_list = fs.glob(f"{ts_path}/*{bldg_id}*.parquet")

    if not file_list:
        print(f"❌ Error: File not found for ID {bldg_id}")
        print("Debugging: Listing first 5 files in directory to verify naming convention:")
        print(fs.ls(ts_path)[:5])
        sys.exit(1)

    remote_file = file_list[0]
    print(f"✅ Found: {remote_file}")

    # 5. Download & Analyze
    cols_to_read = [
        'timestamp',
        'out.electricity.total.energy_consumption', 
        'out.natural_gas.heating.energy_consumption' 
    ]

    print("⬇️  Downloading interval data (this takes ~10s)...")
    with fs.open(remote_file) as f:
        df_ts = pd.read_parquet(f, columns=cols_to_read)

    df_ts['timestamp'] = pd.to_datetime(df_ts['timestamp'])
    df_ts.set_index('timestamp', inplace=True)

    # 6. Stress Test Simulation
    print("📈 Running simulation...")
    COP = 3.0 # Efficiency of new Heat Pump
    
    # Logic: New Load = Old Electric + (Old Gas / 3.0)
    df_ts['hp_added_load'] = df_ts['out.natural_gas.heating.energy_consumption'] / COP
    df_ts['total_load_after'] = df_ts['out.electricity.total.energy_consumption'] + df_ts['hp_added_load']

    # 7. Plotting (Zooming in on Jan 17th Peak)
    start_date = '2018-01-17 00:00'
    end_date = '2018-01-18 23:59'
    zoom = df_ts[start_date:end_date]

    plt.figure(figsize=(15, 6))
    sns.set_theme(style="whitegrid")

    # Before (Blue)
    sns.lineplot(x=zoom.index, y=zoom['out.electricity.total.energy_consumption'], 
                 label='Baseline (Gas Heat)', color='#1f77b4', linewidth=3)

    # After (Red)
    sns.lineplot(x=zoom.index, y=zoom['total_load_after'], 
                 label='Simulated (Heat Pump)', color='#d62728', linewidth=3, linestyle='--')

    # Shade the gap
    plt.fill_between(zoom.index, 
                     zoom['out.electricity.total.energy_consumption'], 
                     zoom['total_load_after'], 
                     color='red', alpha=0.1, label='Added Grid Stress')

    plt.title(f"Grid Stress Test: Building {bldg_id} (Jan 17, 2018)", fontsize=16)
    plt.ylabel("Load (kWh per 15-min)", fontsize=12)
    plt.xlabel("Hour of Day", fontsize=12)
    plt.legend(loc='upper left')
    plt.tight_layout()

    # Save
    img_name = "grid_stress_test_chart.png"
    plt.savefig(img_name)
    df_ts.to_csv("archetype_profile.csv")
    print("✅ Data saved as 'archetype_profile.csv' for the dashboard.")

if __name__ == "__main__":
    main()