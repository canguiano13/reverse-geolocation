"""RIG (Tier 1) recall vs. ARIN (Tier 2)."""

import csv
import ipaddress
import os
import re
from collections import defaultdict

RADII = [5, 10, 20]
OUTPUT_FILE = "data/outputs/recall_vs_arin.csv"

# Drop generic structural tokens so ARIN names like
# "Monroe-Woodbury Central School District" match gigamaps names like
# "Monroe Woodbury Middle School" on the distinguishing tokens.
GENERIC = {"school", "schools", "district", "central", "union", "free",
           "city", "of", "the", "and", "csd", "ufsd", "boces", "middle",
           "high", "elementary", "senior", "junior", "academy",
           "primary", "secondary", "k", "12"}


def tokens(name):
    return {t for t in re.findall(r"[a-z0-9]+", name.lower())
            if t not in GENERIC and len(t) >= 3}


def name_match(a, b):
    return bool(tokens(a) & tokens(b))


def load_arin():
    out = defaultdict(list)
    with open("data/outputs/phase0_arin.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                net = ipaddress.IPv4Network(row["cidr"].strip(), strict=False)
            except ValueError:
                continue
            out[row["school_name"].strip()].append(net)
    return out


def load_tier1(r):
    path = f"data/outputs/phase3_reattributed_{r}km.csv"
    if not os.path.exists(path):
        return None
    out = defaultdict(set)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("confidence") == "high":
                out[row["school_name"].strip()].add(row["ip_address"].strip())
    return out


def run():
    arin = load_arin()
    n_arin = len(arin)
    print(f"ARIN (Tier 2) districts: {n_arin}")

    rows = []
    summary = {}

    for r in RADII:
        tier1 = load_tier1(r)
        if tier1 is None:
            print(f"  {r}km: phase3_reattributed missing, skipping")
            continue

        matched = 0
        rig_in_arin = 0
        arin_matched_size = 0

        for arin_name, nets in sorted(arin.items()):
            arin_size = sum(n.num_addresses for n in nets)
            t1 = next((n for n in tier1 if name_match(arin_name, n)), None)

            if t1:
                matched += 1
                rig_ips = tier1[t1]
                covered = 0
                for ip in rig_ips:
                    try:
                        addr = ipaddress.IPv4Address(ip)
                    except ValueError:
                        continue
                    if any(addr in n for n in nets):
                        covered += 1
                rig_in_arin += covered
                arin_matched_size += arin_size
                pct = (covered / arin_size * 100) if arin_size else 0
                rows.append({
                    "radius_km": r,
                    "arin_district": arin_name,
                    "arin_block_count": len(nets),
                    "arin_total_ips": arin_size,
                    "status": "BOTH",
                    "tier1_district": t1,
                    "tier1_ip_count": len(rig_ips),
                    "tier1_ips_inside_arin": covered,
                    "tier1_ips_outside_arin": len(rig_ips) - covered,
                    "ip_coverage_pct": round(pct, 2),
                })
            else:
                rows.append({
                    "radius_km": r,
                    "arin_district": arin_name,
                    "arin_block_count": len(nets),
                    "arin_total_ips": arin_size,
                    "status": "ARIN_ONLY",
                    "tier1_district": "",
                    "tier1_ip_count": 0,
                    "tier1_ips_inside_arin": 0,
                    "tier1_ips_outside_arin": 0,
                    "ip_coverage_pct": 0.0,
                })

        for t1 in tier1:
            if not any(name_match(a, t1) for a in arin):
                rows.append({
                    "radius_km": r,
                    "arin_district": "",
                    "arin_block_count": 0,
                    "arin_total_ips": 0,
                    "status": "TIER1_ONLY",
                    "tier1_district": t1,
                    "tier1_ip_count": len(tier1[t1]),
                    "tier1_ips_inside_arin": 0,
                    "tier1_ips_outside_arin": len(tier1[t1]),
                    "ip_coverage_pct": 0.0,
                })

        summary[r] = {
            "matched": matched,
            "n_arin": n_arin,
            "recall_pct": (matched / n_arin * 100) if n_arin else 0,
            "ip_coverage_pct": (rig_in_arin / arin_matched_size * 100) if arin_matched_size else 0,
            "rig_ips_in_arin": rig_in_arin,
            "arin_size_matched": arin_matched_size,
        }

    fieldnames = ["radius_km", "arin_district", "arin_block_count", "arin_total_ips",
                  "status", "tier1_district", "tier1_ip_count",
                  "tier1_ips_inside_arin", "tier1_ips_outside_arin", "ip_coverage_pct"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print("\n" + "=" * 75)
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

    print(f"\nDetail written to: {OUTPUT_FILE}")

    if summary:
        med = sorted(summary)[len(summary) // 2]
        s = summary[med]
        print(f"\nPAPER NUMBER (median radius={med}km):")
        print(f"  District-level recall:  {s['matched']}/{s['n_arin']} = {s['recall_pct']:.1f}%")
        print(f"  IP-level coverage of matched districts: {s['ip_coverage_pct']:.1f}%")


if __name__ == "__main__":
    run()
