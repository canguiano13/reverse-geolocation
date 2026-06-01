"""
Phase 3b: re-attribute Phase 3 results to the correct district.

Phase 1 assigns each IP block to the first school that geographically claims it,
which is often wrong. The PTR hostname (e.g. tech.scarsdaleschools.k12.ny.us)
tells us the real owner. This script:
  1. Extracts the district code from each *.k12.ny.us hostname
  2. Matches it to the best school name in the gigamaps NY list
  3. Writes a corrected CSV with both original and attributed school names

Input:  data/outputs/phase3_confirmed_10km.csv
Output: data/outputs/phase3_reattributed_10km.csv
"""

import csv
import re
from collections import defaultdict

INPUT_FILE   = "data/outputs/phase3_confirmed_10km.csv"
SCHOOLS_FILE = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE  = "data/outputs/phase3_reattributed_10km.csv"

# District code (subdomain before .k12.ny.us) -> search term for school list.
# Compound codes can't be auto-split, so they're mapped explicitly.
MANUAL_MAPPINGS = {
    "mw":               "Monroe-Woodbury",
    "cpcs":             "Chateaugay",
    "scarsdaleschools": "Scarsdale",
    "bayshore":         "Bay Shore",
    "syosset":          "Syosset",
    "wallkill":         "Wallkill",
    "liberty":          "Liberty",
    "hackley":          "Hackley",
    "halfhollowhills":  "Half Hollow Hills High School",
    "hhh":              "Half Hollow Hills High School",
    "northshore":       "Sea Cliff School",                # unique anchor for North Shore CSD
    "westhempstead":    "West Hempstead High School",
    "pob":              "Plainview-Old Bethpage",
    "smithtown":        "Smithtown High School",
    "greatneck":        "Great Neck South Middle School",
    "lmcs":             "Livingston Manor Central School", # Sullivan County, via Ulster BOCES
}

# Non-standard cases. Anything not listed defaults to "public".
DISTRICT_FLAGS = {
    "hackley": "private",       # private prep school, Tarrytown NY
    "lmcs":    "out_of_metro",  # Sullivan County, geolocated here via shared BOCES infra
}

# When the search anchor is an obscure school, show a recognisable name in the output
DISPLAY_NAMES = {
    "Sea Cliff School": "North Shore High School",
}


def extract_district_code(hostname):
    """Pull the subdomain before .k12.ny.us, e.g. 'scarsdaleschools'."""
    m = re.search(r'([^.]+)\.k12\.ny\.us', hostname.lower())
    return m.group(1) if m else None


def find_best_match(code, school_names):
    """Best school name match for a district code. First all-token match, else best partial."""
    search_term = MANUAL_MAPPINGS.get(code, code)
    tokens = [t.lower() for t in re.split(r'[\s\-]+', search_term) if len(t) >= 3]

    for name in school_names:
        nl = name.lower()
        if all(t in nl for t in tokens):
            return name

    best_name  = None
    best_score = 0
    for name in school_names:
        nl    = name.lower()
        score = sum(1 for t in tokens if t in nl)
        if score > best_score:
            best_score = score
            best_name  = name

    return best_name if best_score > 0 else None


def run(input_file=INPUT_FILE, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    with open(schools_file, newline="", encoding="utf-8") as f:
        school_names = [r["school_name"].strip() for r in csv.DictReader(f)
                        if r["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(school_names)} schools for matching")

    code_cache = {}

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Processing {len(rows)} Phase 3 IPs...")

    stats = defaultdict(int)
    for row in rows:
        hostname = row.get("hostname", "").lower()
        code     = extract_district_code(hostname)

        if code:
            if code not in code_cache:
                matched = find_best_match(code, school_names)
                code_cache[code] = matched
                if matched:
                    print(f"  '{code}' -> '{matched}'")
                else:
                    print(f"  '{code}' -> no match found, keeping original")

            matched_name = code_cache[code]
            row["geo_school"]    = row["school_name"]
            attributed           = matched_name or row["school_name"]
            row["school_name"]   = DISPLAY_NAMES.get(attributed, attributed)
            row["district_code"] = code
            if matched_name is None:
                row["district_type"] = "unresolved"
                stats["unresolved"] += 1
            else:
                row["district_type"] = DISTRICT_FLAGS.get(code, "public")
            stats["k12_domain"] += 1
        else:
            row["geo_school"]    = row["school_name"]
            row["district_code"] = ""
            row["district_type"] = "public"
            stats["no_domain"] += 1

    out_fieldnames = ["ip_address", "school_name", "geo_school", "district_code",
                      "district_type", "hostname", "phase2_match", "asn", "whois_org",
                      "is_hosting", "ny_k12_domain", "whois_match", "fcc_match",
                      "score", "confidence"]

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n=== Attribution Summary ===")
    by_district = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0})
    for row in rows:
        by_district[row["school_name"]][row["confidence"]] += 1

    for district, counts in sorted(by_district.items(),
                                   key=lambda x: -(x[1]["high"]+x[1]["medium"]+x[1]["low"])):
        total = counts["high"] + counts["medium"] + counts["low"]
        print(f"  {total:>5} IPs  H={counts['high']} M={counts['medium']} L={counts['low']}  {district}")

    print(f"\nDone. Written to {output_file}")
    print(f"Re-attributed: {stats['k12_domain']}  kept original: {stats['no_domain']}  "
          f"unresolved: {stats['unresolved']}")
    if stats["unresolved"]:
        unresolved_codes = [code for code, name in code_cache.items() if name is None]
        print(f"Unresolved district codes (add to MANUAL_MAPPINGS): {unresolved_codes}")


if __name__ == "__main__":
    run()
