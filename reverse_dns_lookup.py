import csv
import re
import socket
import sys
import time
from collections import defaultdict

INPUT_FILE  = "phase1_candidates.csv"
OUTPUT_FILE = "phase2_filtered.csv"
DELAY       = 0.1  # seconds between lookups
TIMEOUT     = 3.0  # seconds per DNS lookup

# stripped from school/district names before keyword matching
STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
}


# hostname must contain one of these to count as match or partial_match
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "sch", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
}


# strips stop words and returns meaningful tokens from a school/district name
def extract_keywords(name):
    cleaned = re.sub(r"[^a-z0-9]", " ", name.lower())
    return [t for t in cleaned.split() if t not in STOP_WORDS and len(t) >= 3]


# returns the PTR hostname for an IP, or None if no record or timeout
def reverse_dns(ip):
    socket.setdefaulttimeout(TIMEOUT)
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname.lower()
    except:
        return None


# returns True if the hostname contains a K-12 sector keyword
def has_k12_indicator(hostname):
    for kw in K12_INDICATORS:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname):
            return True
    return False


# classifies a hostname as match, partial_match, no_match, or no_record
def classify(hostname, school_kws, district_kws):
    if hostname is None:
        return "no_record"
    name_found = any(kw in hostname for kw in school_kws + district_kws)
    if name_found and has_k12_indicator(hostname):
        return "match"
    if has_k12_indicator(hostname):
        return "partial_match"
    return "no_match"


if __name__ == "__main__":
    # load input
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    print(f"Loaded {total} IPs from {INPUT_FILE}")

    counts = defaultdict(int)
    results = []

    for i, row in enumerate(rows, 1):
        ip       = row["ip_address"].strip()
        school   = row["school_name"].strip()
        district = row["district_name"].strip()

        hostname   = reverse_dns(ip)
        match_type = classify(hostname, 
                              extract_keywords(school), 
                              extract_keywords(district))

        counts[match_type] += 1
        results.append({
            "ip_address":    ip,
            "school_name":   school,
            "district_name": district,
            "hostname":      hostname or "",
            "match_type":    match_type,
        })

        if match_type in ("match", "partial_match"):
            print(f"{i}/{total}  {ip}  [{match_type}]  {hostname or '(no record)'}")
        elif i % 250 == 0 or i == total:
            print(f"Progress: {i}/{total}")

        time.sleep(DELAY)

    # write output
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ip_address", "school_name", "district_name", "hostname", "match_type"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"match: {counts['match']}  partial: {counts['partial_match']}  no_match: {counts['no_match']}  no_record: {counts['no_record']}")
