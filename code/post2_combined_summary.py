import csv
import re
import ipaddress
from collections import defaultdict

OVERLAP_STOP = {
    "school", "schools", "district", "central", "union", "free",
    "high", "middle", "elementary", "public", "city", "county",
    "board", "education", "east", "west", "north", "south",
    "town", "village", "campus", "annex", "academy", "institute",
    "boces", "ufsd", "csd",
    "center", "regional", "information", "lower", "upper", "valley",
    "fire", "training", "technical", "learning",
}

PHASE0_FILE  = "data/outputs/phase0_arin.csv"
PHASE0B_FILE = "data/outputs/phase0b_rwhois.csv"
PHASE3_FILE  = "data/outputs/phase3_reattributed_10km.csv"
PHASE4_FILE  = "data/outputs/phase4_validated_10km.csv"
OUTPUT_FILE  = "data/outputs/combined_results_10km.csv"


def content_words(name):
    words = [w.lower() for w in re.split(r'[\s\-\/]+', name) if len(w) > 3]
    return [w for w in words if w not in OVERLAP_STOP]


def block_size(cidr):
    try:
        return max(1, ipaddress.ip_network(cidr, strict=False).num_addresses - 2)
    except Exception:
        return 0


def run(phase0_file=PHASE0_FILE, phase0b_file=PHASE0B_FILE,
        phase3_file=PHASE3_FILE, phase4_file=PHASE4_FILE,
        output_file=OUTPUT_FILE):

    with open(phase3_file, newline="", encoding="utf-8") as f:
        rig_rows = list(csv.DictReader(f))

    p4_status = {}
    try:
        with open(phase4_file, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                p4_status[r["ip_address"]] = r.get("ripe_validated", "not_run")
    except FileNotFoundError:
        pass

    rig_by_district = defaultdict(list)
    for r in rig_rows:
        rig_by_district[r["school_name"]].append(r)

    with open(phase0_file, newline="", encoding="utf-8") as f:
        arin_rows = list(csv.DictReader(f))

    arin_by_district = defaultdict(list)
    for r in arin_rows:
        arin_by_district[r["school_name"]].append(r)

    rwhois_by_district = defaultdict(list)
    try:
        with open(phase0b_file, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rwhois_by_district[r["school_name"]].append(r)
    except FileNotFoundError:
        pass

    output_rows = []

    print("\n" + "=" * 65)
    print("  TIER 1: RIG CONFIRMED (GeoLite2 + Reverse DNS)")
    print("  IP blocks geolocated near school + *.k12.ny.us PTR records")
    print("=" * 65)

    for district, ips in sorted(rig_by_district.items(), key=lambda x: -len(x[1])):
        high   = sum(1 for r in ips if r["confidence"] == "high")
        total  = len(ips)
        ny_k12 = sum(1 for r in ips if r.get("ny_k12_domain") == "yes")

        if ny_k12 == 0 and high == 0:
            continue

        ripe_yes  = sum(1 for r in ips if p4_status.get(r["ip_address"]) == "yes")
        ripe_skip = sum(1 for r in ips if p4_status.get(r["ip_address"]) == "skipped")

        cw = content_words(district)
        arin_overlap = bool(cw) and any(
            any(re.search(r'\b' + re.escape(w) + r'\b', arin_d.lower()) for w in cw)
            for arin_d in arin_by_district
        )

        sample        = next((r["hostname"] for r in ips if r.get("ny_k12_domain") == "yes"), "")
        district_type = next(
            (r.get("district_type", "public") for r in ips if r.get("district_type")),
            "public"
        )

        print(f"\n  {district}")
        print(f"    IPs confirmed : {total} ({high} high confidence)")
        print(f"    k12.ny.us     : {ny_k12} IPs with NY state PTR records")
        print(f"    RIPE Atlas    : {ripe_yes} validated / {ripe_skip} skipped (ICMP blocked)")
        print(f"    Also in ARIN  : {'yes (dual confirmation)' if arin_overlap else 'no (RIG only)'}")
        if district_type != "public":
            print(f"    Type          : *** {district_type.upper()} ***")
        print(f"    Sample PTR    : {sample}")

        output_rows.append({
            "tier":             1,
            "district":         district,
            "district_type":    district_type,
            "method":           "RIG (GeoLite2 + reverse DNS)",
            "total_ips":        total,
            "high_confidence":  high,
            "ny_k12_confirmed": ny_k12,
            "ripe_validated":   ripe_yes,
            "ripe_skipped":     ripe_skip,
            "also_in_arin":     "yes" if arin_overlap else "no",
            "arin_blocks":      "",
            "arin_block_size":  "",
            "sample_hostname":  sample,
        })

    print("\n\n" + "=" * 65)
    print("  TIER 2: ARIN OWNERSHIP (WHOIS registration)")
    print("  IP blocks registered to NY school districts in ARIN")
    print("=" * 65)

    tier1_names = set(rig_by_district.keys())

    for district, blocks in sorted(arin_by_district.items()):
        total_ips = sum(block_size(r["cidr"]) for r in blocks)
        cidrs     = [r["cidr"] for r in blocks]

        cw = content_words(district)
        rig_overlap = bool(cw) and any(
            any(re.search(r'\b' + re.escape(w) + r'\b', t1.lower()) for w in cw)
            for t1 in tier1_names
        )

        status = "also in Tier 1 (dual confirmation)" if rig_overlap else "ARIN only"
        print(f"\n  {district}")
        print(f"    Blocks : {', '.join(cidrs)}")
        print(f"    ~IPs   : {total_ips:,}")
        print(f"    Status : {status}")

        output_rows.append({
            "tier":             2,
            "district":         district,
            "district_type":    "public",
            "method":           "ARIN WHOIS ownership",
            "total_ips":        total_ips,
            "high_confidence":  "",
            "ny_k12_confirmed": "",
            "ripe_validated":   "",
            "ripe_skipped":     "",
            "also_in_arin":     "yes",
            "arin_blocks":      " | ".join(cidrs),
            "arin_block_size":  total_ips,
            "sample_hostname":  "",
        })

    print("\n\n" + "=" * 65)
    print("  TIER 2b: RWHOIS SUB-ALLOCATIONS")
    print("  IP blocks from ISP-maintained RWHOIS servers (not in ARIN)")
    print("=" * 65)

    # Names already covered by Tier 1 or Tier 2
    covered_names = set(rig_by_district.keys()) | set(arin_by_district.keys())

    for district, blocks in sorted(rwhois_by_district.items()):
        total_ips = sum(block_size(r["cidr"]) for r in blocks)
        cidrs     = [r["cidr"] for r in blocks]

        # Check overlap with already-covered districts
        cw = content_words(district)
        already_covered = bool(cw) and any(
            any(re.search(r'\b' + re.escape(w) + r'\b', cname.lower()) for w in cw)
            for cname in covered_names
        )

        status = "duplicate (already in Tier 1 or 2)" if already_covered else "new (RWHOIS only)"
        print(f"\n  {district}")
        print(f"    Blocks : {', '.join(cidrs)}")
        print(f"    ~IPs   : {total_ips:,}")
        print(f"    Status : {status}")

        if not already_covered:
            output_rows.append({
                "tier":             "2b",
                "district":         district,
                "district_type":    "public",
                "method":           "RWHOIS sub-allocation",
                "total_ips":        total_ips,
                "high_confidence":  "",
                "ny_k12_confirmed": "",
                "ripe_validated":   "",
                "ripe_skipped":     "",
                "also_in_arin":     "no",
                "arin_blocks":      " | ".join(cidrs),
                "arin_block_size":  total_ips,
                "sample_hostname":  "",
            })

    fieldnames = [
        "tier", "district", "district_type", "method", "total_ips", "high_confidence",
        "ny_k12_confirmed", "ripe_validated", "ripe_skipped",
        "also_in_arin", "arin_blocks", "arin_block_size", "sample_hostname",
    ]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    t1   = [r for r in output_rows if r["tier"] == 1]
    t2   = [r for r in output_rows if r["tier"] == 2]
    t2b  = [r for r in output_rows if r["tier"] == "2b"]
    dual = [r for r in t1 if r["also_in_arin"] == "yes"]

    print("\n\n" + "=" * 65)
    print("  COMBINED SUMMARY")
    print("=" * 65)
    print(f"  Tier 1  (RIG confirmed)    : {len(t1)} districts, "
          f"{sum(r['total_ips'] for r in t1):,} IPs")
    print(f"  Tier 2  (ARIN ownership)   : {len(t2)} districts, "
          f"{sum(r['arin_block_size'] for r in t2 if r['arin_block_size']):,} registered IPs")
    print(f"  Tier 2b (RWHOIS new only)  : {len(t2b)} districts, "
          f"{sum(r['arin_block_size'] for r in t2b if r['arin_block_size']):,} registered IPs")
    print(f"  Dual confirmation          : {len(dual)} districts (both methods agree)")
    print(f"  Total unique districts     : {len(t1) + len(t2) + len(t2b)}")
    print(f"\n  Results written to {output_file}")


if __name__ == "__main__":
    run()
