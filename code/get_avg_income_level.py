"""
Calculates income level off of county median household income data for each school LatLong in the input.

Input: data/inputs/schools_selected.csv
Output: data/outputs/combined_results.csv
"""

import os
import time
import pandas as pd
import requests

CENSUS_KEY = "49aa50d23f36182c911596a996ca243ca3ce11f2"
INPUT_PATH = "data/inputs/schools_selected.csv"
OUTPUT_PATH = "data/outputs/schools_with_income.csv"

"""
Worker function that identifies the county for a given pair of coordinates
and fetches the county-level median household income from the ACS data.
"""
def get_county_income_for_row(lat, lon, state_fips="36", census_key=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    }
    
    clean_lat = round(float(lat), 5)
    clean_lon = round(float(lon), 5)
    
    # 1. Use the Census Geocoder to find the County
    geocoder_url = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
    geo_params = {
        "x": clean_lon,
        "y": clean_lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json"
    }
    
    try:
        geo_res = requests.get(geocoder_url, params=geo_params, headers=headers, timeout=12)
        
        if "html" in geo_res.text.lower() or geo_res.status_code != 200:
            return "Geocoder Server Error", "N/A"
            
        geo_data = geo_res.json()
        geographies = geo_data.get("result", {}).get("geographies", {})
        
        if "Counties" not in geographies:
            return "Outside NY State Borders", "N/A"
            
        county_info = geographies["Counties"][0]
        county_name = county_info["NAME"]
        county_fips = county_info["COUNTY"] # Extracts the specific 3-digit county identifier
        
        # 2. Query the ACS Financial Tables for this specific County
        # Using variable B19013_001E (Median Household Income) for the county geography tier
        census_url = f"https://api.census.gov/data/2022/acs/acs5?get=NAME,B19013_001E&for=county:{county_fips}&in=state:{state_fips}"
        if census_key:
            census_url += f"&key={census_key}"
            
        time.sleep(0.5) # Polite API rate-limit pause
        
        res = requests.get(census_url, headers=headers, timeout=12)
        if "html" in res.text.lower() or res.status_code != 200:
            return county_name, f"Data API Error {res.status_code}"
            
        census_data = res.json()
        
        # The Census Bureau returns a clean data row matching the target county FIPS perfectly
        income_val = int(census_data[1][1]) 
        
        return county_name, f"${income_val:,}" if income_val >= 0 else "Suppressed"
            
    except Exception as e:
        return "Lookup Failed", f"Error: {str(e)}"


def batch_process_counties(input_csv, output_csv, census_key=None, state_fips="36"):
    """
    Loads your CSV file, loops through the coordinates, maps them to counties,
    and appends the median household income data safely.
    """
    print(f"Loading spreadsheet dataset: {input_csv}...")
    df = pd.read_csv(input_csv)
    
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        raise ValueError("The CSV file must contain 'latitude' and 'longitude' columns.")
        
    counties = []
    incomes = []
    total_rows = len(df)
    
    print(f"Processing {total_rows} rows via County-Level matching optimization...")
    for index, row in df.iterrows():
        lat, lon = row['latitude'], row['longitude']
        print(f" -> Row {index + 1}/{total_rows} ({lat}, {lon})")
        
        county, income = get_county_income_for_row(lat, lon, state_fips, census_key)
        print(f"Mapped to County: {county}, Median Income: {income}")
        
        counties.append(county)
        income = income.replace("$", "").replace(",", "")
        incomes.append(float(income))
        
        time.sleep(0.5) #have gap between api calls
        
    df['census_county'] = counties
    df['county_median_household_income'] = incomes
    
    df.to_csv(output_csv, index=False)
    print(f"\nProcessing complete! Results saved to output: {output_csv}")


if __name__ == "__main__":
    
    if os.path.exists(INPUT_PATH):
        batch_process_counties(INPUT_PATH, OUTPUT_PATH, census_key=CENSUS_KEY, state_fips="36")
    else:
        print(f"Error: Could not locate your input file '{INPUT_PATH}'.")

