"""
Post-9: radius-sensitivity comparison.

Quantifies how Tier-1 results vary across the four radii (5, 10, 20, 30 km).
If the results are essentially identical across radii (as our run suggests),
that itself is a finding to cite in the paper: the bottleneck is not the
geographic search radius but DNS-zone coverage.

Output: data/outputs/radius_sensitivity.csv  (radius, total_ips, n_districts,
            unique_24s, ny_k12_ips, top_district, top_district_ips)
        + a printed comparison table.

Reads:  phase3_reattributed_{R}km.csv for R in [5,10,20,30]
"""

import csv
import ipaddress
import os
from collections import Counter, defaultdict

RADII   = [5, 10, 20, 30]
INPUT_TPL = "data/outputs/phase3_reattributed_{r}km.csv"
OUT_FILE  = "data/outputs/radius_sensitivity.csv"


def ip_to_24(ip):
    try:
        return str(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    except ValueError:
        return ""


def run():
    summary_rows = []
    print(f"{'radius':>8} {'high IPs':>10} {'districts':>10} {'unique /24s':>13} "
          f"{'k12.ny.us':>10} {'top district':<35} {'top IPs':>8}")
    print("-" * 100)

    for r in RADII:
        path = INPUT_TPL.format(r=r)
        if not os.path.exists(path):
            print(f"{r:>6}km  (missing)")
            summary_rows.append({"radius": r, "status": "missing"})
            continue

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        high = [row for row in rows if row.get("confidence") == "high"]
        per_district = Counter(row["school_name"] for row in high)
        unique_24 = {ip_to_24(row["ip_address"]) for row in high}
        unique_24.discard("")
        k12_count = sum(1 for row in high if row.get("ny_k12_domain") == "yes")
        top, top_n = per_district.most_common(1)[0] if per_district else ("(none)", 0)

        print(f"{r:>6}km  {len(high):>10} {len(per_district):>10} "
              f"{len(unique_24):>13} {k12_count:>10}  {top[:33]:<35} {top_n:>8}")

        summary_rows.append({
            "radius":           r,
            "total_high_ips":   len(high),
            "n_districts":      len(per_district),
            "unique_24_blocks": len(unique_24),
            "ny_k12_ips":       k12_count,
            "top_district":     top,
            "top_district_ips": top_n,
            "status":           "ok",
        })

    # Set-level comparison: how do districts change between radii?
    # If the set is identical across all radii, the radius parameter is
    # effectively a no-op for our results — write that.
    sets = {}
    for r in RADII:
        path = INPUT_TPL.format(r=r)
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as f:
            sets[r] = {row["school_name"] for row in csv.DictReader(f)
                       if row.get("confidence") == "high"}

    print()
    print("District set overlap:")
    radii_present = sorted(sets.keys())
    for i, r1 in enumerate(radii_present):
        for r2 in radii_present[i + 1:]:
            only_r1 = sets[r1] - sets[r2]
            only_r2 = sets[r2] - sets[r1]
            common  = sets[r1] & sets[r2]
            print(f"  {r1}km vs {r2}km : common={len(common)}, "
                  f"only-{r1}km={len(only_r1)}, only-{r2}km={len(only_r2)}")

    fieldnames = ["radius", "status", "total_high_ips", "n_districts",
                  "unique_24_blocks", "ny_k12_ips", "top_district", "top_district_ips"]
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in summary_rows:
            w.writerow(row)
    print(f"\nWritten -> {OUT_FILE}")


if __name__ == "__main__":
    run()
