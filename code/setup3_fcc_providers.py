"""
FCC Provider Lookup

For each school's census block, find which ISPs offer broadband service there.
Uses FCC broadband deployment data (download for NY from broadbandmap.fcc.gov).

Input:  data/school_blocks.csv   (from fcc_get_blocks.py)
Output: data/school_providers.csv
"""

import csv
import pandas as pd

INPUT_FILE  = "data/school_blocks.csv"
OUTPUT_FILE = "data/school_providers.csv"

# FCC broadband deployment CSVs for New York state
FCC_DATA_FILES = [
    "data/bdc_36_Cable_fixed_broadband_D25_04may2026.csv",
    "data/bdc_36_FibertothePremises_fixed_broadband_D25_04may2026.csv",
]


def get_providers(census_tract):
    """Return all ISP brand names serving a census tract (first 11 digits of block code)."""
    providers = set()
    for file_path in FCC_DATA_FILES:
        try:
            df = pd.read_csv(file_path, usecols=["block_geoid", "brand_name"],
                             dtype={"block_geoid": str, "brand_name": str})
            matches = df[df["block_geoid"].str.startswith(census_tract, na=False)]
            providers.update(matches["brand_name"].dropna().unique())
        except FileNotFoundError:
            print(f"Warning: {file_path} not found, skipping")
    return list(providers)


if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        schools = list(csv.DictReader(f))

    print(f"Looking up providers for {len(schools)} schools")

    results = []
    for i, school in enumerate(schools, 1):
        name  = school["school_name"].strip()
        block = school["census_block"].strip()

        if not block:
            results.append({"school_name": name, "providers": ""})
            print(f"{i}/{len(schools)}  {name[:45]:<45}  no block code")
            continue

        providers = get_providers(block[:11])   # first 11 digits = census tract
        results.append({"school_name": name, "providers": "|".join(providers)})
        print(f"{i}/{len(schools)}  {name[:45]:<45}  {len(providers)} providers")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "providers"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone {OUTPUT_FILE}")
