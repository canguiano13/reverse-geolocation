"""Phase 1: scan GeoLite2 and IPinfo location for every /24 block within RADIUS_KM of any school."""

import csv
import datetime
import gzip
import ipaddress
import math
from collections import defaultdict
import maxminddb

SCHOOLS_FILE          = "data/inputs/schools_selected.csv"
OUTPUT_FILE           = "data/outputs/phase1_candidates.csv"
DB_FILE               = "data/inputs/GeoLite2-City.mmdb"
IPINFO_LOCATION_FILE  = "data/inputs/ipinfo/ipinfo_location.csv.gz"
RADIUS_KM             = 10
GRID_DEG              = 0.5  # ~55km cells

NY_LAT_MIN, NY_LAT_MAX =  40.4,  45.1
NY_LON_MIN, NY_LON_MAX = -79.9, -71.7


def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def build_grid(school_boxes, grid_deg):
    grid = defaultdict(list)
    for s in school_boxes:
        row_min = int(math.floor(s["min_lat"] / grid_deg))
        row_max = int(math.floor(s["max_lat"] / grid_deg))
        col_min = int(math.floor(s["min_lon"] / grid_deg))
        col_max = int(math.floor(s["max_lon"] / grid_deg))
        for row in range(row_min, row_max + 1):
            for col in range(col_min, col_max + 1):
                grid[(row, col)].append(s)
    return grid


def scan_database(db_path, school_grid, grid_deg, radius_km, seen_cidrs, writer):
    count = 0
    with maxminddb.open_database(db_path) as db:
        for network, record in db:
            cidr   = str(network)
            loc    = record.get("location") or {}
            ip_lat = loc.get("latitude")
            ip_lon = loc.get("longitude")
            if ip_lat is None or ip_lon is None:
                ip_lat = record.get("latitude")
                ip_lon = record.get("longitude")

            if ip_lat is None or ip_lon is None:
                continue
            if not (NY_LAT_MIN <= ip_lat <= NY_LAT_MAX and NY_LON_MIN <= ip_lon <= NY_LON_MAX):
                continue

            try:
                if ipaddress.ip_network(cidr, strict=False).prefixlen < 24:
                    continue
            except ValueError:
                continue

            cell = (int(math.floor(ip_lat / grid_deg)), int(math.floor(ip_lon / grid_deg)))
            for s in school_grid.get(cell, []):
                if not (s["min_lat"] <= ip_lat <= s["max_lat"]
                        and s["min_lon"] <= ip_lon <= s["max_lon"]):
                    continue
                dist = distance_km(s["lat"], s["lon"], ip_lat, ip_lon)
                if dist > radius_km:
                    continue
                if cidr not in seen_cidrs:
                    seen_cidrs.add(cidr)
                    writer.writerow({"cidr": cidr, "school_name": s["name"],
                                     "distance_km": round(dist, 2)})
                    s["count"] += 1
                    count += 1
    return count


def scan_ipinfo_location(path, school_grid, grid_deg, radius_km, seen_cidrs, writer):
    count = 0
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                ip_lat = float(row['latitude'])
                ip_lon = float(row['longitude'])
            except (ValueError, KeyError):
                continue

            if not (NY_LAT_MIN <= ip_lat <= NY_LAT_MAX and NY_LON_MIN <= ip_lon <= NY_LON_MAX):
                continue

            cidr = row['network']
            try:
                if ipaddress.ip_network(cidr, strict=False).prefixlen < 24:
                    continue
            except ValueError:
                continue

            cell = (int(math.floor(ip_lat / grid_deg)), int(math.floor(ip_lon / grid_deg)))
            for s in school_grid.get(cell, []):
                if not (s["min_lat"] <= ip_lat <= s["max_lat"]
                        and s["min_lon"] <= ip_lon <= s["max_lon"]):
                    continue
                dist = distance_km(s["lat"], s["lon"], ip_lat, ip_lon)
                if dist > radius_km:
                    continue
                if cidr not in seen_cidrs:
                    seen_cidrs.add(cidr)
                    writer.writerow({"cidr": cidr, "school_name": s["name"],
                                     "distance_km": round(dist, 2)})
                    s["count"] += 1
                    count += 1
                break  # assign each CIDR to first matching school only
    return count


def run(radius_km=RADIUS_KM, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    with open(schools_file, newline="", encoding="utf-8") as f:
        schools = [r for r in csv.DictReader(f)
                   if r["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(schools)} schools")

    school_boxes = []
    for s in schools:
        lat  = float(s["latitude"])
        lon  = float(s["longitude"])
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
        school_boxes.append({
            "name": s["school_name"].strip(), "lat": lat, "lon": lon,
            "min_lat": lat - dlat, "max_lat": lat + dlat,
            "min_lon": lon - dlon, "max_lon": lon + dlon,
            "count": 0,
        })

    school_grid = build_grid(school_boxes, GRID_DEG)
    print(f"Spatial grid: {len(school_grid)} cells, {len(school_boxes)} schools")

    seen_cidrs = set()

    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["cidr", "school_name", "distance_km"])
        writer.writeheader()

        with maxminddb.open_database(DB_FILE) as _db:
            build_epoch = _db.metadata().build_epoch
            build_date  = datetime.datetime.utcfromtimestamp(build_epoch).strftime("%Y-%m-%d")
        print(f"GeoLite2-City build date: {build_date}  (record this for reproducibility)")
        print(f"Scanning {DB_FILE} ...")
        n_geo = scan_database(DB_FILE, school_grid, GRID_DEG, radius_km, seen_cidrs, writer)
        print(f"  {n_geo} blocks from GeoLite2")

        print(f"Scanning {IPINFO_LOCATION_FILE} ...")
        n_ip = scan_ipinfo_location(
            IPINFO_LOCATION_FILE, school_grid, GRID_DEG, radius_km, seen_cidrs, writer
        )
        print(f"  {n_ip} additional blocks from IPinfo (not in GeoLite2)")

    total = n_geo + n_ip
    print(f"\nDone. {total} blocks written to {output_file}  "
          f"(GeoLite2={n_geo}, IPinfo={n_ip})")


if __name__ == "__main__":
    run()
