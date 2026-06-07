import csv
import math
import random

INPUT_FILE  = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE = "data/inputs/schools_selected.csv"

GRID_SIZE = 9
N_SAMPLE  = 250

NY_LAT_MIN, NY_LAT_MAX =  40.5,  45.05
NY_LON_MIN, NY_LON_MAX = -79.8, -73.0


def get_grid_cell(lat, lon):
    lat_ratio = max(0, min(0.9999, (lat - NY_LAT_MIN) / (NY_LAT_MAX - NY_LAT_MIN)))
    lon_ratio = max(0, min(0.9999, (lon - NY_LON_MIN) / (NY_LON_MAX - NY_LON_MIN)))
    return (int(lat_ratio * GRID_SIZE), int(lon_ratio * GRID_SIZE))


def sample_schools(schools, n=N_SAMPLE):
    random.shuffle(schools)

    grid = {}
    for school in schools:
        lat = float(school["latitude"])
        lon = float(school["longitude"])
        if not (NY_LAT_MIN <= lat <= NY_LAT_MAX and NY_LON_MIN <= lon <= NY_LON_MAX):
            continue
        cell = get_grid_cell(lat, lon)
        grid.setdefault(cell, []).append(school)

    per_cell = max(1, n // (GRID_SIZE * GRID_SIZE))

    sampled = []
    for bucket in grid.values():
        random.shuffle(bucket)
        sampled.extend(bucket[:per_cell])

    if len(sampled) < n:
        remaining = [s for s in schools if s not in sampled]
        random.shuffle(remaining)
        sampled.extend(remaining[:n - len(sampled)])

    return sorted(sampled[:n], key=lambda s: (float(s["latitude"]), float(s["longitude"])))


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, seed=None):
    if seed is not None:
        random.seed(seed)

    with open(input_file, newline="", encoding="utf-8") as f:
        all_schools = list(csv.DictReader(f))

    schools = [
        s for s in all_schools
        if s["school_name"].lower() != "name unknown"
        and s["education_level"] != "Unknown"
    ]
    print(f"Input: {len(all_schools)} schools, {len(schools)} after filtering unknowns")

    sampled = sample_schools(schools)
    print(f"Sampled: {len(sampled)} schools across NY State grid")

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=sampled[0].keys())
        writer.writeheader()
        writer.writerows(sampled)

    print(f"Done -> {output_file}")


if __name__ == "__main__":
    run()
