"""
Phase 1 — Geo Lookup
--------------------
Scans geolocation database(s) and finds every IP block (/24) within
RADIUS_KM kilometers of a school. Writes those blocks to a CSV so
Phase 2 can do reverse DNS lookups on them.

Supports two geolocation databases (the paper uses IPinfo + Maxmind).
Using two databases and taking their UNION significantly increases
coverage — the paper found 66% more libraries at 50km using both vs
Maxmind alone. Set DB_FILE_2 to a second mmdb file to enable this.

DB_FILE_2 (optional second database):
  Download the free DB-IP city lite mmdb from:
  https://db-ip.com/db/download/ip-to-city-lite
  Place it at data/inputs/dbip-city-lite.mmdb
"""

import csv
import ipaddress
import math
import os
import maxminddb

SCHOOLS_FILE = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE  = "data/outputs/phase1_candidates.csv"
DB_FILE      = "data/inputs/GeoLite2-City.mmdb"
DB_FILE_2    = "data/inputs/dbip-city-lite.mmdb"   # optional — leave as-is if not downloaded
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


def get_lat_lon(record):
    """
    Extract latitude and longitude from a geo database record.
    Handles both GeoLite2 format (nested under 'location') and
    DB-IP format (flat fields at the top level).
    """
    # GeoLite2 format
    loc = record.get("location") or {}
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is not None and lon is not None:
        return lat, lon
    # DB-IP / flat format
    lat = record.get("latitude")
    lon = record.get("longitude")
    return lat, lon


def scan_database(db_path, school_boxes, radius_km, seen_cidrs):
    """
    Scan one geolocation database and return (cidr, school_name) pairs
    for all blocks within radius_km of any school.
    seen_cidrs is updated in-place to track already-found blocks.
    """
    new_results = []
    with maxminddb.open_database(db_path) as db:
        for network, record in db:
            cidr   = str(network)
            ip_lat, ip_lon = get_lat_lon(record)

            if ip_lat is None or ip_lon is None:
                continue

            # Skip blocks larger than /24 (more than 256 IPs)
            try:
                if ipaddress.ip_network(cidr, strict=False).prefixlen < 24:
                    continue
            except ValueError:
                continue

            for s in school_boxes:
                # Fast bounding box check before expensive distance calculation
                if not (s["min_lat"] <= ip_lat <= s["max_lat"]
                        and s["min_lon"] <= ip_lon <= s["max_lon"]):
                    continue
                if distance_km(s["lat"], s["lon"], ip_lat, ip_lon) > radius_km:
                    continue

                key = (cidr, s["name"])
                if key not in seen_cidrs:
                    seen_cidrs.add(key)
                    new_results.append({"cidr": cidr, "school_name": s["name"]})
                    s["count"] += 1

    return new_results


def run(radius_km=RADIUS_KM, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    # Step 1: Load schools (skip any with no name)
    with open(schools_file, newline="", encoding="utf-8") as f:
        schools = [r for r in csv.DictReader(f)
                   if r["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(schools)} schools")

    # Step 2: Pre-compute a bounding box around each school.
    # Box check is O(1) and fast; distance formula only runs if box passes.
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

    # Step 3: Scan database(s).
    # If a second database file exists, scan both and take the union.
    # The paper found using IPinfo + Maxmind gave 66% more libraries at 50km.
    seen_cidrs = set()   # tracks (cidr, school_name) pairs already found
    all_results = []

    print(f"Scanning {DB_FILE} ...")
    results_1 = scan_database(DB_FILE, school_boxes, radius_km, seen_cidrs)
    all_results.extend(results_1)
    print(f"  {len(results_1)} blocks found")

    if os.path.exists(DB_FILE_2):
        print(f"Scanning {DB_FILE_2} (second database) ...")
        results_2 = scan_database(DB_FILE_2, school_boxes, radius_km, seen_cidrs)
        all_results.extend(results_2)
        print(f"  {len(results_2)} additional blocks found (not in first database)")
    else:
        print(f"Note: second database not found at {DB_FILE_2}")
        print(f"      Download DB-IP city lite from https://db-ip.com/db/download/ip-to-city-lite")
        print(f"      to significantly increase coverage (the paper found 66% more IPs using two databases)")

    # Step 4: Write results
    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["cidr", "school_name"])
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\nDone. {len(all_results)} total blocks written to {output_file}")


if __name__ == "__main__":
    run()
