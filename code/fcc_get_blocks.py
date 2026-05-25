import csv
import requests

INPUT_FILE  = "data/schools_selected.csv"
OUTPUT_FILE = "data/school_blocks.csv"


# asks the FCC which census block a GPS coordinate falls in
def get_census_block(lat, lon):
    url = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lon}&format=json"
    try:
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
    print(f"\nDone. {found}/{len(results)} blocks found. Saved to {OUTPUT_FILE}")
