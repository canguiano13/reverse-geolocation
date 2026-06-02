"""
Post-10: RIG (Tier 1) recall vs. ARIN ground truth (Tier 2).

This is the contribution-defining number for the paper.

ARIN's registry search (Phase 0) returns the set of NY school/BOCES
organizations that own registered IP space. That's the ground truth
"can be found at all" set for institutionally registered K-12 IP space.

Tier 1 (the RIG pipeline) finds a subset of that — districts whose
IPs are (a) geographically near sampled schools, AND (b) reverse-DNS
under a school-related zone (primarily .k12.ny.us).

Recall = |Tier 1 districts ∩ Tier 2 districts| / |Tier 2 districts|

We also compute IP-level coverage for districts found in both:
how many of the ARIN-registered IPs does RIG actually identify?

Output: data/outputs/recall_vs_arin.csv
        + a printed summary including the headline recall figure.
"""

import csv
import ipaddress
import os
import re
from collections import defaultdict

RADII         = [5, 10, 20]
ARIN_FILE     = "data/outputs/phase0_arin.csv"
TIER1_TPL     = "data/outputs/phase3_reattributed_{r}km.csv"
OUTPUT_FILE   = "data/outputs/recall_vs_arin.csv"


_GENERIC_DISTRICT_TOKENS = {
    "school", "schools", "district", "central", "union", "free",
    "city", "of", "the", "and", "csd", "ufsd", "boces", "middle",
    "high", "elementary", "senior", "junior", "academy",
    "primary", "secondary", "k", "12",
}


def normalize_district_name(name):
    """Return a set of significant tokens for fuzzy matching district names.

    ARIN names ("Monroe-Woodbury Central School District") and gigamaps
    names ("Monroe Woodbury Middle School") differ syntactically but
    share the distinguishing tokens.  Stripping generic structural
    tokens gives a stable signature.
    """
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    return {t for t in tokens if t not in _GENERIC_DISTRICT_TOKENS and len(t) >= 3}


def name_match(arin_name, tier1_name):
    """Two district names match if they share ≥1 distinguishing token."""
    return bool(normalize_district_name(arin_name) & normalize_district_name(tier1_name))


