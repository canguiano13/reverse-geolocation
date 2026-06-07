import csv
import time
import requests

SCHOOLS_FILE = "data/inputs/schools_selected.csv"
OUTPUT_FILE  = "data/outputs/probe_coverage.csv"
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
                "status": 1, "radius": f"{lat},{lon}:{NEAR_KM}", "fields": "id", "page_size": 1,
            })
            count = r.json().get("count", 0)
        except Exception:
            count = 0

        results.append({
            "school_name":        name,
            "latitude":           lat,
            "longitude":          lon,
            "probes_within_40km": count,
            "has_probes":         "yes" if count > 0 else "no",
        })
        marker = "ok" if count > 0 else "--"
        print(f"[{i:3}/{len(schools)}] {marker} {name[:50]:<50}  {count} probes")
        time.sleep(0.15)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "latitude", "longitude",
                                               "probes_within_40km", "has_probes"])
        writer.writeheader()
        writer.writerows(results)

    covered = sum(1 for r in results if r["has_probes"] == "yes")
    print(f"\nDone -> {OUTPUT_FILE}")
    print(f"Schools with probes within {NEAR_KM} km: {covered}/{len(results)} ({covered/len(results):.0%})")


if __name__ == "__main__":
    run()
