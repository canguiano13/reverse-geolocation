"""
combined_summary.py — Two-tier results summary.

Tier 1 (RIG-confirmed): Districts identified via GeoLite2 proximity + reverse DNS.
  PTR records in *.k12.ny.us prove the IP is actively used by a NY school district.
  These are the core RIG findings — districts whose infrastructure was geolocated
  without prior knowledge of their IP blocks.

Tier 2 (ARIN ownership): Districts with IP blocks registered in ARIN WHOIS.
  Proves the district owns those blocks, but does not confirm active use or
  precise geolocation. Complementary to RIG — captures self-hosted infrastructure
  that may not have reverse DNS configured.

Together the two tiers show:
  - Which districts are findable via RIG (ISP-managed, PTR-configured infrastructure)
  - Which districts are findable via ARIN (self-registered, self-hosted blocks)
  - Where the two methods overlap (strongest evidence)

Output: data/outputs/combined_results.csv
"""

import csv
import re
import ipaddress
from collections import defaultdict

# Generic words that appear in nearly every school/district name and therefore
# cannot distinguish one district from another for overlap detection.
OVERLAP_STOP = {
    "school", "schools", "district", "central", "union", "free",
    "high", "middle", "elementary", "public", "city", "county",
    "board", "education", "east", "west", "north", "south",
    "town", "village", "campus", "annex", "academy", "institute",
    "boces", "ufsd", "csd",
    # Additional generics that caused false dual-confirmation matches
    "center", "regional", "information", "lower", "upper", "valley",
    "fire", "training", "technical", "learning",
}


def content_words(name):
    """Return meaningful (non-generic) words from a district/school name."""
    words = [w.lower() for w in re.split(r'[\s\-\/]+', name) if len(w) > 3]
    return [w for w in words if w not in OVERLAP_STOP]

PHASE0_FILE   = "data/outputs/phase0_arin.csv"
PHASE3_FILE   = "data/outputs/phase3_reattributed_10km.csv"
PHASE4_FILE   = "data/outputs/phase4_validated_10km.csv"
OUTPUT_FILE   = "data/outputs/combined_results_10km.csv"


def block_size(cidr):
    """Return number of usable host IPs in a CIDR block."""
    try:
        return max(1, ipaddress.ip_network(cidr, strict=False).num_addresses - 2)
    except Exception:
        return 0