# import requests
# import pandas as pd
# import time

# INPUT_DIR = "data/inputs/schools_selected.csv"
# OUTPUT_DIR = "data/outputs/schools_with_income.csv"

# def get_income_by_coordinates(lat, lon, state_fips="36", census_key=None):
#     """
#     Directly identifies a school district's median household income using 
#     pre-obtained latitude and longitude coordinates.
#     """
#     # 1. Query the Census Bureau Geocoder to find the School District ID
#     headers = {
#         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
#     }

#     lat = round(float(lat), 5)
#     lon = round(float(lon), 5)

#     print(f"Step 1: Identifying Census boundaries for coordinates: {lat}, {lon}...")
#     geocoder_url = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
    

#     geo_params = {
#         "x": lon,  # Longitude is the X-axis
#         "y": lat,  # Latitude is the Y-axis
#         "benchmark": "Public_AR_Census2020",
#         "vintage": "Census2020_Census2020",
#         "format": "json"
#     }
    
#     try:
#         geo_res = requests.get(geocoder_url, params=geo_params, headers=headers, timeout=12)
        
#         if "html" in geo_res.text.lower() or geo_res.status_code != 200:
#             return "Geocoder Server Error", f"Status {geo_res.status_code}"

#         geo_data = geo_res.json()

#         if "geographies" not in geo_data.get("result", {}):
#             return "Boundary Match Failed", "N/A"
        
#         geographies = geo_data["result"]["geographies"]
        
#         district_name = None
#         geo_type = None
        
#         # Check standard district layers (Unified vs Secondary)
#         if "Unified School Districts" in geographies:
#             district_name = geographies["Unified School Districts"][0]["NAME"]
#             geo_type = "school district (unified)"
#         elif "Secondary School Districts" in geographies:
#             district_name = geographies["Secondary School Districts"][0]["NAME"]
#             geo_type = "school district (secondary)"
#         elif "Elementary School Districts" in geographies:
#             district_name = geographies["Elementary School Districts"][0]["NAME"]
#             geo_type = "school district (elementary)"
            
#         # NYC EXCEPTION HANDLER
#         # If districts return "not defined" (typical inside NYC boroughs), fallback to the County layer
#         if not district_name or "not defined" in district_name.lower():
#             if "Counties" in geographies:
#                 county_name = geographies["Counties"][0]["NAME"]
#                 nyc_counties = ["Richmond", "Kings", "Queens", "New York", "Bronx"]
#                 if any(nyc_c in county_name for nyc_c in nyc_counties):
#                     print(f" -> Detected NYC Borough ({county_name} County). Mapping to NYC School District.")
#                     district_name = "New York City School District"
#                     geo_type = "school district (unified)"
        
#         if not district_name:
#             return "The coordinates provided do not fall within a mapped US public school district boundary."
            
#         print(f" -> Census Layer Match: {district_name}")
        
#     except Exception as e:
#         return f"Census Geocoder boundary mapping failed: {e}"

#     # 2. Fetch Income Data from the ACS Financial Tables
#     print(f"\nStep 2: Pulling economic metrics from Census Bureau for '{district_name}'...")
    
#     # Clean up the string to match row indexing text
#     core_search_name = district_name.lower().replace("school district", "").split("[")[0].strip()
    
#     time.sleep(1) # Polite API rate-limit pause
    
#     census_url = f"https://api.census.gov/data/2022/acs/acs5?get=NAME,B19013_001E&for={geo_type}:*&in=state:{state_fips}"
#     if census_key:
#         census_url += f"&key={census_key}"
        
#     try:
#         res = requests.get(census_url)
#         if res.status_code != 200:
#             return f"Census Data API rejected the request with status code {res.status_code}."
            
