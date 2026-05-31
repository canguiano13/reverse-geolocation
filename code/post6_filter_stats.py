"""
Filter impact statistics.

Per-radius table of how many /24 prefixes, IPs, and schools survive each
pipeline stage. Mirrors Table 4 from the original paper.
"""

import csv
import ipaddress
import os

RADII       = [5, 10, 20, 30]
OUTPUT_DIR  = "data/outputs"
OUTPUT_FILE = f"{OUTPUT_DIR}/filter_stats.csv"


def ip_to_24(ip):
    return str(ipaddress.IPv4Network(f"{ip}/24", strict=False).network_address)


def cidr_to_24s(cidr):
    """Return list of /24 prefixes contained in a CIDR. Skips IPv6/malformed."""
    try:
        net = ipaddress.IPv4Network(cidr, strict=False)
    except (ipaddress.AddressValueError, ValueError):
        return []
    if net.prefixlen >= 24:
        return [str(net.network_address)]
    return [str(s.network_address) for s in net.subnets(new_prefix=24)]


def count_csv_rows(path):
    if not os.path.exists(path):
        return 0
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def count_unique_24s_from_ips(path, col="ip_address"):
    """Unique /24 prefixes from an IP column."""
    if not os.path.exists(path):
        return 0
    seen = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = (row.get(col) or "").strip()
            if v:
                seen.add(ip_to_24(v))
    return len(seen)


def count_unique_24s_from_cidrs(path, col="cidr"):
    """Unique /24 prefixes from a CIDR column. Expands wider CIDRs into /24s."""
    if not os.path.exists(path):
        return 0
    seen = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = (row.get(col) or "").strip()
            if v:
                seen.update(cidr_to_24s(v))
    return len(seen)


def count_unique_values(path, col):
    if not os.path.exists(path):
        return 0
    seen = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = (row.get(col) or "").strip()
            if v:
                seen.add(v)
    return len(seen)


def count_high_confidence(path):
    """Returns (high-confidence IP count, distinct school count)."""
    if not os.path.exists(path):
        return 0, 0
    ips = 0
    schools = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("confidence", "").strip() == "high":
                ips += 1
                if row.get("school_name"):
                    schools.add(row["school_name"].strip())
    return ips, len(schools)


def stats_for_radius(r):
    p_phase1        = f"{OUTPUT_DIR}/phase1_candidates_{r}km.csv"
    p_merged        = f"{OUTPUT_DIR}/phase_candidates_{r}km.csv"
    p_phase2        = f"{OUTPUT_DIR}/phase2_filtered_{r}km.csv"
    p_phase3        = f"{OUTPUT_DIR}/phase3_confirmed_{r}km.csv"
    p_phase3_reattr = f"{OUTPUT_DIR}/phase3_reattributed_{r}km.csv"

    p3_high_ips, p3_high_schools  = count_high_confidence(p_phase3)
    _,           p3r_high_districts = count_high_confidence(p_phase3_reattr)

    return {
        "radius_km":                   r,
        "phase1_24s":                  count_unique_24s_from_cidrs(p_phase1),
        "phase1_schools_w_blocks":     count_unique_values(p_phase1, "school_name"),
        "phase_merged_24s":            count_unique_24s_from_cidrs(p_merged),
        "phase2_matched_ips":          count_csv_rows(p_phase2),
        "phase2_unique_24s":           count_unique_24s_from_ips(p_phase2),
        "phase2_schools_w_match":      count_unique_values(p_phase2, "school_name"),
        "phase3_total_ips":            count_csv_rows(p_phase3),
        "phase3_unique_24s":           count_unique_24s_from_ips(p_phase3),
        "phase3_high_conf_ips":        p3_high_ips,
        "phase3_high_conf_schools":    p3_high_schools,
        "phase3b_high_conf_districts": p3r_high_districts,
    }


def run():
    rows = [stats_for_radius(r) for r in RADII]

    verification_path = f"{OUTPUT_DIR}/verification_results.csv"
    tp = fp = mc = 0
    if os.path.exists(verification_path):
        with open(verification_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                v = row.get("verdict", "").strip()
                if v == "TRUE_POSITIVE":  tp += 1
                elif v == "FALSE_POSITIVE": fp += 1
                elif v == "MANUAL_CHECK":   mc += 1

    fieldnames = list(rows[0].keys())
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("=" * 78)
    print("FILTER IMPACT (per radius)")
    print("=" * 78)
    print(f"{'Stage':<35} " + " ".join(f"{r:>5}km" for r in RADII))
    print("-" * 78)

    def line(label, key):
        vals = [f"{row[key]:>7,}" for row in rows]
        print(f"{label:<35} " + " ".join(vals))

    line("Phase 1 (/24 prefixes)",          "phase1_24s")
    line("Phase 1 (schools w/ candidates)", "phase1_schools_w_blocks")
    line("Phase 1+0 merged (/24 prefixes)", "phase_merged_24s")
    line("Phase 2 (matched IPs)",           "phase2_matched_ips")
    line("Phase 2 (unique /24s)",           "phase2_unique_24s")
    line("Phase 2 (schools w/ match)",      "phase2_schools_w_match")
    line("Phase 3 (total IPs scored)",      "phase3_total_ips")
    line("Phase 3 (high-confidence IPs)",   "phase3_high_conf_ips")
    line("Phase 3 (high-conf schools)",     "phase3_high_conf_schools")
    line("Phase 3b (high-conf districts)",  "phase3b_high_conf_districts")

    print()
    print("=" * 78)
    print("VERIFICATION (all radii combined)")
    print("=" * 78)
    total_verified = tp + fp + mc
    print(f"  TRUE_POSITIVE : {tp:>6}")
    print(f"  FALSE_POSITIVE: {fp:>6}")
    print(f"  MANUAL_CHECK  : {mc:>6}")
    print(f"  Total verified: {total_verified:>6}")
    if tp + fp > 0:
        print(f"  Precision     : {tp / (tp + fp):.2%}  (excluding manual check)")
    print()
    print(f"Stats table written to {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
