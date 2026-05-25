import csv
import math
import random
from collections import Counter


#splitting NY into 9x9 grid
GRID_SIZE = 9

MIN_LAT = 40.5
MAX_LAT = 45.05
MIN_LON = -79.8
MAX_LON = -73.0

def get_grid_cell(lat, lon):
    lat = float(lat)
    lon = float(lon)

    lat_ratio = (lat - MIN_LAT) / (MAX_LAT - MIN_LAT)
    lon_ratio = (lon - MIN_LON) / (MAX_LON - MIN_LON)

    # clamp
    lat_ratio = max(0, min(0.9999, lat_ratio))
    lon_ratio = max(0, min(0.9999, lon_ratio))

    i = int(lat_ratio * GRID_SIZE)
    j = int(lon_ratio * GRID_SIZE)

    return (i, j) 

def sample(schools, n=250):
    random.shuffle(schools)

    grid = {}

    # bucket schools into grid cells
    for school in schools:
        lat = school['latitude']
        lon = school['longitude']

        # filter outside NY bounding box
        if not (MIN_LAT <= float(lat) <= MAX_LAT and MIN_LON <= float(lon) <= MAX_LON):
            continue

        cell = get_grid_cell(lat, lon)

        if cell not in grid:
            grid[cell] = []

        grid[cell].append(school)

    # how many per cell
    num_cells = GRID_SIZE * GRID_SIZE
    per_cell = max(1, n // num_cells)

    sampled = []

    # sample from each grid cell
    for cell, bucket in grid.items():
        random.shuffle(bucket)
        sampled.extend(bucket[:per_cell])

    # if we undershot target, top up randomly
    if len(sampled) < n:
        remaining = [s for s in schools if s not in sampled]
        random.shuffle(remaining)
        sampled.extend(remaining[:n - len(sampled)])

    # trim excess
    sampled = sampled[:n]

    # sort for readability
    sampled = sorted(sampled, key=lambda s: (float(s['latitude']), float(s['longitude'])))

    print(f"Total sampled: {len(sampled)}")
    print(f"Grid cells used: {len(grid)}")

    return sampled


#we wont sample from schools whose names are "Name unknown" in the CSV:
def skip_unknown(schools):
    schools_with_known_name = []

    for school in schools:
        #skip school if name is unknown
        if school['school_name'].lower() == "name unknown":
            continue

        schools_with_known_name.append(school)
    return schools_with_known_name

#Try to infer the school level based on name
def infer_school_levels(schools):
    known = []
    for school in schools:
        if school['education_level'] != "Unknown":
            known.append(school)
            continue
            
        school_name = school['school_name'].lower()

        if "elementary" in school_name:
            school['education_level'] = "Primary"
            known.append(school)
        elif 'middle' in school_name or 'high school' in school_name:
            school['education_level'] = "Secondary"
            known.append(school)

    return known

def import_schools(filename):
    #read in schools from csv
    schools = []
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        schools = list(reader)
    return schools

def export_schools(schools, filename):
    if not schools:
        print("no schools sampled", file=sys.stderr)
        exit(1)

    fieldnames = schools[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        writer.writeheader()
        writer.writerows(schools)

    print(f"wrote {len(schools)} entries.")


def main():
    filename = "gigamaps_schools_ny.csv"
    schools = import_schools(filename)

    #skip schools with unknown names
    schools = skip_unknown(schools)

    #infer schools level from school names
    schools = infer_school_levels(schools)

    #get a random (and hopefully diverse) sample of schools
    sampled_schools = sample(schools)

    export_filename="../sampled_schools_grid.csv"
    export_schools(sampled_schools, export_filename)

if __name__ == "__main__":
    main()