def run(phase0_file=PHASE0_FILE, phase3_file=PHASE3_FILE,
        phase4_file=PHASE4_FILE, output_file=OUTPUT_FILE):

    # ── Tier 1: RIG-confirmed districts ──────────────────────────────────────
    rig_rows = list(csv.DictReader(open(phase3_file, newline="", encoding="utf-8")))

    # Load Phase 4 validation status
    p4_status = {}
    try:
        for r in csv.DictReader(open(phase4_file, newline="", encoding="utf-8")):
            p4_status[r["ip_address"]] = r.get("ripe_validated", "not_run")
    except FileNotFoundError:
        pass

    rig_by_district = defaultdict(list)
    for r in rig_rows:
        rig_by_district[r["school_name"]].append(r)

    # ── Tier 2: ARIN-ownership districts ─────────────────────────────────────
    arin_rows = list(csv.DictReader(open(phase0_file, newline="", encoding="utf-8")))

    arin_by_district = defaultdict(list)
    for r in arin_rows:
        arin_by_district[r["school_name"]].append(r)

    # ── Build combined output ─────────────────────────────────────────────────
    output_rows = []

    print("\n" + "=" * 65)
    print("  TIER 1 — RIG CONFIRMED (GeoLite2 + Reverse DNS)")
    print("  IP blocks geolocated near school + *.k12.ny.us PTR records")
    print("=" * 65)

    for district, ips in sorted(rig_by_district.items(), key=lambda x: -len(x[1])):
        high  = sum(1 for r in ips if r["confidence"] == "high")
        total = len(ips)
        ny_k12 = sum(1 for r in ips if r.get("ny_k12_domain") == "yes")

        # Skip entries with no k12.ny.us confirmation and no high-confidence IPs.
        # These are false positives from keyword matching (e.g. private CT schools,
        # non-school entities) that passed Phase 2 but have no authoritative signal.
        if ny_k12 == 0 and high == 0:
            continue

        # RIPE Atlas status
        ripe_yes    = sum(1 for r in ips if p4_status.get(r["ip_address"]) == "yes")
        ripe_skip   = sum(1 for r in ips if p4_status.get(r["ip_address"]) == "skipped")

        # Also in ARIN? Use content words only to avoid generic-word false matches.
        # Use word-boundary matching to prevent "great" matching "greater", etc.
        cw = content_words(district)
        arin_overlap = bool(cw) and any(
            any(re.search(r'\b' + re.escape(w) + r'\b', arin_d.lower()) for w in cw)
            for arin_d in arin_by_district
        )

        # Sample hostname
        sample = next((r["hostname"] for r in ips if r.get("ny_k12_domain") == "yes"), "")

        # District type — first non-empty value across IPs for this district
        district_type = next(
            (r.get("district_type", "public") for r in ips if r.get("district_type")),
            "public"
        )

        print(f"\n  {district}")
        print(f"    IPs confirmed : {total} ({high} high confidence)")
        print(f"    k12.ny.us     : {ny_k12} IPs with NY state PTR records")
        print(f"    RIPE Atlas    : {ripe_yes} validated / {ripe_skip} skipped (ICMP blocked)")
        print(f"    Also in ARIN  : {'yes — dual confirmation' if arin_overlap else 'no — RIG only'}")
        if district_type != "public":
            print(f"    Type          : *** {district_type.upper()} ***")
        print(f"    Sample PTR    : {sample}")

        output_rows.append({
            "tier":            1,
            "district":        district,
            "district_type":   district_type,
            "method":          "RIG (GeoLite2 + reverse DNS)",
            "total_ips":       total,
            "high_confidence": high,
            "ny_k12_confirmed":ny_k12,
            "ripe_validated":  ripe_yes,
            "ripe_skipped":    ripe_skip,
            "also_in_arin":    "yes" if arin_overlap else "no",
            "arin_blocks":     "",
            "arin_block_size": "",
            "sample_hostname": sample,
        })

    print("\n\n" + "=" * 65)
    print("  TIER 2 — ARIN OWNERSHIP (WHOIS registration)")
    print("  IP blocks registered to NY school districts in ARIN")
    print("=" * 65)

    # Group ARIN by district, skip those already in Tier 1
    tier1_names = set(rig_by_district.keys())

    for district, blocks in sorted(arin_by_district.items()):
        total_ips = sum(block_size(r["cidr"]) for r in blocks)
        cidrs = [r["cidr"] for r in blocks]

        # Check overlap with Tier 1 — content words only.
        # Use word-boundary matching to prevent "great" matching "greater", etc.
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
            "tier":            2,
            "district":        district,
            "district_type":   "public",   # ARIN registrants are public entities
            "method":          "ARIN WHOIS ownership",
            "total_ips":       total_ips,
            "high_confidence": "",
            "ny_k12_confirmed":"",
            "ripe_validated":  "",
            "ripe_skipped":    "",
            "also_in_arin":    "yes",
            "arin_blocks":     " | ".join(cidrs),
            "arin_block_size": total_ips,
            "sample_hostname": "",
        })

    # ── Write CSV ─────────────────────────────────────────────────────────────
    fieldnames = [
        "tier", "district", "district_type", "method", "total_ips", "high_confidence",
        "ny_k12_confirmed", "ripe_validated", "ripe_skipped",
        "also_in_arin", "arin_blocks", "arin_block_size", "sample_hostname",
    ]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    # ── Summary stats ─────────────────────────────────────────────────────────
    t1 = [r for r in output_rows if r["tier"] == 1]
    t2 = [r for r in output_rows if r["tier"] == 2]
    dual = [r for r in t1 if r["also_in_arin"] == "yes"]

    print("\n\n" + "=" * 65)
    print("  COMBINED SUMMARY")
    print("=" * 65)
    print(f"  Tier 1 (RIG confirmed)   : {len(t1)} districts, "
          f"{sum(r['total_ips'] for r in t1):,} IPs")
    print(f"  Tier 2 (ARIN ownership)  : {len(t2)} districts, "
          f"{sum(r['arin_block_size'] for r in t2 if r['arin_block_size']):,} registered IPs")
    print(f"  Dual confirmation        : {len(dual)} districts (both methods agree)")
    print(f"\n  Results written to {output_file}")


if __name__ == "__main__":
    run()
