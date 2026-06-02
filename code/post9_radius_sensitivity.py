"""Compare Tier 1 results across radii."""

import csv
import ipaddress
import os
from collections import Counter

RADII = [5, 10, 20, 30]
OUT_FILE = "data/outputs/radius_sensitivity.csv"


def run():
    rows_out = []
    sets = {}

    print(f"{'radius':>8} {'high IPs':>10} {'districts':>10} {'unique /24s':>13} "
          f"{'k12.ny.us':>10} {'top district':<35} {'top IPs':>8}")
    print("-" * 100)

    for r in RADII:
        path = f"data/outputs/phase3_reattributed_{r}km.csv"
        if not os.path.exists(path):
            print(f"{r:>6}km  (missing)")
            rows_out.append({"radius": r, "status": "missing"})
            continue

        with open(path, newline="", encoding="utf-8") as f:
            rows = [row for row in csv.DictReader(f) if row.get("confidence") == "high"]

        per_district = Counter(row["school_name"] for row in rows)
        unique_24 = set()
        for row in rows:
            try:
                unique_24.add(str(ipaddress.IPv4Network(f"{row['ip_address']}/24", strict=False)))
            except ValueError:
                pass
        k12 = sum(1 for row in rows if row.get("ny_k12_domain") == "yes")
        top, top_n = per_district.most_common(1)[0] if per_district else ("(none)", 0)

        print(f"{r:>6}km  {len(rows):>10} {len(per_district):>10} "
              f"{len(unique_24):>13} {k12:>10}  {top[:33]:<35} {top_n:>8}")

        sets[r] = set(per_district)
        rows_out.append({
            "radius": r,
            "total_high_ips": len(rows),
            "n_districts": len(per_district),
            "unique_24_blocks": len(unique_24),
            "ny_k12_ips": k12,
            "top_district": top,
            "top_district_ips": top_n,
            "status": "ok",
        })

    print("\nDistrict set overlap:")
    radii = sorted(sets)
    for i, r1 in enumerate(radii):
        for r2 in radii[i + 1:]:
            common = sets[r1] & sets[r2]
            print(f"  {r1}km vs {r2}km : common={len(common)}, "
                  f"only-{r1}km={len(sets[r1] - sets[r2])}, "
                  f"only-{r2}km={len(sets[r2] - sets[r1])}")

    fieldnames = ["radius", "status", "total_high_ips", "n_districts",
                  "unique_24_blocks", "ny_k12_ips", "top_district", "top_district_ips"]
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_out)
    print(f"\nWritten -> {OUT_FILE}")


if __name__ == "__main__":
    run()
