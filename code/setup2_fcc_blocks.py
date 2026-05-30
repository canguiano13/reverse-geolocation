"""
FCC Census Block Lookup

For each school's GPS coordinates, ask the FCC API which census block it falls in.
That block ID is used by fcc_get_providers.py to find ISPs serving the area.

Input:  data/schools_selected.csv
Output: data/school_blocks.csv
"""

import csv
import requests

INPUT_FILE  = "data/schools_selected.csv"
OUTPUT_FILE = "data/school_blocks.csv"


def get_census_block(lat, lon):
    """Return the FCC census block FIPS code for a GPS coordinate."""
    try:
        url      = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lon}&format=json"
        response = requests.get(url, timeout=5).json()
        return response["Block"]["FIPS"]
    except Exception:
        return None


if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        schools = list(csv.DictReader(f))

    print(f"Fetching census blocks for {len(schools)} schools")

    results = []
    for i, school in enumerate(schools, 1):
        name  = school["school_name"].strip()
        lat   = float(school["latitude"])
        lon   = float(school["longitude"])
        block = get_census_block(lat, lon)
        results.append({"school_name": name, "census_block": block or ""})
        print(f"{i}/{len(schools)}  {name[:45]:<45}  {block or 'not found'}")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "census_block"])
        writer.writeheader()
        writer.writerows(results)

    found = sum(1 for r in results if r["census_block"])
    print(f"\nDone. {found}/{len(results)} blocks found {OUTPUT_FILE}")
