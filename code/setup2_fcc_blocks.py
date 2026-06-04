"""FCC census block lookup - resolve each school's GPS coordinates to a census block FIPS code."""

import csv
import requests

INPUT_FILE  = "data/inputs/schools_selected.csv"
OUTPUT_FILE = "data/inputs/school_blocks.csv"


def get_census_block(lat, lon):
    try:
        url  = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lon}&format=json"
        resp = requests.get(url, timeout=5).json()
        return resp["Block"]["FIPS"]
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
