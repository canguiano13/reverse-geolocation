"""
fix_attribution.py — Re-attribute Phase 3 results to correct school districts.

The CIDR-only dedup in Phase 1 assigns each IP block to the first school
that geographically claims it, which is often wrong. But the PTR record
hostname (e.g. tech.scarsdaleschools.k12.ny.us) tells us the real owner.

This script:
  1. Extracts the district code from each *.k12.ny.us hostname
  2. Matches it to the best school name in our 12k school list
  3. Writes a corrected CSV with both original and attributed school names

Input:  data/outputs/phase3_confirmed_10km.csv
Output: data/outputs/phase3_reattributed_10km.csv
"""

import csv
import re
import os
from collections import defaultdict

INPUT_FILE   = "data/outputs/phase3_confirmed_10km.csv"
SCHOOLS_FILE = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE  = "data/outputs/phase3_reattributed_10km.csv"

# Manual mappings for all known district codes → search term for school list.
# We know all 8 codes from the data, so map them all explicitly.
# Value = substring to search for in school names (case-insensitive).
MANUAL_MAPPINGS = {
    "mw":               "Monroe-Woodbury",
    "cpcs":             "Chateaugay",
    "scarsdaleschools": "Scarsdale",
    "bayshore":         "Bay Shore",
    "syosset":          "Syosset",
    "wallkill":         "Wallkill",
    "liberty":          "Liberty",
    "hackley":          "Hackley",
}


def extract_district_code(hostname):
    """
    Pull the district identifier from a *.k12.ny.us hostname.
    e.g. tech.scarsdaleschools.k12.ny.us → 'scarsdaleschools'
         mwcsd-209-222-32-25.mw.k12.ny.us → 'mw'
         bayshore.k12.ny.us → 'bayshore'
    """
    m = re.search(r'([^.]+)\.k12\.ny\.us', hostname.lower())
    return m.group(1) if m else None


def find_best_match(code, school_names):
    """
    Find the best matching school name for a district code.
    Uses the manual mapping search term split into individual words,
    each of which must appear in the school name.
    Returns the first school whose name contains ALL search tokens.
    Falls back to partial match if no school matches all tokens.
    """
    search_term = MANUAL_MAPPINGS.get(code, code)

    # Split search term into words (handles "Monroe-Woodbury", "Bay Shore", etc.)
    tokens = [t.lower() for t in re.split(r'[\s\-]+', search_term) if len(t) >= 3]

    # First pass: find schools containing ALL tokens
    for name in school_names:
        name_lower = name.lower()
        if all(t in name_lower for t in tokens):
            return name

    # Second pass: find schools containing ANY token (best partial match)
    best_name  = None
    best_score = 0
    for name in school_names:
        name_lower = name.lower()
        score = sum(1 for t in tokens if t in name_lower)
        if score > best_score:
            best_score = score
            best_name  = name

    return best_name if best_score > 0 else None


def run(input_file=INPUT_FILE, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    # Load school names
    with open(schools_file, newline="", encoding="utf-8") as f:
        school_names = [r["school_name"].strip() for r in csv.DictReader(f)
                        if r["school_name"].strip().lower() != "name unknown"]
    print(f"Loaded {len(school_names)} schools for matching")

    # Build a cache: district_code → best matching school name
    code_cache = {}

    # Read phase 3 results
    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys()) if rows else []

    print(f"Processing {len(rows)} Phase 3 IPs...")

    stats = defaultdict(int)
    for row in rows:
        hostname = row.get("hostname", "").lower()
        code     = extract_district_code(hostname)

        if code:
            # Look up or compute the best school match for this district code
            if code not in code_cache:
                matched = find_best_match(code, school_names)
                code_cache[code] = matched
                if matched:
                    print(f"  '{code}' → '{matched}'")
                else:
                    print(f"  '{code}' → no match found, keeping original")

            matched_name = code_cache[code]
            row["geo_school"]        = row["school_name"]   # preserve original
            row["school_name"]       = matched_name or row["school_name"]
            row["district_code"]     = code
            stats["k12_domain"] += 1
        else:
            # No k12.ny.us hostname — keep original Phase 1 attribution
            row["geo_school"]    = row["school_name"]
            row["district_code"] = ""
            stats["no_domain"] += 1

    # Write output
    out_fieldnames = ["ip_address", "school_name", "geo_school", "district_code",
                      "hostname", "phase2_match", "asn", "whois_org",
                      "is_hosting", "ny_k12_domain", "whois_match", "fcc_match",
                      "score", "confidence"]

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    print(f"\n=== Attribution Summary ===")
    by_district = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0})
    for row in rows:
        by_district[row["school_name"]][row["confidence"]] += 1

    for district, counts in sorted(by_district.items(),
                                   key=lambda x: -(x[1]["high"]+x[1]["medium"]+x[1]["low"])):
        total = counts["high"] + counts["medium"] + counts["low"]
        print(f"  {total:>5} IPs  H={counts['high']} M={counts['medium']} L={counts['low']}  {district}")

    print(f"\nDone. Written to {output_file}")
    print(f"Re-attributed: {stats['k12_domain']}  kept original: {stats['no_domain']}")


if __name__ == "__main__":
    run()
