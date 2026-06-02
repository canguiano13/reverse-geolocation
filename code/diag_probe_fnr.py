"""Audit Phase 2's probe-first filter for false negatives.

For a random sample of blocks Phase 2 dropped, check N_CHECK_IPS random
IPs for .k12.ny.us or other K-12 PTR records that the 5-probe heuristic
might have missed.
"""

import csv
import ipaddress
import random
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import dns.resolver
import dns.reversename

CANDIDATES_FILE = "data/outputs/phase_candidates_10km.csv"
FILTERED_FILE = "data/outputs/phase2_filtered_10km.csv"
OUTPUT_FILE = "data/outputs/diag_probe_fnr.csv"

N_SAMPLE = 200
N_CHECK_IPS = 40
TIMEOUT = 1.5
WORKERS = 60
SEED = 42

K12_INDICATORS = {"k12", "school", "district", "elementary", "middle",
                  "schl", "csd", "ufsd", "boces", "isd", "academy"}

socket.setdefaulttimeout(TIMEOUT)

# macOS mDNSResponder throttles under sustained PTR load; query public
# resolvers directly.
_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
_resolver.timeout = 1.5
_resolver.lifetime = 3.0


def reverse_dns(ip):
    try:
        rev = dns.reversename.from_address(ip)
        return str(_resolver.resolve(rev, "PTR")[0]).rstrip(".").lower()
    except Exception:
        return None


def has_k12_indicator(hostname):
    return any(re.search(r'(?<![a-z])' + kw + r'(?![a-z])', hostname)
               for kw in K12_INDICATORS)


def check_block(cidr, school_name):
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return {"cidr": cidr, "school_name": school_name, "verdict": "error",
                "ny_k12_found": "", "k12_found": "", "ips_checked": 0, "total_hosts": 0}

    # IPv6 prefixes can be enormous; pipeline is IPv4-only anyway.
    if not isinstance(net, ipaddress.IPv4Network):
        return {"cidr": cidr, "school_name": school_name, "verdict": "skipped_ipv6",
                "ny_k12_found": "", "k12_found": "", "ips_checked": 0, "total_hosts": 0}

    hosts = list(net.hosts())
    sample = random.sample(hosts, min(N_CHECK_IPS, len(hosts)))

    ny_k12 = []
    other_k12 = []
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(sample))) as pool:
        futures = {pool.submit(reverse_dns, str(ip)): str(ip) for ip in sample}
        for f in as_completed(futures):
            ip = futures[f]
            host = f.result()
            if not host:
                continue
            if ".k12.ny.us" in host:
                ny_k12.append(f"{ip} -> {host}")
            elif has_k12_indicator(host):
                other_k12.append(f"{ip} -> {host}")

    if ny_k12:
        verdict = "FALSE_NEGATIVE_k12ny"
    elif other_k12:
        verdict = "FALSE_NEGATIVE_k12other"
    else:
        verdict = "CONFIRMED_NEGATIVE"

    return {
        "cidr": cidr,
        "school_name": school_name,
        "verdict": verdict,
        "ny_k12_found": " | ".join(ny_k12),
        "k12_found": " | ".join(other_k12),
        "ips_checked": len(sample),
        "total_hosts": len(hosts),
    }


def run(candidates_file=CANDIDATES_FILE, filtered_file=FILTERED_FILE,
        output_file=OUTPUT_FILE, seed=SEED):
    random.seed(seed)

    candidates = {}
    with open(candidates_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c = row["cidr"].strip()
            if c not in candidates:
                candidates[c] = row["school_name"].strip()
    print(f"Candidate blocks (Phase 1): {len(candidates)}")

    kept = set()
    try:
        with open(filtered_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    net = ipaddress.ip_network(f"{row['ip_address'].strip()}/24", strict=False)
                    kept.add(str(net.network_address) + "/24")
                except ValueError:
                    pass
    except FileNotFoundError:
        print(f"Warning: {filtered_file} not found — run Phase 2 first")
        return
    print(f"Blocks kept by Phase 2: {len(kept)}")

    dropped = []
    n_ipv6 = 0
    for cidr, school in candidates.items():
        if cidr in kept:
            continue
        try:
            if isinstance(ipaddress.ip_network(cidr, strict=False), ipaddress.IPv4Network):
                dropped.append((cidr, school))
            else:
                n_ipv6 += 1
        except ValueError:
            pass
    print(f"Dropped blocks (Phase 2 filtered out): {len(dropped) + n_ipv6}  "
          f"(IPv4: {len(dropped)}, IPv6 skipped: {n_ipv6})")

    if not dropped:
        print("No dropped blocks found — nothing to audit.")
        return

    sample = random.sample(dropped, min(N_SAMPLE, len(dropped)))
    print(f"\nAuditing {len(sample)} randomly sampled dropped blocks "
          f"({N_CHECK_IPS} random IPs each)...\n")

    results = []
    tally = defaultdict(int)
    for i, (cidr, school) in enumerate(sample, 1):
        print(f"[{i}/{len(sample)}] {cidr:<20}  {school[:45]}", end=" ", flush=True)
        r = check_block(cidr, school)
        tally[r["verdict"]] += 1
        extra = ""
        if r["verdict"] != "CONFIRMED_NEGATIVE":
            extra = f"  {r['ny_k12_found'] or r['k12_found']}"
        print(f"-> {r['verdict']}{extra}", flush=True)
        results.append(r)

    fields = ["cidr", "school_name", "verdict", "ny_k12_found", "k12_found",
              "ips_checked", "total_hosts"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    total = len(results)
    fn_ny = tally["FALSE_NEGATIVE_k12ny"]
    fn_other = tally["FALSE_NEGATIVE_k12other"]
    neg = tally["CONFIRMED_NEGATIVE"]

    print(f"\n{'='*55}")
    print(f"  Sampled dropped blocks  : {total}")
    print(f"  CONFIRMED_NEGATIVE      : {neg}  ({neg/total:.1%})")
    print(f"  FALSE_NEGATIVE .k12.ny  : {fn_ny}  ({fn_ny/total:.1%})")
    print(f"  FALSE_NEGATIVE other k12: {fn_other}  ({fn_other/total:.1%})")
    print(f"  Total false negatives   : {fn_ny + fn_other}  "
          f"({(fn_ny + fn_other)/total:.1%})")
    print(f"{'='*55}")
    print(f"\nNote: FNR is a lower bound — only {N_CHECK_IPS}/254 IPs checked per block.")
    print(f"Done -> {output_file}")


if __name__ == "__main__":
    run()
