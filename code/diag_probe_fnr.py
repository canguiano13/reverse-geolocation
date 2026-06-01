"""
Diagnostic: estimate the false negative rate of Phase 2's probe-first filter.

Phase 2 checks only N_PROBE_IPS (currently 5) IPs per /24 block before
deciding whether to fully expand it. Blocks where none of those 5 IPs have
school-looking PTR records are silently dropped — even if the block contains
valid .k12.ny.us records deeper in.

This script measures how often that happens:
  1. Find all /24 blocks that Phase 1 found but Phase 2 dropped entirely
     (no IP from that block appears in phase2_filtered).
  2. Sample N_SAMPLE of those dropped blocks randomly.
  3. For each, check N_CHECK_IPS random IPs for PTR records.
  4. Report how many had .k12.ny.us records → those are false negatives.

The result is a lower-bound estimate: we still check only N_CHECK_IPS out of
254, so we may undercount. But if the FNR is near zero, the probe design is
justified; if it's meaningful (>1-2%), that's a real recall limitation to
quantify in the paper.

Inputs:
  data/outputs/phase_candidates_10km.csv   (Phase 0 + Phase 1 merged)
  data/outputs/phase2_filtered_10km.csv    (Phase 2 survivors)

Output:
  data/outputs/diag_probe_fnr.csv          (sampled dropped blocks + verdict)
"""

import csv
import ipaddress
import random
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

CANDIDATES_FILE = "data/outputs/phase_candidates_10km.csv"
FILTERED_FILE   = "data/outputs/phase2_filtered_10km.csv"
OUTPUT_FILE     = "data/outputs/diag_probe_fnr.csv"

N_SAMPLE    = 200    # dropped blocks to audit
N_CHECK_IPS = 40     # IPs per block to check (random, not first-N)
TIMEOUT     = 1.5
WORKERS     = 60
SEED        = 42


def reverse_dns(ip):
    try:
        hostname = socket.gethostbyaddr(ip)[0]
        return hostname.lower()
    except Exception:
        return None


def is_ny_k12(hostname):
    return hostname is not None and bool(re.search(r'\.k12\.ny\.us', hostname))


def has_k12_indicator(hostname):
    if hostname is None:
        return False
    indicators = {"k12", "school", "district", "elementary", "middle",
                  "schl", "csd", "ufsd", "boces", "isd", "academy"}
    return any(re.search(r'(?<![a-z])' + kw + r'(?![a-z])', hostname)
               for kw in indicators)


def check_block(cidr, school_name):
    """
    Check N_CHECK_IPS random IPs in the block.
    Returns dict with verdict and any matching hostnames found.
    """
    try:
        net   = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
    except ValueError:
        return {"cidr": cidr, "school_name": school_name,
                "verdict": "error", "ny_k12_found": "", "k12_found": "",
                "ips_checked": 0, "total_hosts": 0}

    sample = random.sample(hosts, min(N_CHECK_IPS, len(hosts)))

    ny_k12_hits  = []
    k12_hits     = []

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(sample))) as pool:
        futures = {pool.submit(reverse_dns, str(ip)): str(ip) for ip in sample}
        for future in as_completed(futures):
            ip       = futures[future]
            hostname = future.result()
            if is_ny_k12(hostname):
                ny_k12_hits.append(f"{ip} -> {hostname}")
            elif has_k12_indicator(hostname):
                k12_hits.append(f"{ip} -> {hostname}")

    if ny_k12_hits:
        verdict = "FALSE_NEGATIVE_k12ny"
    elif k12_hits:
        verdict = "FALSE_NEGATIVE_k12other"
    else:
        verdict = "CONFIRMED_NEGATIVE"

    return {
        "cidr":          cidr,
        "school_name":   school_name,
        "verdict":       verdict,
        "ny_k12_found":  " | ".join(ny_k12_hits),
        "k12_found":     " | ".join(k12_hits),
        "ips_checked":   len(sample),
        "total_hosts":   len(hosts),
    }


def run(candidates_file=CANDIDATES_FILE, filtered_file=FILTERED_FILE,
        output_file=OUTPUT_FILE, seed=SEED):

    random.seed(seed)

    # Load all candidate CIDRs (Phase 1 output), keyed by cidr -> school_name
    candidates = {}
    with open(candidates_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cidr   = row["cidr"].strip()
            school = row["school_name"].strip()
            if cidr not in candidates:
                candidates[cidr] = school
    print(f"Candidate blocks (Phase 1): {len(candidates)}")

    # Load CIDRs that Phase 2 actually kept (had at least one match)
    kept_cidrs = set()
    try:
        with open(filtered_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ip = row["ip_address"].strip()
                try:
                    net = ipaddress.ip_network(f"{ip}/24", strict=False)
                    kept_cidrs.add(str(net.network_address) + "/24")
                except ValueError:
                    pass
    except FileNotFoundError:
        print(f"Warning: {filtered_file} not found — run Phase 2 first")
        return
    print(f"Blocks kept by Phase 2: {len(kept_cidrs)}")

    # Dropped = in candidates but no IP ended up in phase2 output
    dropped = [(cidr, school)
               for cidr, school in candidates.items()
               if cidr not in kept_cidrs]
    print(f"Dropped blocks (Phase 2 filtered out): {len(dropped)}")

    if not dropped:
        print("No dropped blocks found — nothing to audit.")
        return

    sample = random.sample(dropped, min(N_SAMPLE, len(dropped)))
    print(f"\nAuditing {len(sample)} randomly sampled dropped blocks "
          f"({N_CHECK_IPS} random IPs each)...\n")

    results = []
    tally   = defaultdict(int)

    for i, (cidr, school) in enumerate(sample, 1):
        print(f"[{i}/{len(sample)}] {cidr:<20}  {school[:45]}", end=" ", flush=True)
        result = check_block(cidr, school)
        tally[result["verdict"]] += 1
        print(f"-> {result['verdict']}"
              + (f"  {result['ny_k12_found'] or result['k12_found']}"
                 if result["verdict"] != "CONFIRMED_NEGATIVE" else ""),
              flush=True)
        results.append(result)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["cidr", "school_name", "verdict",
                           "ny_k12_found", "k12_found",
                           "ips_checked", "total_hosts"]
        )
        writer.writeheader()
        writer.writerows(results)

    total   = len(results)
    fn_ny   = tally["FALSE_NEGATIVE_k12ny"]
    fn_k12  = tally["FALSE_NEGATIVE_k12other"]
    neg     = tally["CONFIRMED_NEGATIVE"]

    print(f"\n{'='*55}")
    print(f"  Sampled dropped blocks  : {total}")
    print(f"  CONFIRMED_NEGATIVE      : {neg}  ({neg/total:.1%})")
    print(f"  FALSE_NEGATIVE .k12.ny  : {fn_ny}  ({fn_ny/total:.1%})")
    print(f"  FALSE_NEGATIVE other k12: {fn_k12}  ({fn_k12/total:.1%})")
    print(f"  Total false negatives   : {fn_ny + fn_k12}  "
          f"({(fn_ny + fn_k12)/total:.1%})")
    print(f"{'='*55}")
    print(f"\nNote: FNR is a lower bound — only {N_CHECK_IPS}/{254} IPs "
          f"checked per block.")
    print(f"Done -> {output_file}")


if __name__ == "__main__":
    run()
