import pandas as pd
import s3fs
import pyarrow.parquet as pq
import sys
import os

# --- CONFIGURATION ---
BASE_BUCKET = "oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock"
RELEASE_YEAR = "2021" 
DATASET_NAME = "resstock_amy2018_release_1"
TABLE_NAME = "metadata" 
FULL_S3_PATH = f"{BASE_BUCKET}/{RELEASE_YEAR}/{DATASET_NAME}/{TABLE_NAME}"

TARGET_STATE = 'SC' 
OUTPUT_FILE = "sc_resstock_metadata.csv"

def main():
    print(f"--- Grid Stress Simulator Data Ingest (Diagnostic Mode) ---")
    
    # 1. DELETE OLD FILE (Auto-delete for you)
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
        print(f"🗑️  Deleted old {OUTPUT_FILE} to ensure clean start.")

    fs = s3fs.S3FileSystem(anon=True)
    
    print(f"⬇️  Streaming housing profiles for {TARGET_STATE} from {FULL_S3_PATH}...")
    try:
        dataset = pq.ParquetDataset(
            FULL_S3_PATH, 
            filesystem=fs, 
            filters=[('in.state', '=', TARGET_STATE)]
        )
        df = dataset.read().to_pandas()
        
        # --- DIAGNOSTIC PRINT ---
        print(f"\n🧐 RAW DATA INSPECTION:")
        print(f"   Index Name: {df.index.name}")
        print(f"   Columns: {list(df.columns[:5])}...") # Show first 5

        # --- THE FIX: FORCE INDEX TO COLUMN ---
        # If the ID is hidden in the index, pull it out
        print("🔧 Resetting Index to retrieve Building ID...")
        df.reset_index(inplace=True)
        
        # If the index didn't have a name, Pandas names the new column 'index'
        if 'index' in df.columns and 'bldg_id' not in df.columns:
            print("🔧 Renaming 'index' column to 'bldg_id'...")
            df.rename(columns={'index': 'bldg_id'}, inplace=True)

        # Verify we have the ID now
        if 'bldg_id' not in df.columns:
            print("❌ CRITICAL ERROR: Could not find 'bldg_id' even after reset!")
            print(f"   Available columns: {list(df.columns)}")
            sys.exit(1)
        else:
            print(f"✅ 'bldg_id' successfully recovered.")

    except Exception as e:
        print(f"❌ Error reading Parquet data: {e}")
        sys.exit(1)

    # 2. SELECT COLUMNS
    cols_to_keep = [
        'bldg_id', 
        'in.city', 'in.county', 'in.sqft', 'in.vintage',
        'in.heating_fuel', 'in.hvac_cooling_type', 
        'in.income', 'in.usage_level'
    ]

    # Smart Filter (Handle column renames)
    final_cols = []
    for c in cols_to_keep:
        if c in df.columns:
            final_cols.append(c)
        elif c == 'in.sqft' and 'in.geometry_floor_area' in df.columns:
            final_cols.append('in.geometry_floor_area')
        elif c == 'in.heating_fuel' and 'in.hvac_heating_type' in df.columns:
            final_cols.append('in.hvac_heating_type')

    # 3. SAVE
    df_clean = df[final_cols]
    df_clean.to_csv(OUTPUT_FILE, index=False)
    
    print(f"\n💾 Data saved to: {OUTPUT_FILE}")
    print(f"   Rows: {len(df_clean)}")
    print(f"   Columns: {list(df_clean.columns)}") # Verify bldg_id is here

if __name__ == "__main__":
    main()