#         census_data = res.json()
#         df = pd.DataFrame(census_data[1:], columns=census_data[0]).rename(columns={"B19013_001E": "Median_Income"})
        
#         # Isolate our specific row match
#         matched = df[df['NAME'].str.lower().str.contains(core_search_name)]
        
#         if not matched.empty:
#             return {
#                 "Coordinates Given": f"{lat}, {lon}",
#                 "Census District Bound": matched.iloc[0]['NAME'],
#                 "Median Household Income": f"${int(matched.iloc[0]['Median_Income']):,}"
#             }
#         else:
#             return f"Found district '{district_name}', but couldn't isolate its row entry in the financial table."
            
#     except Exception as e:
#         return f"Final execution phase failed: {e}"


# def get_income_by_school(school_query, state_abbr="NY", state_fips="36", census_key=None):
#     """
#     Chains the NCES API and Census API together to map a single school to its local household income.
#     """
#     print(f"Step 1: Looking up school tracking data for '{school_query}'...")
#     school_info = get_district_from_school_name(school_query, state_abbr)
    
#     if not school_info:
#         return f"Could not find a public school matching '{school_query}' in state {state_abbr}."
        
#     official_school = school_info["official_school_name"]
#     district_target = school_info["parent_district"]
    
#     print(f" -> Found School: {official_school}")
#     print(f" -> Parent District: {district_target}")
#     print(f"\nStep 2: Pulling economic metrics from Census Bureau for district...")
    
#     # Clean the district text for Census mapping
#     # NCES names often end in "School District", but Census might expect "Central School District"
#     # Extracting the core identifier token handles the translation smoothly
#     if "NEW YORK CITY GEOGRAPHIC DISTRICT" in district_target.upper():
#         core_search_name = "new york city school district"
#     else:
#         # For standard suburban/rural towns (e.g. Smithtown), extract the primary name
#         core_search_name = district_target.lower().replace("school district", "").split()[0].strip()
    
#     geo_types = ["school district (unified)", "school district (secondary)"]
    
#     for geo_type in geo_types:
#         census_url = f"https://api.census.gov/data/2022/acs/acs5?get=NAME,B19013_001E&for={geo_type}:*&in=state:{state_fips}"
#         if census_key:
#             census_url += f"&key={census_key}"
            
#         try:
#             res = requests.get(census_url)
#             if res.status_code != 200:
#                 continue
                
#             census_data = res.json()
#             df = pd.DataFrame(census_data[1:], columns=census_data[0]).rename(columns={"B19013_001E": "Median_Income"})
            
#             matched = df[df['NAME'].str.lower().str.contains(core_search_name)]
            
#             if not matched.empty:
#                 return {
#                     "School Searched": official_school,
#                     "Assigned District": matched.iloc[0]['NAME'],
#                     "Median Household Income": f"${int(matched.iloc[0]['Median_Income']):,}"
#                 }
#         except Exception:
#             continue
            
#     return f"Found the school district '{district_target}', but Census database lookup timed out or failed."

# # --- Execution Block ---
# if __name__ == "__main__":
#     # Paste your valid free Census API key here
#     MY_CENSUS_KEY = "49aa50d23f36182c911596a996ca243ca3ce11f2"
    
#     df = pd.read_csv(INPUT_DIR)

#     # Example 1: Coordinates for Tottenville High School (NYC Exception case)
#     tottenville_lat = 44.7886695861816
#     tottenville_lon = -75.1535415649414
    
#     # Example 2: Coordinates for Smithtown High School West (Standard Suburban Unified case)
#     # smithtown_lat = 40.85764
#     # smithtown_lon = -73.23842
    

#     for index, row in df.iterrows():
#         lat = row['latitude']
#         lon = row['longitude']

#         print(f"\nProcessing row {index + 1} with coordinates: {lat}, {lon}")
#         result = get_income_by_coordinates(lat, lon, state_fips="36", census_key=MY_CENSUS_KEY)
#         print(f"Result for row {index + 1}:", result)