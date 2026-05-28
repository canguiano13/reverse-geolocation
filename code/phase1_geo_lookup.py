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

Memory design: results are streamed directly to disk — no large lists
kept in RAM. A spatial grid index limits each DB record to checking
only nearby schools instead of all 12k. A NY state bounding box
pre-filters blocks that are clearly outside New York.

DB_FILE_2 (optional second database):
  Download the free DB-IP city lite mmdb from:
  https://db-ip.com/db/download/ip-to-city-lite
  Place it at data/inputs/dbip-city-lite.mmdb
"""

import csv
import ipaddress
import math
import os
from collections import defaultdict
import maxminddb

SCHOOLS_FILE = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE  = "data/outputs/phase1_candidates.csv"
DB_FILE      = "data/inputs/GeoLite2-City.mmdb"
DB_FILE_2    = "data/inputs/dbip-city-lite.mmdb"   # optional — leave as-is if not downloaded
RADIUS_KM    = 10

# Spatial grid cell size in degrees. 0.5° ≈ 55km, so a 10km radius
# only needs to search the immediate grid cell.
GRID_DEG = 0.5

# NY state bounding box — skip IP blocks geolocated clearly outside New York.
# This is the single biggest space/time saver: drops ~90% of global blocks early.
NY_LAT_MIN, NY_LAT_MAX =  40.4,  45.1
NY_LON_MIN, NY_LON_MAX = -79.9, -71.7


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
    loc = record.get("location") or {}
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is not None and lon is not None:
        return lat, lon
    lat = record.get("latitude")
    lon = record.get("longitude")
    return lat, lon


def build_grid(school_boxes, grid_deg):
    """
    Index schools into a spatial grid so we can quickly find candidate
    schools for any given lat/lon without scanning all 12k schools.
    Each school is stored in every grid cell its bounding box overlaps.
    Returns a dict: (grid_row, grid_col) → list of school_box dicts.
    """
    grid = defaultdict(list)
    for s in school_boxes:
        # Find the range of grid cells this school's bounding box covers
        row_min = int(math.floor(s["min_lat"] / grid_deg))
        row_max = int(math.floor(s["max_lat"] / grid_deg))
        col_min = int(math.floor(s["min_lon"] / grid_deg))
        col_max = int(math.floor(s["max_lon"] / grid_deg))
        for row in range(row_min, row_max + 1):
            for col in range(col_min, col_max + 1):
                grid[(row, col)].append(s)
    return grid


def scan_database(db_path, school_grid, grid_deg, radius_km, seen_cidrs, writer):
    """
    Scan one geolocation database.
    Results are written directly to `writer` (CSV) — no in-memory list.
    seen_cidrs (set of (cidr, school_name)) is updated in-place.
    Returns count of new rows written.
    """
    count = 0
    with maxminddb.open_database(db_path) as db:
        for network, record in db:
            cidr = str(network)
            ip_lat, ip_lon = get_lat_lon(record)

            if ip_lat is None or ip_lon is None:
                continue

            # Skip blocks clearly outside New York state
            if not (NY_LAT_MIN <= ip_lat <= NY_LAT_MAX
                    and NY_LON_MIN <= ip_lon <= NY_LON_MAX):
                continue

            # Skip blocks larger than /24 (more than 256 IPs)
            try:
                if ipaddress.ip_network(cidr, strict=False).prefixlen < 24:
                    continue
            except ValueError:
                continue

            # Find which grid cell this IP belongs to
            cell_row = int(math.floor(ip_lat / grid_deg))
            cell_col = int(math.floor(ip_lon / grid_deg))

            # Only check schools whose bounding box overlaps this grid cell
            candidates = school_grid.get((cell_row, cell_col), [])
            for s in candidates:
                # Bounding box pre-filter (already guaranteed by grid, but cheap double-check)
                if not (s["min_lat"] <= ip_lat <= s["max_lat"]
                        and s["min_lon"] <= ip_lon <= s["max_lon"]):
                    continue
                if distance_km(s["lat"], s["lon"], ip_lat, ip_lon) > radius_km:
                    continue

                if cidr not in seen_cidrs:
                    seen_cidrs.add(cidr)
                    writer.writerow({"cidr": cidr, "school_name": s["name"]})
                    s["count"] += 1
                    count += 1

    return count


def run(radius_km=RADIUS_KM, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    # Step 1: Load schools (skip any with no name)
    with open(schools_file, newline="", encoding="utf-8") as f:
        schools = [r for r in csv.DictReader(f)
                   if r["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(schools)} schools")

    # Step 2: Pre-compute bounding boxes + build spatial grid.
    # Grid lookup is O(1) per DB record; only nearby schools are checked.
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

    school_grid = build_grid(school_boxes, GRID_DEG)
    print(f"Spatial grid: {len(school_grid)} cells covering {len(school_boxes)} schools")

    # Step 3: Stream results directly to disk as we scan.
    # seen_cidrs deduplicates across both databases.
    seen_cidrs = set()
    total = 0

    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["cidr", "school_name"])
        writer.writeheader()

        print(f"Scanning {DB_FILE} ...")
        n1 = scan_database(DB_FILE, school_grid, GRID_DEG, radius_km, seen_cidrs, writer)
        total += n1
        print(f"  {n1} blocks found")

        if os.path.exists(DB_FILE_2):
            print(f"Scanning {DB_FILE_2} (second database) ...")
            n2 = scan_database(DB_FILE_2, school_grid, GRID_DEG, radius_km, seen_cidrs, writer)
            total += n2
            print(f"  {n2} additional blocks found (not in first database)")
        else:
            print(f"Note: second database not found at {DB_FILE_2}")
            print(f"      Download DB-IP city lite from https://db-ip.com/db/download/ip-to-city-lite")
            print(f"      to significantly increase coverage (paper found 66% more IPs using two databases)")

    print(f"\nDone. {total} total blocks written to {output_file}")


if __name__ == "__main__":
    run()
