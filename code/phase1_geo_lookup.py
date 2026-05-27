"""
Phase 1 — Geo Lookup
--------------------
Scans the GeoLite2 database and finds every IP block (/24) that is
within RADIUS_KM kilometers of a school. Writes those blocks to a CSV
so phase 2 can do reverse DNS lookups on them.
"""

import csv
import ipaddress
import math
import maxminddb

SCHOOLS_FILE = "data/inputs/schools_selected.csv"
OUTPUT_FILE  = "data/outputs/phase1_candidates.csv"
DB_FILE      = "data/inputs/GeoLite2-City.mmdb"
RADIUS_KM    = 10


def distance_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two GPS coordinates (in km)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def run(radius_km=RADIUS_KM, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    # Step 1: Load schools (skip any with no name)
    with open(schools_file, newline="", encoding="utf-8") as f:
        schools = [r for r in csv.DictReader(f)
                   if r["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(schools)} schools")

    # Step 2: Pre-compute a rough bounding box around each school.
    # We check the box first (fast) then confirm with exact distance (slow).
    # This avoids running the distance formula on every IP in the database.
    school_boxes = []
    for s in schools:
        lat  = float(s["latitude"])
        lon  = float(s["longitude"])
        name = s["school_name"].strip()
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
        school_boxes.append({
            "name": name, "lat": lat, "lon": lon,
            "min_lat": lat - dlat, "max_lat": lat + dlat,
            "min_lon": lon - dlon, "max_lon": lon + dlon,
            "count": 0,
        })

    # Step 3: Scan the entire GeoLite2 database once.
    # For each IP block in the database, check if it falls near any school.
    total_written = 0
    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["cidr", "school_name"])
        writer.writeheader()

        with maxminddb.open_database(DB_FILE) as db:
            for network, record in db:
                cidr = str(network)
                loc  = record.get("location", {})
                ip_lat = loc.get("latitude")
                ip_lon = loc.get("longitude")

                # skip blocks with no location or that are too large to scan
                if ip_lat is None or ip_lon is None:
                    continue
                try:
                    prefix_len = ipaddress.ip_network(cidr, strict=False).prefixlen
                    if prefix_len < 24:
                        continue   # block is too large (more than 256 IPs)
                except ValueError:
                    continue

                # check each school
                for s in school_boxes:
                    # quick box check first
                    if not (s["min_lat"] <= ip_lat <= s["max_lat"]
                            and s["min_lon"] <= ip_lon <= s["max_lon"]):
                        continue
                    # exact distance check
                    if distance_km(s["lat"], s["lon"], ip_lat, ip_lon) > radius_km:
                        continue

                    writer.writerow({"cidr": cidr, "school_name": s["name"]})
                    s["count"] += 1
                    total_written += 1

    # Step 4: Print a summary
    for s in school_boxes:
        print(f"{s['name'][:45]:<45}  {s['count']} blocks")
    print(f"\nDone. {total_written} total blocks written to {output_file}")


if __name__ == "__main__":
    run()
