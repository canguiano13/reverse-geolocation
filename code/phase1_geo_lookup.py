import csv
import ipaddress
import math
import maxminddb

INPUT_FILE  = "data/schools_selected.csv"
OUTPUT_FILE = "data/phase1_candidates.csv"
DB_FILE     = "data/GeoLite2-City.mmdb"
# RADIUS_KM   = 20 
RADIUS_KM   = 10


# rough square around point to quickly narrow down candidates
def search_area(lat, lon, radius_km):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon

# distance btwn 2 coords in km
def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))

# skip very large IP blocks that take too long to scan
def cidr_in_range(cidr):
    try:
        return ipaddress.ip_network(cidr, strict=False).prefixlen >= 24
    except ValueError:
        return False


if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        schools = list(csv.DictReader(f))

    schools = [s for s in schools if s["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(schools)} schools from {INPUT_FILE}")

    # precompute a search area around each school
    school_data = []
    for s in schools:
        lat  = float(s["latitude"])
        lon  = float(s["longitude"])
        name = s["school_name"].strip()
        min_lat, max_lat, min_lon, max_lon = search_area(lat, lon, RADIUS_KM)
        school_data.append((name, lat, lon, min_lat, max_lat, min_lon, max_lon))

    counts = [0] * len(school_data)
    rows_written = 0

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["cidr", "school_name"])
        writer.writeheader()

        # go through the database once and check all schools at the same time
        with maxminddb.open_database(DB_FILE) as reader:
            for network, record in reader:
                location = record.get("location", {})
                rec_lat  = location.get("latitude")
                rec_lon  = location.get("longitude")
                cidr = str(network)

                if (rec_lat is None) or (rec_lon is None) or (not cidr_in_range(cidr)):
                    continue

                for idx, (name, lat, lon, min_lat, max_lat, min_lon, max_lon) in enumerate(school_data):
                    
                    # first check the rough box, then confirm w exact distance
                    if not (min_lat <= rec_lat <= max_lat and min_lon <= rec_lon <= max_lon):
                        continue
                    if distance_km(lat, lon, rec_lat, rec_lon) > RADIUS_KM:
                        continue

                    writer.writerow({"cidr": cidr, "school_name": name})
                    counts[idx] += 1
                    rows_written += 1

    for idx, (name, *_) in enumerate(school_data):
        print(f"{name[:45]:<45}  {counts[idx]} blocks")

    print(f"\nDone. {rows_written} total blocks written to {OUTPUT_FILE}")
    