import csv
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
_resolver.timeout = 1.5
_resolver.lifetime = 3.0


def reverse_dns(ip):
    try:
        rev = dns.reversename.from_address(ip)
        return str(_resolver.resolve(rev, "PTR")[0]).rstrip(".").lower()
    except Exception:
        return ""


def probe_block(cidr):
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except Exception:
        return []
    hosts = list(net.hosts())
    if not hosts:
        return []
    step = max(1, len(hosts) // N_PROBE)
    ips  = [str(h) for h in hosts[::step][:N_PROBE]]

    found = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(reverse_dns, ip): ip for ip in ips}
        for fut in as_completed(futures):
            host = fut.result()
            if ".k12.ny.us" in host:
                found.append((futures[fut], host))
    return found


def run(phase0_file=PHASE0_FILE, combined_file=COMBINED_FILE, output_file=OUTPUT_FILE):
    by_district = defaultdict(list)
    with open(phase0_file, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by_district[r["school_name"]].append(r["cidr"])

    tier1_names = set()
    try:
        with open(combined_file, newline="", encoding="utf-8") as f:
            tier1_names = {r["district"] for r in csv.DictReader(f) if r["tier"] == "1"}
    except FileNotFoundError:
        pass

    print(f"Probing {len(by_district)} ARIN districts for k12.ny.us PTR records")
    print(f"({N_PROBE} IPs sampled per CIDR, {MAX_WORKERS} parallel threads)\n")

    rows = []
    for district, cidrs in sorted(by_district.items()):
        found = []
        for cidr in cidrs:
            found.extend(probe_block(cidr))

        also_t1 = any(district.lower() in t.lower() or t.lower() in district.lower()
                      for t in tier1_names)
        sample = found[0][1] if found else ""

        status = "HAS k12.ny.us PTR" if found else "no PTR records"
        note   = " (also Tier 1, expected)" if also_t1 else ""
        print(f"  {district}")
        print(f"    {status}{note}")
        print(f"    blocks: {', '.join(cidrs)}")
        if sample:
            print(f"    sample: {sample}")

        rows.append({
            "district":      district,
            "cidrs":         " | ".join(cidrs),
            "also_tier1":    "yes" if also_t1 else "no",
            "k12_ptr_found": "yes" if found else "no",
            "k12_ptr_count": len(found),
            "sample_ptr":    sample,
        })

    fields = ["district", "cidrs", "also_tier1", "k12_ptr_found",
              "k12_ptr_count", "sample_ptr"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    has_ptr = [r for r in rows if r["k12_ptr_found"] == "yes"]
    no_ptr  = [r for r in rows if r["k12_ptr_found"] == "no"]
    t2_with = [r for r in has_ptr if r["also_tier1"] == "no"]
    t2_only = [r for r in no_ptr  if r["also_tier1"] == "no"]

    print(f"\n{'=' * 60}")
    print(f"  RECALL ESTIMATE SUMMARY")
    print(f"{'=' * 60}")
    print(f"  ARIN districts probed          : {len(rows)}")
    print(f"  Have k12.ny.us PTR records     : {len(has_ptr)}")
    print(f"    - also in Tier 1 (expected)  : {len(has_ptr) - len(t2_with)}")
    print(f"    - Tier 2 only (RIG missed)   : {len(t2_with)}")
    print(f"  No k12.ny.us PTR records       : {len(no_ptr)}")
    print(f"    - Tier 2 only (ARIN-only)    : {len(t2_only)}")

    if t2_with:
        print(f"\n  -> RIG missed {len(t2_with)} district(s) that have PTR records:")
        for r in t2_with:
            print(f"      {r['district']}  ({r['sample_ptr']})")
        print(f"    Cause: GeoLite2 geo-accuracy, not absence of PTR records.")
    else:
        print(f"\n  -> No Tier-2-only districts have k12.ny.us PTR records.")
        print(f"    RIG found everything findable; remaining districts are ARIN-only.")
    print(f"\n  Results written to {output_file}")


if __name__ == "__main__":
    run()
