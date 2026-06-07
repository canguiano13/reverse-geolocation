# per-school IP coverage from phase3 output

import pandas as pd
from math import radians, cos, sin, asin, sqrt

SCHOOLS_FILE  = "data/inputs/schools_selected.csv"
PHASE3_FILE   = "data/outputs/phase3_confirmed_20km.csv"
OUTPUT_FILE   = "data/outputs/school_coverage.csv"

# Approximate center coordinates for ARIN districts that don't appear in
# schools_selected.csv by name. Schools within DISTRICT_RADIUS_KM of the
# center are considered part of that district.
ARIN_DISTRICTS = {
    "Beacon Central School District":        (41.5034, -73.9843),
    "Monroe-Woodbury Central School District": (41.3209, -74.1862),
    "Sachem Central School District":        (40.7929, -73.0829),
}
DISTRICT_RADIUS_KM = 25


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    a = (sin((lat2-lat1)/2)**2
         + cos(lat1) * cos(lat2) * sin((lon2-lon1)/2)**2)
    return R * 2 * asin(sqrt(a))


def summarize(matches):
    high   = int((matches["confidence"] == "high").sum())
    medium = int((matches["confidence"] == "medium").sum())
    low    = int((matches["confidence"] == "low").sum())
    total  = high + medium + low
    if total == 0:
        best = "none"
    elif high > 0:
        best = "high"
    elif medium > 0:
        best = "medium"
    else:
        best = "low"
    return total, high, medium, low, best


def run(schools_file=SCHOOLS_FILE, phase3_file=PHASE3_FILE, output_file=OUTPUT_FILE):
    schools = pd.read_csv(schools_file)
    phase3  = pd.read_csv(phase3_file)

    rows = []
    for _, school in schools.iterrows():
        name = school["school_name"]
        lat  = school["latitude"]
        lon  = school["longitude"]

        # Strategy 1: direct name match
        matches = phase3[phase3["school_name"] == name]
        if len(matches) > 0:
            total, high, medium, low, best = summarize(matches)
            rows.append({
                "school_name":     name,
                "ips_found":       total,
                "high":            high,
                "medium":          medium,
                "low":             low,
                "best_confidence": best,
                "source":          "direct",
            })
            continue

        # Strategy 2: geographic match to ARIN district
        district_match = None
        for district_name, (dlat, dlon) in ARIN_DISTRICTS.items():
            if haversine(lat, lon, dlat, dlon) <= DISTRICT_RADIUS_KM:
                district_match = district_name
                break

        if district_match:
            matches = phase3[phase3["school_name"] == district_match]
            total, high, medium, low, best = summarize(matches)
            rows.append({
                "school_name":     name,
                "ips_found":       total,
                "high":            high,
                "medium":          medium,
                "low":             low,
                "best_confidence": best,
                "source":          f"district ({district_match})",
            })
        else:
            rows.append({
                "school_name":     name,
                "ips_found":       0,
                "high":            0,
                "medium":          0,
                "low":             0,
                "best_confidence": "none",
                "source":          "none",
            })

    df = pd.DataFrame(rows)
    df.to_csv(output_file, index=False)

    direct   = (df["source"] == "direct").sum()
    district = df["source"].str.startswith("district").sum()
    none     = (df["source"] == "none").sum()

    print(f"Direct name match      : {direct} schools")
    print(f"District (ARIN) match  : {district} schools")
    print(f"No results found       : {none} schools")
    print(f"Output -> {output_file}")


if __name__ == "__main__":
    run()
