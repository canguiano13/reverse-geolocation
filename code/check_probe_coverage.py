"""
Probe Coverage Check
---------------------
For each school in our sample, counts how many active RIPE Atlas probes
are within 40 km. Saves results to data/probe_coverage.csv.

Run this once to generate the coverage table for the paper.
"""

import csv
import math
import time
import requests

SCHOOLS_FILE = "data/schools_selected.csv"
OUTPUT_FILE  = "data/probe_coverage.csv"
NEAR_KM      = 40


def run():
    with open(SCHOOLS_FILE, newline="", encoding="utf-8") as f:
        schools = list(csv.DictReader(f))

    print(f"Checking {len(schools)} schools for nearby RIPE Atlas probes (within {NEAR_KM} km)...")

    results = []
    for i, s in enumerate(schools, 1):
        lat  = float(s["latitude"])
        lon  = float(s["longitude"])
        name = s["school_name"].strip()

        try:
            r = requests.get("https://atlas.ripe.net/api/v2/probes/", timeout=10, params={
                "status":    1,
                "radius":    f"{lat},{lon}:{NEAR_KM}",
                "fields":    "id",
                "page_size": 1,   # we only need the count, not the actual probes
            })
            count = r.json().get("count", 0)
        except Exception:
            count = 0

        has_probes = "yes" if count > 0 else "no"
        results.append({
            "school_name": name,
            "latitude":    lat,
            "longitude":   lon,
            "probes_within_40km": count,
            "has_probes":  has_probes,
        })

        marker = "✓" if count > 0 else "✗"
        print(f"[{i:3}/{len(schools)}] {marker} {name[:50]:<50}  {count} probes")
        time.sleep(0.15)

    # Save to CSV
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "latitude", "longitude",
                                               "probes_within_40km", "has_probes"])
        writer.writeheader()
        writer.writerows(results)

    # Summary
    covered = sum(1 for r in results if r["has_probes"] == "yes")
    total   = len(results)
    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"Schools with probes within {NEAR_KM} km: {covered}/{total} ({covered/total:.0%})")


if __name__ == "__main__":
    run()