def load_arin_districts():
    """Returns: { arin_district_name: [(cidr, n_ips, network), ...] }."""
    by_district = defaultdict(list)
    with open(ARIN_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["school_name"].strip()
            cidr = row["cidr"].strip()
            try:
                net   = ipaddress.IPv4Network(cidr, strict=False)
                n_ips = net.num_addresses
            except ValueError:
                continue
            by_district[name].append((cidr, n_ips, net))
    return by_district


def load_tier1(radius):
    """Returns: { tier1_district_name: set_of_ips } for high-confidence IPs only."""
    path = TIER1_TPL.format(r=radius)
    if not os.path.exists(path):
        return None
    out = defaultdict(set)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("confidence") != "high":
                continue
            out[row["school_name"].strip()].add(row["ip_address"].strip())
    return out


def ips_in_networks(ips, networks):
    """How many of `ips` fall inside any of `networks`."""
    if not ips or not networks:
        return 0
    in_count = 0
    for ip_str in ips:
        try:
            ip = ipaddress.IPv4Address(ip_str)
        except ValueError:
            continue
        for net in networks:
            if ip in net:
                in_count += 1
                break
    return in_count


def run():
    arin_districts = load_arin_districts()
    n_arin = len(arin_districts)
    print(f"ARIN (Tier 2) districts: {n_arin}")

    rows = []
    summary = {}

    for r in RADII:
        tier1 = load_tier1(r)
        if tier1 is None:
            print(f"  {r}km: phase3_reattributed missing, skipping")
            continue

        matched = 0
        rig_ips_in_arin_block = 0
        arin_block_size_total = 0

        for arin_name, blocks in sorted(arin_districts.items()):
            arin_size = sum(n for _, n, _ in blocks)
            networks  = [net for _, _, net in blocks]

            # Find any Tier 1 district whose name matches this ARIN name
            tier1_match = next((t1 for t1 in tier1 if name_match(arin_name, t1)), None)

            if tier1_match:
                matched += 1
                rig_ips     = tier1[tier1_match]
                covered_ips = ips_in_networks(rig_ips, networks)
                rig_ips_in_arin_block += covered_ips
                arin_block_size_total += arin_size
                coverage_pct = (covered_ips / arin_size * 100) if arin_size else 0
                status = "BOTH"
                tier1_name_out = tier1_match
                tier1_ip_count = len(rig_ips)
                tier1_ips_in_arin = covered_ips
                tier1_ips_outside_arin = len(rig_ips) - covered_ips
            else:
                status = "ARIN_ONLY"
                tier1_name_out = ""
                tier1_ip_count = 0
                tier1_ips_in_arin = 0
                tier1_ips_outside_arin = 0
                coverage_pct = 0.0

            rows.append({
                "radius_km":              r,
                "arin_district":          arin_name,
                "arin_block_count":       len(blocks),
                "arin_total_ips":         arin_size,
                "status":                 status,
                "tier1_district":         tier1_name_out,
                "tier1_ip_count":         tier1_ip_count,
                "tier1_ips_inside_arin":  tier1_ips_in_arin,
                "tier1_ips_outside_arin": tier1_ips_outside_arin,
                "ip_coverage_pct":        round(coverage_pct, 2),
            })

        # Tier 1 districts that aren't in ARIN at all (e.g., schools whose
        # network IP space was never registered to a recognizable school org)
        tier1_only = []
        for t1 in tier1:
            if not any(name_match(arin_name, t1) for arin_name in arin_districts):
                tier1_only.append(t1)
                rows.append({
                    "radius_km":              r,
                    "arin_district":          "",
                    "arin_block_count":       0,
                    "arin_total_ips":         0,
                    "status":                 "TIER1_ONLY",
                    "tier1_district":         t1,
                    "tier1_ip_count":         len(tier1[t1]),
                    "tier1_ips_inside_arin":  0,
                    "tier1_ips_outside_arin": len(tier1[t1]),
                    "ip_coverage_pct":        0.0,
                })

        recall_pct = (matched / n_arin * 100) if n_arin else 0
        ip_coverage = (rig_ips_in_arin_block / arin_block_size_total * 100) \
                      if arin_block_size_total else 0
        summary[r] = {
            "matched":             matched,
            "n_arin":              n_arin,
            "tier1_only":          len(tier1_only),
            "recall_pct":          recall_pct,
            "ip_coverage_pct":     ip_coverage,
            "rig_ips_in_arin":     rig_ips_in_arin_block,
            "arin_size_matched":   arin_block_size_total,
        }

    fieldnames = ["radius_km", "arin_district", "arin_block_count", "arin_total_ips",
                  "status", "tier1_district", "tier1_ip_count",
                  "tier1_ips_inside_arin", "tier1_ips_outside_arin",
                  "ip_coverage_pct"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    # Headline summary
    print()
    print("=" * 75)
    print("RIG (Tier 1) recall against ARIN-registered ground truth")
    print("=" * 75)
    print(f"{'radius':>8}  {'matched':>9}  {'recall %':>10}  "
          f"{'IPs in matched block':>22}  {'block IP coverage %':>21}")
    print("-" * 75)
    for r, s in summary.items():
        print(f"{r:>6}km  {s['matched']:>4}/{s['n_arin']:<4}  "
              f"{s['recall_pct']:>9.1f}%  "
              f"{s['rig_ips_in_arin']:>10}/{s['arin_size_matched']:<10}  "
              f"{s['ip_coverage_pct']:>20.1f}%")
    print()
    print(f"Detail written to: {OUTPUT_FILE}")

    # The headline number for the paper
    if summary:
        med = sorted(summary.keys())[len(summary) // 2]   # median radius
        print()
        print(f"PAPER NUMBER (median radius={med}km):")
        print(f"  District-level recall:  {summary[med]['matched']}/"
              f"{summary[med]['n_arin']} = {summary[med]['recall_pct']:.1f}%")
        print(f"  IP-level coverage of matched districts: "
              f"{summary[med]['ip_coverage_pct']:.1f}%")


if __name__ == "__main__":
    run()
