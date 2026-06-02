"""
Probe ARIN Tier 2 blocks for k12.ny.us PTR records to estimate RIG recall.

For each ARIN-registered district, sample IPs from their blocks and check
for k12.ny.us PTR records.

  PTR records found in Tier 2 block: RIG could theoretically find this
  district but missed it (GeoLite2 placed the block outside our radius).

  No PTR records found: ARIN is the only discovery method for this district.

Input:  data/outputs/phase0_arin.csv
        data/outputs/combined_results_10km.csv  (for Tier 1 overlap annotation)
Output: data/outputs/recall_estimate.csv
"""

import csv
import ipaddress
import socket
import concurrent.futures
from collections import defaultdict
import dns.resolver
import dns.reversename

PHASE0_FILE   = "data/outputs/phase0_arin.csv"
COMBINED_FILE = "data/outputs/combined_results_10km.csv"
OUTPUT_FILE   = "data/outputs/recall_estimate.csv"
N_PROBE       = 50
MAX_WORKERS   = 30
TIMEOUT       = 3.0

socket.setdefaulttimeout(TIMEOUT)


# Custom resolver bypassing macOS mDNSResponder (which throttles aggressively
# under sustained PTR load and silently drops queries — same issue we fixed
# in phase2_dns_lookup.py).
def _make_resolver():
    r = dns.resolver.Resolver(configure=False)
    r.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
    r.timeout  = 1.5
    r.lifetime = 3.0
    return r

_RESOLVER = _make_resolver()


def reverse_dns(ip):
    try:
        rev     = dns.reversename.from_address(ip)
        answers = _RESOLVER.resolve(rev, "PTR")
        return str(answers[0]).rstrip(".").lower()
    except Exception:
        return ""


def sample_ips(cidr, n=N_PROBE):
    """Up to n evenly-spaced host IPs from a CIDR block."""
    try:
        net   = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
        if not hosts:
            return []
        step = max(1, len(hosts) // n)
        return [str(h) for h in hosts[::step][:n]]
    except Exception:
        return []


def probe_block(cidr):
    """Returns list of (ip, hostname) pairs with k12.ny.us PTR records."""
    ips = sample_ips(cidr)
    if not ips:
        return []
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(reverse_dns, ip): ip for ip in ips}
        for fut in concurrent.futures.as_completed(futures):
            ip       = futures[fut]
            hostname = fut.result()
            if ".k12.ny.us" in hostname:
                found.append((ip, hostname))
    return found


def run(phase0_file=PHASE0_FILE, combined_file=COMBINED_FILE,
        output_file=OUTPUT_FILE):

    with open(phase0_file, newline="", encoding="utf-8") as f:
        arin_rows = list(csv.DictReader(f))
    by_district = defaultdict(list)
    for r in arin_rows:
        by_district[r["school_name"]].append(r["cidr"])

    try:
        with open(combined_file, newline="", encoding="utf-8") as f:
            combined = list(csv.DictReader(f))
        tier1_names = {r["district"] for r in combined if r["tier"] == "1"}
    except FileNotFoundError:
        tier1_names = set()

    print(f"Probing {len(by_district)} ARIN districts for k12.ny.us PTR records")
    print(f"({N_PROBE} IPs sampled per CIDR, {MAX_WORKERS} parallel threads)\n")

    output_rows = []

    for district, cidrs in sorted(by_district.items()):
        all_found = []
        for cidr in cidrs:
            all_found.extend(probe_block(cidr))

        has_k12 = bool(all_found)
        sample  = all_found[0][1] if all_found else ""
        also_t1 = any(
            district.lower() in t1.lower() or t1.lower() in district.lower()
            for t1 in tier1_names
        )

        status    = "HAS k12.ny.us PTR" if has_k12 else "no PTR records"
        tier_note = " (also Tier 1, expected)" if also_t1 else ""
        print(f"  {district}")
        print(f"    {status}{tier_note}")
        print(f"    blocks: {', '.join(cidrs)}")
        if sample:
            print(f"    sample: {sample}")

        output_rows.append({
            "district":      district,
            "cidrs":         " | ".join(cidrs),
            "also_tier1":    "yes" if also_t1 else "no",
            "k12_ptr_found": "yes" if has_k12 else "no",
            "k12_ptr_count": len(all_found),
            "sample_ptr":    sample,
        })

    fieldnames = ["district", "cidrs", "also_tier1",
                  "k12_ptr_found", "k12_ptr_count", "sample_ptr"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    has_ptr = [r for r in output_rows if r["k12_ptr_found"] == "yes"]
    no_ptr  = [r for r in output_rows if r["k12_ptr_found"] == "no"]
    t2_with = [r for r in has_ptr if r["also_tier1"] == "no"]
    t2_only = [r for r in no_ptr  if r["also_tier1"] == "no"]

    print(f"\n{'=' * 60}")
    print(f"  RECALL ESTIMATE SUMMARY")
    print(f"{'=' * 60}")
    print(f"  ARIN districts probed          : {len(output_rows)}")
    print(f"  Have k12.ny.us PTR records     : {len(has_ptr)}")
    print(f"    - also in Tier 1 (expected)  : {len(has_ptr) - len(t2_with)}")
    print(f"    - Tier 2 only (RIG missed)   : {len(t2_with)}")
    print(f"  No k12.ny.us PTR records       : {len(no_ptr)}")
    print(f"    - Tier 2 only (ARIN-only)    : {len(t2_only)}")
    print()
    if t2_with:
        print(f"  -> RIG missed {len(t2_with)} district(s) that have PTR records:")
        for r in t2_with:
            print(f"      {r['district']}  ({r['sample_ptr']})")
        print(f"    Cause: GeoLite2 geo-accuracy, not absence of PTR records.")
    else:
        print(f"  -> No Tier-2-only districts have k12.ny.us PTR records.")
        print(f"    RIG found everything findable; remaining districts are ARIN-only.")
    print(f"\n  Results written to {output_file}")


if __name__ == "__main__":
    run()
