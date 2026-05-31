"""
Phase 2: reverse DNS lookup.

For every IP block from phase 1, check what hostname each IP resolves to.
Keep IPs whose hostname contains the school's name or a K-12 keyword.

Probe-first: check 5 IPs per block before expanding all 254. If none look
school-related, skip the whole block.

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

TIMEOUT      = 1.0
WORKERS      = 50
MAX_CIDRS    = 5000  # skip schools with more blocks than this
N_PROBE_IPS  = 5     # IPs sampled per block in probe phase

# Generic words removed before extracting keywords from a school name.
STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
    "long", "island", "new", "york",
}

# Words in a hostname that indicate a school even without the school's name.
# "sch" was excluded because it matched a Greek education domain (.att.sch.gr).
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
    "csd", "ufsd", "boces",
}

# Hostname fragments that indicate a cloud / CDN / hosting provider.
# Used to count how many rejected IPs were cloud-owned (for paper stats).
HOSTING_KEYWORDS = {
    "cloudflare", "amazonaws", "googleusercontent", "googleapis",
    "akamai", "fastly", "azure", "compute-1", "ec2",
    "linode", "digitalocean", "ovh", "hetzner", "vultr",
}


def get_keywords(school_name):
    cleaned = re.sub(r"[^a-z0-9]", " ", school_name.lower())
    return [w for w in cleaned.split() if w not in STOP_WORDS and len(w) >= 3]


def reverse_dns(ip):
    try:
        rev     = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev, "PTR", lifetime=TIMEOUT)
        return str(answers[0]).rstrip(".")
    except Exception:
        return None


def has_k12_indicator(hostname):
    for kw in K12_INDICATORS:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname):
            return True
    return False


def is_hosting_hostname(hostname):
    return any(kw in hostname for kw in HOSTING_KEYWORDS)


def classify(hostname, keywords):
    """
    Returns one of:
      match, partial_match, no_record,
      no_match_other_state, no_match_cloud, no_match_other

    Only 'match' and 'partial_match' are written to the output CSV.
    The no_match_* subcategories are counted in the tally for paper stats.
    """
    if hostname is None:
        return "no_record"
    h = hostname.lower()

    state_match = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state_match and state_match.group(1) != 'ny':
        return "no_match_other_state"

    # Need 2+ keywords; one common word like "new" is not enough
    if sum(1 for kw in keywords if kw in h) >= 2:
        return "match"

    if has_k12_indicator(h):
        return "partial_match"

    if is_hosting_hostname(h):
        return "no_match_cloud"

    return "no_match_other"


def probe_block(cidr, keywords):
    """Check first N_PROBE_IPS IPs in the block. True if any look school-related."""
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
    hostname   = reverse_dns(ip)
    match_type = classify(hostname, keywords)
    return {
        "ip_address":    ip,
        "school_name":   school_name,
        "district_name": "",
        "hostname":      hostname or "",
        "match_type":    match_type,
    }


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, force_fresh=False):

    checkpoint_file = output_file.replace(".csv", "_checkpoint.txt")

    completed = set()
    if not force_fresh and os.path.exists(checkpoint_file) and os.path.exists(output_file):
        with open(checkpoint_file) as f:
            completed = {line.strip() for line in f if line.strip()}
        print(f"Resuming: {len(completed)} schools already done")
    elif os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

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

    schools   = list(blocks_per_school.keys())
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

                all_cidrs = blocks_per_school[school]
                probe_futures = {pool.submit(probe_block, cidr, keywords): cidr for cidr in all_cidrs}
                promising_cidrs = [probe_futures[f] for f in as_completed(probe_futures) if f.result()]
                print(f"  {len(promising_cidrs)}/{len(all_cidrs)} blocks passed probe", flush=True)

                ips = []
                for cidr in promising_cidrs:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        if isinstance(net, ipaddress.IPv4Network):
                            ips.extend(str(ip) for ip in net.hosts())
                    except ValueError:
                        continue
                print(f"  {len(ips)} IPs to check", flush=True)

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

    print(f"\nDone -> {output_file}")
    print(f"match: {tally['match']}  partial: {tally['partial_match']}  "
          f"cloud: {tally['no_match_cloud']}  "
          f"other-state-k12: {tally['no_match_other_state']}  "
          f"no_match: {tally['no_match_other']}  "
          f"no_record: {tally['no_record']}")


if __name__ == "__main__":
    run()
