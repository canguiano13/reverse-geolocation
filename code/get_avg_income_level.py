import os
import time
import pandas as pd
import requests

CENSUS_KEY    = "49aa50d23f36182c911596a996ca243ca3ce11f2"
INPUT_PATH    = "data/inputs/schools_selected.csv"
OUTPUT_PATH   = "data/outputs/schools_with_income.csv"
COMBINED_PATH = "data/outputs/combined_results_20km.csv"
ENRICHED_PATH = "data/outputs/combined_results_with_income_20km.csv"

def get_county_income_for_row(lat, lon, state_fips="36", census_key=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    }

    clean_lat = round(float(lat), 5)
    clean_lon = round(float(lon), 5)

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
        county_fips = county_info["COUNTY"]

        #query ACS financial tables for county
        #var B19013_001E is Median Household Income for county
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
        # handle bug carlos had
        try:
            incomes.append(float(income.replace("$", "").replace(",", "")))
        except ValueError:
            incomes.append(None)

        time.sleep(0.5)

    df['census_county'] = counties
    df['county_median_household_income'] = incomes

    df.to_csv(output_csv, index=False)
    print(f"\nProcessing complete! Results saved to output: {output_csv}")


def join_with_combined_results(income_csv, combined_csv, output_csv):
    if not os.path.exists(combined_csv):
        print(f"Skipping join: {combined_csv} not found.")
        return

    income_df   = pd.read_csv(income_csv)[["school_name", "census_county", "county_median_household_income"]]
    combined_df = pd.read_csv(combined_csv)

    enriched = combined_df.merge(income_df, on="school_name", how="left")
    enriched.to_csv(output_csv, index=False)
    print(f"Joined income data into combined results -> {output_csv}")


if __name__ == "__main__":

    if os.path.exists(INPUT_PATH):
        batch_process_counties(INPUT_PATH, OUTPUT_PATH, census_key=CENSUS_KEY, state_fips="36")
        join_with_combined_results(OUTPUT_PATH, COMBINED_PATH, ENRICHED_PATH)
    else:
        print(f"Error: Could not locate your input file '{INPUT_PATH}'.")
