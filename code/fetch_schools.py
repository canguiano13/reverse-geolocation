"""
fetch_schools.py — Grid-based geographic sampling of NY K-12 schools.

Divides NY into a 9x9 grid and samples ~250 schools proportionally from each
cell. Produces a geographically diverse set that avoids the NYC metro bias
of the full 13k-school list.

Input:  data/inputs/gigamaps_schools_ny.csv
Output: data/inputs/sampled_schools_grid.csv
"""

import csv
import random

INPUT_FILE  = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE = "data/inputs/sampled_schools_grid.csv"

MIN_LAT = 40.5
MAX_LAT = 45.05
MIN_LON = -79.8
MAX_LON = -73.0

GRID_SIZE   = 9
N_SCHOOLS   = 250
RANDOM_SEED = 42


def get_grid_cell(lat, lon):
    """Map (lat, lon) to a (row, col) grid index. Returns None if outside NY."""
    if not (MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lon <= MAX_LON):
        return None
    cell_lat = (MAX_LAT - MIN_LAT) / GRID_SIZE
    cell_lon = (MAX_LON - MIN_LON) / GRID_SIZE
    row = min(int((lat - MIN_LAT) / cell_lat), GRID_SIZE - 1)
    col = min(int((lon - MIN_LON) / cell_lon), GRID_SIZE - 1)
    return (row, col)


def build_grid(schools):
    """Bucket all schools into their grid cells."""
    grid    = {}
    outside = 0
    for school in schools:
        try:
            lat = float(school.get("lat") or school.get("latitude") or 0)
            lon = float(school.get("lon") or school.get("lng") or school.get("longitude") or 0)
        except (ValueError, TypeError):
            outside += 1
            continue
        cell = get_grid_cell(lat, lon)
        if cell is None:
            outside += 1
            continue
        grid.setdefault(cell, []).append(school)
    print(f"  In grid: {sum(len(v) for v in grid.values())}  Outside NY: {outside}  Cells: {len(grid)}")
    return grid


def sample_grid(grid, n_total, rng):
    """
    Sample from the grid:
    1. At least 1 school per non-empty cell (geographic coverage).
    2. Remaining slots allocated proportionally to cell size.
    3. Random pick within each cell.
    """
    cells         = sorted(grid.keys())
    base_alloc    = {cell: 1 for cell in cells}
    remaining     = max(0, n_total - len(cells))
    total_schools = sum(len(grid[c]) for c in cells)

    for cell in cells:
        base_alloc[cell] += round(remaining * len(grid[cell]) / total_schools)

    sampled = []
    for cell in cells:
        quota = min(base_alloc[cell], len(grid[cell]))
        sampled.extend(rng.sample(grid[cell], quota))

    if len(sampled) > n_total:
        sampled = rng.sample(sampled, n_total)
    elif len(sampled) < n_total:
        all_schools = [s for c in cells for s in grid[c]]
        already     = {id(s) for s in sampled}
        extras      = [s for s in all_schools if id(s) not in already]
        sampled.extend(rng.sample(extras, min(n_total - len(sampled), len(extras))))

    return sampled


def safe_lat(school):
    try:
        return float(school.get("lat") or school.get("latitude") or 0)
    except (ValueError, TypeError):
        return 0


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, n=N_SCHOOLS):
    print(f"Loading schools from {input_file} ...")
    with open(input_file, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        schools    = list(reader)
        fieldnames = reader.fieldnames
    print(f"  Total loaded: {len(schools)}")

    rng     = random.Random(RANDOM_SEED)
    grid    = build_grid(schools)
    sampled = sample_grid(grid, n, rng)
    sampled.sort(key=lambda s: -safe_lat(s))   # north / south

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sampled)

    # Print distribution by latitude band
    cell_lat = (MAX_LAT - MIN_LAT) / GRID_SIZE
    print("\nLatitude distribution:")
    for row_idx in range(GRID_SIZE - 1, -1, -1):
        lat_lo = MIN_LAT + row_idx * cell_lat
        lat_hi = lat_lo  + cell_lat
        count  = sum(1 for s in sampled if lat_lo <= safe_lat(s) < lat_hi)
        print(f"  {lat_hi:.2f}-{lat_lo:.2f}N  {count:3d}  {'#' * count}")

    print(f"\nDone. {len(sampled)} schools -> {output_file}")
    return sampled


if __name__ == "__main__":
    run()
