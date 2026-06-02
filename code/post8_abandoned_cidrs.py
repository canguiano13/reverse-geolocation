"""
Post-8: identify CIDR blocks that entered Phase 2 but were never resolved.

Phase 2 wraps every probe batch in a wall-clock timeout. When a batch times
out, any futures still running are abandoned with no recorded hostname.
This script reconstructs the set of "abandoned" /24 blocks for each radius
by diffing phase_candidates_{R}km.csv against phase2_filtered_{R}km.csv,
filtered to schools that were actually processed (i.e., MAX_CIDRS-eligible).

The point is transparency: reviewers will ask whether the timeouts hide
real school IPs. The output lets us either re-probe these blocks in a
follow-up pass or quote "abandoned CIDRs contributed 0 hits" in the paper.

Output: data/outputs/abandoned_cidrs_{R}km.csv
        (cidr, school_name, reason)
        + a summary line per radius.
"""

import csv
import ipaddress
import os

RADII     = [5, 10, 20, 30]
MAX_CIDRS = 500   # must match the Phase 2 constant

CANDIDATES_TPL = "data/outputs/phase_candidates_{r}km.csv"
PHASE2_TPL     = "data/outputs/phase2_filtered_{r}km.csv"
OUT_TPL        = "data/outputs/abandoned_cidrs_{r}km.csv"


def ip_to_24(ip):
    """Map any IPv4 string to its /24 network address as a CIDR."""
    try:
        return str(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    except ValueError:
        return None


def run():
    for r in RADII:
        cand_path = CANDIDATES_TPL.format(r=r)
        p2_path   = PHASE2_TPL.format(r=r)
        out_path  = OUT_TPL.format(r=r)

        if not (os.path.exists(cand_path) and os.path.exists(p2_path)):
            print(f"{r}km: missing inputs, skipping")
            continue

        # Count blocks per school in the candidate set so we can filter out
        # schools that Phase 2 would have skipped under MAX_CIDRS.
        block_count = {}
        school_cidrs = {}
        with open(cand_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                s = row["school_name"].strip()
                c = row["cidr"].strip()
                block_count[s] = block_count.get(s, 0) + 1
                school_cidrs.setdefault(s, set()).add(c)

        eligible_schools = {s for s, n in block_count.items() if n <= MAX_CIDRS}

        # CIDRs that produced a successful Phase 2 match (the /24 covers the IP).
        resolved_cidrs = set()
        with open(p2_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                c24 = ip_to_24(row["ip_address"].strip())
                if c24:
                    resolved_cidrs.add(c24)

        # An "abandoned" CIDR is one that:
        #   - belongs to an eligible school (under MAX_CIDRS), AND
        #   - did not contribute any IP to the Phase 2 output.
        # This conflates "probed and rejected" with "probed and timed out" —
        # the Phase 2 logs distinguish them but the CSV does not. Reviewers
        # should treat this number as an upper bound on timeout-abandoned blocks.
        abandoned = []
        for s in eligible_schools:
            for c in school_cidrs[s]:
                if c not in resolved_cidrs:
                    abandoned.append((c, s))

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["cidr", "school_name", "reason"])
            for c, s in abandoned:
                w.writerow([c, s, "no_phase2_match_or_timed_out"])

        skipped_schools = len(block_count) - len(eligible_schools)
        eligible_blocks = sum(block_count[s] for s in eligible_schools)
        print(f"{r}km: eligible schools={len(eligible_schools)} "
              f"(skipped {skipped_schools} over MAX_CIDRS={MAX_CIDRS}), "
              f"eligible CIDRs={eligible_blocks}, "
              f"resolved={len(resolved_cidrs)}, abandoned/rejected={len(abandoned)} "
              f"-> {out_path}")


if __name__ == "__main__":
    run()
