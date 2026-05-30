"""
Phase 2 — Reverse DNS Lookup
------------------------------
For every IP block found in phase 1, expands it into individual IPs
and checks what hostname is registered to each one (PTR record).
Keeps only IPs whose hostname contains the school's name or a K-12 keyword.
Uses 50 parallel threads so DNS lookups run simultaneously instead of one-by-one.

Probe-first optimization: before expanding a /24 block to 254 IPs, we check
just the first IP. If that probe returns no k12 hostname, we skip the entire
block. This reduces DNS lookups by ~100x since most blocks are not school networks.

Checkpointing: progress is saved after each school. If the run is interrupted,
restart and it picks up where it left off instead of starting over.

Test mode: set test_cap=500 in main.py to only process the first 500 schools
and estimate how long the full run will take.
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

TIMEOUT      = 1.0   # seconds before a DNS lookup is considered failed
WORKERS      = 50    # number of parallel DNS lookups
MAX_CIDRS    = 5000  # skip schools with more blocks than this (usually dense urban schools)
                     # 5000 is enough for Bay Shore (2520), Syosset (3152), Malverne (3122)
                     # Probe-first keeps runtime manageable: 5000 probes ≈ 100s at 50 workers
N_PROBE_IPS  = 5     # IPs to check per block in probe phase (checked in parallel, ~1s total)
                     # Catches PTR records that aren't on .1 — e.g. brentwood.k12.ny.us is on
                     # .11.2, not .11.1, so a 1-IP probe would miss it entirely

# Words to ignore when extracting keywords from a school name.
# e.g. "Highview Elementary School" → keywords: ["highview"]
STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
    # Geographic terms too common on Long Island / NYC to use as school identifiers
    # e.g. "The Children's Center at UCP of Long Island" → keywords would include
    # "long" and "island", which match longislandfiberexchange.net (an ISP)
    "long", "island", "new", "york",
}

# Words in a hostname that suggest it belongs to a school,
# even if the school's name isn't in it.
# e.g. "barracuda.msd.k12.ny.us" → partial match via "k12"
#
# NOTE: "sch" intentionally removed — it matched the Greek education domain
# (.att.sch.gr) because .sch. passes the word-boundary regex. All legitimate
# NY school PTR records use longer identifiers (k12, csd, ufsd, boces, etc.).
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "schl",
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


def probe_block(cidr, keywords):
    """
    Check the first N_PROBE_IPS usable IPs of a CIDR block in parallel.
    Returns True if ANY of them looks like a school network.

    Why N_PROBE_IPS > 1: some districts' PTR records don't start at .1.
    e.g. brentwood.k12.ny.us is on .11.2 — a 1-IP probe on .11.1 would
    return None and skip the entire /24, missing Brentwood entirely.
    Parallel lookup keeps total probe time ~1s regardless of N_PROBE_IPS.
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        if not isinstance(net, ipaddress.IPv4Network):
            return False
        hosts = list(net.hosts())
        if not hosts:
            return False

        probe_ips = [str(h) for h in hosts[:N_PROBE_IPS]]

        # Run all probes in parallel so latency stays ~1s not N*1s
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


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, test_cap=None, force_fresh=False):

    # Checkpoint file sits next to the output file — tracks which schools are done
    checkpoint_file = output_file.replace(".csv", "_checkpoint.txt")

    # Load already-completed schools so we can skip them on resume
    completed = set()
    if not force_fresh and os.path.exists(checkpoint_file) and os.path.exists(output_file):
        with open(checkpoint_file) as f:
            completed = set(line.strip() for line in f if line.strip())
        print(f"Resuming from checkpoint: {len(completed)} schools already done")
    else:
        # Fresh start — clear any old checkpoint
        if os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)

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

    schools = list(blocks_per_school.keys())

    # Test cap — only process first N schools to estimate runtime
    if test_cap:
        schools = schools[:test_cap]
        print(f"TEST MODE: capped at {test_cap} schools (out of {len(blocks_per_school)})")
        print(f"Time this run and multiply up to estimate full runtime.")

    # Filter out schools already done in a previous (interrupted) run
    remaining = [s for s in schools if s not in completed]
    print(f"Processing {len(remaining)} schools  ({len(completed)} already done, {len(schools) - len(remaining) - len(completed)} skipped by cap)")

    total_ips = 0
    tally     = defaultdict(int)

    # Step 3: For each school, expand its IP blocks and run DNS lookups in parallel
    # Append to existing file if resuming, write fresh if starting over
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

                # Probe-first: check one IP per block before expanding.
                # Blocks whose first IP has no k12 hostname are skipped entirely —
                # saves ~100x DNS lookups vs expanding every block blindly.
                all_cidrs = blocks_per_school[school]
                probe_futures = {
                    pool.submit(probe_block, cidr, keywords): cidr
                    for cidr in all_cidrs
                }
                promising_cidrs = []
                for future in as_completed(probe_futures):
                    if future.result():
                        promising_cidrs.append(probe_futures[future])

                print(f"  {len(promising_cidrs)}/{len(all_cidrs)} blocks passed probe filter", flush=True)

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

                print(f"  finished school. total IPs checked so far: {total_ips}", flush=True)

                # Save progress — mark this school as done in the checkpoint file
                with open(checkpoint_file, "a") as ckpt:
                    ckpt.write(school + "\n")

    # Step 4: Print summary
    print(f"\nDone. Results written to {output_file}")
    print(f"match: {tally['match']}  partial: {tally['partial_match']}  "
          f"no_match: {tally['no_match']}  no_record: {tally['no_record']}")

    if test_cap and len(remaining) > 0:
        print(f"\nTEST MODE summary: processed {len(remaining)} schools, {total_ips} IPs checked.")
        print(f"Full run has {len(blocks_per_school)} schools — scale up time accordingly.")


if __name__ == "__main__":
    run()
