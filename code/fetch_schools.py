"""
fetch_schools.py — Grid-based geographic sampling of NY K-12 schools.

Divides NY state into a GRID_SIZE x GRID_SIZE cell grid and samples schools
proportionally from each cell. This produces a geographically diverse set of
~250 schools that evenly covers the state, avoiding the cluster bias of the
full 13k-school list (which skews heavily toward NYC metro).

Usage:
    python3 fetch_schools.py

Input:  data/inputs/gigamaps_schools_ny.csv   (all 13k NY schools)
Output: data/inputs/sampled_schools_grid.csv  (250 diverse schools)
"""

import csv
import random

# -- configuration -------------------------------------------------------------

INPUT_FILE  = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE = "data/inputs/sampled_schools_grid.csv"

# NY state bounding box (slightly tighter than phase1 uses)
MIN_LAT = 40.5
MAX_LAT = 45.05
MIN_LON = -79.8
MAX_LON = -73.0

GRID_SIZE   = 9      # 9x9 = 81 cells
N_SCHOOLS   = 250    # target sample size
RANDOM_SEED = 42     # for reproducibility


# -- grid helpers --------------------------------------------------------------

def get_grid_cell(lat, lon):
    """
    Map a (lat, lon) to a (row, col) cell index in the GRID_SIZE x GRID_SIZE grid.
    Returns None if the point is outside NY bounding box.
    """
    if not (MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lon <= MAX_LON):
        return None

    cell_lat = (MAX_LAT - MIN_LAT) / GRID_SIZE
    cell_lon = (MAX_LON - MIN_LON) / GRID_SIZE

    row = int((lat - MIN_LAT) / cell_lat)
    col = int((lon - MIN_LON) / cell_lon)

    # Clamp to valid range (handles edge values exactly on the max boundary)
    row = min(row, GRID_SIZE - 1)
    col = min(col, GRID_SIZE - 1)

    return (row, col)


def build_grid(schools):
    """
    Bucket all schools into their grid cells.
    Returns: dict mapping (row, col) -> list of school dicts.
    """
    grid = {}
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

    print(f"  Schools in grid: {sum(len(v) for v in grid.values())}")
    print(f"  Schools outside NY bbox: {outside}")
    print(f"  Non-empty cells: {len(grid)} / {GRID_SIZE * GRID_SIZE}")
    return grid


def sample_grid(grid, n_total, rng):
    """
    Sample schools from the grid, allocating slots proportionally to cell size
    but guaranteeing at least 1 school per non-empty cell (up to n_total).

    Strategy:
      1. Give 1 slot to every non-empty cell (geographic coverage guarantee)
      2. Distribute remaining slots proportionally to cell population
      3. Pick randomly within each cell
    """
    cells = sorted(grid.keys())  # deterministic ordering
    n_cells = len(cells)

    if n_cells == 0:
        return []

    # Step 1: at least 1 per cell
    base_alloc = {cell: 1 for cell in cells}
    remaining  = max(0, n_total - n_cells)

    # Step 2: proportional extra allocation
    total_schools = sum(len(grid[c]) for c in cells)
    for cell in cells:
        extra = round(remaining * len(grid[cell]) / total_schools)
        base_alloc[cell] += extra

    # Step 3: sample within each cell
    sampled = []
    for cell in cells:
        quota  = min(base_alloc[cell], len(grid[cell]))
        chosen = rng.sample(grid[cell], quota)
        sampled.extend(chosen)

    # Trim or top-up to hit n_total exactly
    if len(sampled) > n_total:
        sampled = rng.sample(sampled, n_total)
    elif len(sampled) < n_total:
        all_schools = [s for c in cells for s in grid[c]]
        already     = set(id(s) for s in sampled)
        extras      = [s for s in all_schools if id(s) not in already]
        need        = n_total - len(sampled)
        sampled.extend(rng.sample(extras, min(need, len(extras))))

    return sampled


def _safe_lat(school):
    try:
        return float(school.get("lat") or school.get("latitude") or 0)
    except (ValueError, TypeError):
        return 0


# -- main ----------------------------------------------------------------------

def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, n=N_SCHOOLS):
    print(f"Loading schools from {input_file} ...")

    with open(input_file, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        schools    = list(reader)
        fieldnames = reader.fieldnames

    print(f"  Total schools loaded: {len(schools)}")

    rng  = random.Random(RANDOM_SEED)
    grid = build_grid(schools)

    print(f"\nSampling {n} schools from {len(grid)} non-empty grid cells ...")
    sampled = sample_grid(grid, n, rng)

    # Sort north -> south
    sampled.sort(key=lambda s: -_safe_lat(s))

    print(f"\nWriting {len(sampled)} schools to {output_file} ...")
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sampled)

    # Print distribution by latitude band
    print("\nGeographic distribution (by latitude band):")
    cell_lat = (MAX_LAT - MIN_LAT) / GRID_SIZE
    for row_idx in range(GRID_SIZE - 1, -1, -1):   # north -> south
        lat_lo = MIN_LAT + row_idx * cell_lat
        lat_hi = lat_lo  + cell_lat
        count  = sum(1 for s in sampled if lat_lo <= _safe_lat(s) < lat_hi)
        bar    = "#" * count
        print(f"  {lat_hi:.2f}-{lat_lo:.2f}N  {count:3d}  {bar}")

    print(f"\nDone. {len(sampled)} schools saved to {output_file}")
    return sampled


if __name__ == "__main__":
    run()
