import pandas as pd

# Can add more, should only need cables and fiber
fcc_data_files = [
    "../bdc_36_Cable_fixed_broadband_D25_04may2026.csv",
    "../bdc_36_FibertothePremises_fixed_broadband_D25_04may2026.csv",
]
# We slice the first 11 characters to get the Census Tract. 
# First 11 characters of block_geoid represent the Census Tract,
# last 6 represent the block. By slicing to 11, we can match all blocks within the same tract.
 #TODO: Make into function that takes census tract as input
target_census_tract = "061130105012006"[:11] 

providers_in_area = set()

print(f"Scanning datasets for surrounding Census Tract {target_census_tract}...")

for file_path in fcc_data_files:
    try:
        df = pd.read_csv(
            file_path, 
            usecols=['block_geoid', 'brand_name', 'business_residential_code'], 
            dtype={'block_geoid': str, 'brand_name': str, 'business_residential_code': str}
        )
        
        # Match any block that starts with our 11-digit Census Tract
        school_area_data = df[df['block_geoid'].str.startswith(target_census_tract, na=False)]
        
        for brand in school_area_data['brand_name'].dropna().unique():
            providers_in_area.add(brand)
            
        print(f"Processed {file_path}")
        
    except FileNotFoundError:
        print(f"Warning: Could not find {file_path}. Skipping.")

final_provider_list = list(providers_in_area)

print("\nFinal list of enterprise-capable providers in this area:")
print(final_provider_list)