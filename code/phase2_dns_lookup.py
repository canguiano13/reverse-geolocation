"""
Phase 2 — Reverse DNS Lookup
------------------------------
For every IP block found in phase 1, expands it into individual IPs
and checks what hostname is registered to each one (PTR record).
Keeps only IPs whose hostname contains the school's name or a K-12 keyword.
Uses 50 parallel threads so DNS lookups run simultaneously instead of one-by-one.
"""

import csv
import ipaddress
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import dns.resolver
import dns.reversename

INPUT_FILE  = "data/phase1_candidates.csv"
OUTPUT_FILE = "data/phase2_filtered.csv"

TIMEOUT   = 1.0   # seconds before a DNS lookup is considered failed
WORKERS   = 50    # number of parallel DNS lookups
MAX_CIDRS = 2000  # skip schools with more blocks than this (usually dense urban schools)

# Words to ignore when extracting keywords from a school name.
# e.g. "Highview Elementary School" → keywords: ["highview"]
STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
}

# Words in a hostname that suggest it belongs to a school,
# even if the school's name isn't in it.
# e.g. "barracuda.msd.k12.ny.us" → partial match via "k12"
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "sch", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
    "csd", "ufsd", "boces",
}


def get_keywords(school_name):
    """Extract meaningful words from a school name (lowercased, stop words removed)."""
    cleaned = re.sub(r"[^a-z0-9]", " ", school_name.lower())
    return [word for word in cleaned.split() if word not in STOP_WORDS and len(word) >= 3]


def reverse_dns(ip):
    """Look up the hostname for an IP address. Returns None if no record exists."""
    try:
        rev     = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev, "PTR", lifetime=TIMEOUT)
        return str(answers[0]).rstrip(".")
    except Exception:
        return None


def has_k12_indicator(hostname):
    """Check if a hostname contains any K-12 related word."""
    for kw in K12_INDICATORS:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname):
            return True
    return False


def classify(hostname, keywords):
    """
    Decide how well a hostname matches a school.
    Returns: "match", "partial_match", "no_match", or "no_record"
    """
    if hostname is None:
        return "no_record"

    h = hostname.lower()

    # Reject hostnames from another state's k12 domain right away.
    # e.g. berkeley.k12.sc.us is South Carolina — can't be a NY school.
    state_match = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state_match and state_match.group(1) != 'ny':
        return "no_match"

    # Require at least 2 school keywords to match.
    # Prevents single-word false positives like "new" alone matching
    # "New Hyde Park" or "howard" alone matching a printing company.
    words_found = [kw for kw in keywords if kw in h]
    if len(words_found) >= 2:
        return "match"

    # Fallback: even without the school name, k12 words suggest a school.
    if has_k12_indicator(h):
        return "partial_match"

    return "no_match"


def check_ip(ip, school_name, keywords):
    """Run reverse DNS on one IP and classify the result. Runs in a thread."""
    hostname   = reverse_dns(ip)
    match_type = classify(hostname, keywords)
    return {
        "ip_address":    ip,
        "school_name":   school_name,
        "district_name": "",
        "hostname":      hostname or "",
        "match_type":    match_type,
    }


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):

    # Step 1: Load IP blocks from phase 1
    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Step 2: Group blocks by school, skip schools with too many blocks
    blocks_per_school = defaultdict(list)
    block_count       = defaultdict(int)
    for row in rows:
        block_count[row["school_name"].strip()] += 1
    for row in rows:
        school = row["school_name"].strip()
        if block_count[school] <= MAX_CIDRS:
            blocks_per_school[school].append(row["cidr"].strip())

    skipped = sum(1 for c in block_count.values() if c > MAX_CIDRS)
    if skipped:
        print(f"Skipping {skipped} schools with >{MAX_CIDRS} IP blocks")

    schools    = list(blocks_per_school.keys())
    total_ips  = 0
    tally      = defaultdict(int)

    print(f"Processing {len(schools)} schools")

    # Step 3: For each school, expand its IP blocks and run DNS lookups in parallel
    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(
            out_f,
            fieldnames=["ip_address", "school_name", "district_name", "hostname", "match_type"]
        )
        writer.writeheader()
        out_f.flush()

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for idx, school in enumerate(schools, 1):
                keywords = get_keywords(school)
                print(f"[{idx}/{len(schools)}] {school[:45]} ...", flush=True)

                # Expand each CIDR into individual IPs (IPv4 only)
                ips = []
                for cidr in blocks_per_school[school]:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        if isinstance(net, ipaddress.IPv4Network):
                            ips.extend(str(ip) for ip in net.hosts())
                    except ValueError:
                        continue

                print(f"  {len(ips)} IPs to check", flush=True)

                # Submit all IPs to the thread pool at once, collect results as they finish
                futures = {pool.submit(check_ip, ip, school, keywords): ip for ip in ips}
                done = 0
                for future in as_completed(futures):
                    result = future.result()
                    tally[result["match_type"]] += 1
                    total_ips += 1
                    done += 1

                    if result["match_type"] in ("match", "partial_match"):
                        writer.writerow(result)
                        out_f.flush()
                        print(f"  {result['ip_address']}  [{result['match_type']}]  {result['hostname']}", flush=True)

                    if done % 10000 == 0:
                        print(f"  ... {done}/{len(ips)} done", flush=True)

                print(f"  finished school. total IPs checked: {total_ips}", flush=True)

    # Step 4: Print summary
    print(f"\nDone. Results written to {output_file}")
    print(f"match: {tally['match']}  partial: {tally['partial_match']}  "
          f"no_match: {tally['no_match']}  no_record: {tally['no_record']}")


if __name__ == "__main__":
    run()
