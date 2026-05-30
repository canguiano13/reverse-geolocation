"""
Phase 2 — Reverse DNS Lookup

For every IP block from phase 1, check what hostname each IP resolves to.
Keep IPs whose hostname contains the school's name or a K-12 keyword.

Probe-first: check 5 IPs per block before expanding all 254. If none of
those 5 look like a school, skip the whole block — cuts DNS calls by ~100x.

Checkpointing: saves progress after each school so a crashed run can resume.
"""

import csv
import ipaddress
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import dns.resolver
import dns.reversename

INPUT_FILE  = "data/outputs/phase1_candidates.csv"
OUTPUT_FILE = "data/outputs/phase2_filtered.csv"

TIMEOUT      = 1.0   # seconds per DNS lookup
WORKERS      = 50    # parallel threads
MAX_CIDRS    = 5000  # skip schools with more blocks than this
N_PROBE_IPS  = 5     # IPs checked per block in probe phase

# Generic words stripped from school names before keyword matching
STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
    "long", "island", "new", "york",
}

# Words in a hostname that indicate a school even without the school's name
# Note: "sch" excluded — it matched a Greek education domain (.att.sch.gr)
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
    "csd", "ufsd", "boces",
}


def get_keywords(school_name):
    """Extract meaningful words from a school name (stop words removed)."""
    cleaned = re.sub(r"[^a-z0-9]", " ", school_name.lower())
    return [w for w in cleaned.split() if w not in STOP_WORDS and len(w) >= 3]


def reverse_dns(ip):
    """Look up the PTR hostname for an IP. Returns None if no record exists."""
    try:
        rev     = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev, "PTR", lifetime=TIMEOUT)
        return str(answers[0]).rstrip(".")
    except Exception:
        return None


def has_k12_indicator(hostname):
    """Return True if the hostname contains any K-12 keyword."""
    for kw in K12_INDICATORS:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname):
            return True
    return False


def classify(hostname, keywords):
    """Return match quality: 'match', 'partial_match', 'no_match', or 'no_record'."""
    if hostname is None:
        return "no_record"
    h = hostname.lower()

    # Reject another state's k12 domain immediately
    state_match = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state_match and state_match.group(1) != 'ny':
        return "no_match"

    # Need at least 2 school name keywords to match (prevents single-word false positives)
    if sum(1 for kw in keywords if kw in h) >= 2:
        return "match"

    if has_k12_indicator(h):
        return "partial_match"

    return "no_match"


def probe_block(cidr, keywords):
    """Check the first N_PROBE_IPS IPs of a block. Returns True if any look like a school."""
    try:
        net   = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
        if not hosts or not isinstance(net, ipaddress.IPv4Network):
            return False
        probe_ips = [str(h) for h in hosts[:N_PROBE_IPS]]
        with ThreadPoolExecutor(max_workers=len(probe_ips)) as pp:
            futures = {pp.submit(reverse_dns, ip): ip for ip in probe_ips}
            for future in as_completed(futures):
                hostname = future.result()
                if hostname and classify(hostname, keywords) in ("match", "partial_match"):
                    return True
        return False
    except Exception:
        return False


def check_ip(ip, school_name, keywords):
    """Reverse DNS one IP and classify the result. Called in a thread."""
    hostname   = reverse_dns(ip)
    match_type = classify(hostname, keywords)
    return {
        "ip_address":    ip,
        "school_name":   school_name,
        "district_name": "",
        "hostname":      hostname or "",
        "match_type":    match_type,
    }


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, test_cap=None, force_fresh=False):

    checkpoint_file = output_file.replace(".csv", "_checkpoint.txt")

    # Load checkpoint — which schools were already done in a previous run
    completed = set()
    if not force_fresh and os.path.exists(checkpoint_file) and os.path.exists(output_file):
        with open(checkpoint_file) as f:
            completed = {line.strip() for line in f if line.strip()}
        print(f"Resuming: {len(completed)} schools already done")
    elif os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Group blocks by school; skip schools with too many blocks
    block_count = defaultdict(int)
    for row in rows:
        block_count[row["school_name"].strip()] += 1

    blocks_per_school = defaultdict(list)
    for row in rows:
        school = row["school_name"].strip()
        if block_count[school] <= MAX_CIDRS:
            blocks_per_school[school].append(row["cidr"].strip())

    skipped = sum(1 for c in block_count.values() if c > MAX_CIDRS)
    if skipped:
        print(f"Skipping {skipped} schools with >{MAX_CIDRS} blocks")

    schools = list(blocks_per_school.keys())
    if test_cap:
        schools = schools[:test_cap]
        print(f"TEST MODE: {test_cap} schools (full run has {len(blocks_per_school)})")

    remaining = [s for s in schools if s not in completed]
    print(f"Processing {len(remaining)} schools ({len(completed)} already done)")

    total_ips = 0
    tally     = defaultdict(int)

    file_mode = "a" if completed else "w"
    with open(output_file, file_mode, newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(
            out_f,
            fieldnames=["ip_address", "school_name", "district_name", "hostname", "match_type"]
        )
        if not completed:
            writer.writeheader()
        out_f.flush()

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for idx, school in enumerate(remaining, 1):
                keywords = get_keywords(school)
                print(f"[{idx}/{len(remaining)}] {school[:45]} ...", flush=True)

                # Probe-first: skip blocks where no sample IP looks like a school
                all_cidrs = blocks_per_school[school]
                probe_futures = {pool.submit(probe_block, cidr, keywords): cidr for cidr in all_cidrs}
                promising_cidrs = [probe_futures[f] for f in as_completed(probe_futures) if f.result()]
                print(f"  {len(promising_cidrs)}/{len(all_cidrs)} blocks passed probe", flush=True)

                # Expand only the promising blocks into individual IPs
                ips = []
                for cidr in promising_cidrs:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        if isinstance(net, ipaddress.IPv4Network):
                            ips.extend(str(ip) for ip in net.hosts())
                    except ValueError:
                        continue
                print(f"  {len(ips)} IPs to check", flush=True)

                # Check all IPs in parallel
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

                print(f"  done. total IPs checked: {total_ips}", flush=True)
                with open(checkpoint_file, "a") as ckpt:
                    ckpt.write(school + "\n")

    print(f"\nDone {output_file}")
    print(f"match: {tally['match']}  partial: {tally['partial_match']}  "
          f"no_match: {tally['no_match']}  no_record: {tally['no_record']}")


if __name__ == "__main__":
    run()
