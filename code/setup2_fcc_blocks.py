"""
FCC census block lookup.

For each school's GPS coordinates, ask the FCC API which census block it falls in.
That block ID is used by setup3_fcc_providers.py to find ISPs serving the area.

Input:  data/inputs/metro_schools_nyc.csv
Output: data/inputs/school_blocks.csv
"""

import csv
import requests

INPUT_FILE  = "data/inputs/metro_schools_nyc.csv"
OUTPUT_FILE = "data/inputs/school_blocks.csv"


def get_census_block(lat, lon):
    """FCC census block FIPS code for a coordinate, or None."""
    try:
        url      = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lon}&format=json"
        response = requests.get(url, timeout=5).json()
        return response["Block"]["FIPS"]
    except Exception:
        return None


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    with open(input_file, newline="", encoding="utf-8") as f:
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

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "census_block"])
        writer.writeheader()
        writer.writerows(results)

    found = sum(1 for r in results if r["census_block"])
    print(f"\nDone. {found}/{len(results)} blocks found -> {output_file}")


if __name__ == "__main__":
    run()
