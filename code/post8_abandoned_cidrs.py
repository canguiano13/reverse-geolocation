"""Phase 1 CIDRs minus Phase 2 matches, per radius."""

import csv
import ipaddress
import os

RADII = [5, 10, 20, 30]
MAX_CIDRS = 500


def run():
    for r in RADII:
        cand_path = f"data/outputs/phase_candidates_{r}km.csv"
        p2_path = f"data/outputs/phase2_filtered_{r}km.csv"
        out_path = f"data/outputs/abandoned_cidrs_{r}km.csv"

        if not (os.path.exists(cand_path) and os.path.exists(p2_path)):
            print(f"{r}km: missing inputs, skipping")
            continue

        block_count = {}
        school_cidrs = {}
        with open(cand_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                s = row["school_name"].strip()
                c = row["cidr"].strip()
                block_count[s] = block_count.get(s, 0) + 1
                school_cidrs.setdefault(s, set()).add(c)

        eligible = {s for s, n in block_count.items() if n <= MAX_CIDRS}

        resolved = set()
        with open(p2_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    net = ipaddress.IPv4Network(f"{row['ip_address'].strip()}/24", strict=False)
                    resolved.add(str(net))
                except ValueError:
                    pass

        abandoned = [(c, s) for s in eligible for c in school_cidrs[s] if c not in resolved]

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["cidr", "school_name", "reason"])
            for c, s in abandoned:
                w.writerow([c, s, "no_phase2_match_or_timed_out"])

        eligible_blocks = sum(block_count[s] for s in eligible)
        print(f"{r}km: eligible schools={len(eligible)} "
              f"(skipped {len(block_count) - len(eligible)} over MAX_CIDRS={MAX_CIDRS}), "
              f"eligible CIDRs={eligible_blocks}, "
              f"resolved={len(resolved)}, abandoned/rejected={len(abandoned)} "
              f"-> {out_path}")


if __name__ == "__main__":
    run()
