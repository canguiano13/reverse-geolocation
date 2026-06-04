"""Per-school IP summary from Phase 3 (and Phase 4 if present)."""

import csv
import os
from collections import defaultdict

INPUT_FILE     = "data/outputs/phase3_confirmed.csv"
PHASE4_FILE    = "data/outputs/phase4_validated.csv"
SCHOOLS_FILE   = "data/inputs/schools_selected.csv"
OUTPUT_FILE    = "data/outputs/analysis_summary.csv"
OUTPUT_FILE_P4 = "data/outputs/analysis_summary_phase4.csv"

FIELDS = ["school_name", "source", "total_ips", "high", "medium", "low",
          "best_score", "best_ip", "best_hostname"]


def summarize(rows, all_schools, label):
    by_school = defaultdict(list)
    for row in rows:
        by_school[row["school_name"].strip()].append(row)

    # Phase 3b re-attributes to gigamaps names not in schools_selected.csv;
    # union both so re-attributed districts don't disappear from the report.
    sampled      = set(all_schools)
    found        = set(by_school)
    reattributed = sorted(found - sampled)
    keys         = list(all_schools) + reattributed

    high   = sum(1 for r in rows if r["confidence"] == "high")
    medium = sum(1 for r in rows if r["confidence"] == "medium")
    low    = sum(1 for r in rows if r["confidence"] == "low")

    print("=" * 50)
    print(f"  {label}")
    print("=" * 50)
    print(f"  Sampled schools          : {len(all_schools)}")
    print(f"  Re-attributed districts  : {len(reattributed)}")
    print(f"  Schools/districts w/ IPs : {len(by_school)}")
    print(f"  Sampled schools w/o IPs  : {len(sampled - found)}")
    print(f"  Total IPs                : {len(rows)}")
    print(f"  High / Medium / Low      : {high} / {medium} / {low}")
    print("=" * 50)

    summary = []
    for school in keys:
        school_rows = by_school.get(school, [])
        source = "reattributed" if school in reattributed else "sampled"

        if not school_rows:
            summary.append({
                "school_name": school, "source": source, "total_ips": 0,
                "high": 0, "medium": 0, "low": 0,
                "best_score": 0, "best_ip": "", "best_hostname": "",
            })
            continue

        h    = sum(1 for r in school_rows if r["confidence"] == "high")
        m    = sum(1 for r in school_rows if r["confidence"] == "medium")
        l    = sum(1 for r in school_rows if r["confidence"] == "low")
        best = max(school_rows, key=lambda r: int(r["score"]))

        summary.append({
            "school_name":  school,
            "source":       source,
            "total_ips":    len(school_rows),
            "high": h, "medium": m, "low": l,
            "best_score":    best["score"],
            "best_ip":       best["ip_address"],
            "best_hostname": best["hostname"],
        })
        print(f"  [{source:<12}]  {school[:45]:<45}  {len(school_rows)} IPs  "
              f"H={h} M={m} L={l}  best={best['ip_address']} (score={best['score']})")

    return summary


def write_summary(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Summary written to {path}")


def run(input_file=INPUT_FILE, phase4_file=PHASE4_FILE,
        schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE,
        output_file_p4=OUTPUT_FILE_P4):

    with open(schools_file, newline="", encoding="utf-8") as f:
        all_schools = [r["school_name"].strip() for r in csv.DictReader(f)]

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    write_summary(summarize(rows, all_schools, "Phase 3: Before RIPE Atlas Validation"),
                  output_file)

    if not os.path.exists(phase4_file):
        print(f"\nNote: {phase4_file} not found, skipping phase 4 analysis.")
        return

    with open(phase4_file, newline="", encoding="utf-8") as f:
        p4 = list(csv.DictReader(f))
    validated = [r for r in p4 if r.get("ripe_validated") == "yes"]
    removed   = sum(1 for r in p4 if r.get("ripe_validated") == "no")
    skipped   = sum(1 for r in p4 if r.get("ripe_validated") == "skipped")
    print(f"\nRIPE Atlas: removed {removed} IPs, {skipped} skipped (no probes)")
    write_summary(summarize(validated, all_schools, "Phase 4: After RIPE Atlas Validation"),
                  output_file_p4)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius", default="20km",
                        help="Radius suffix used in filenames (e.g. 10km, 20km)")
    args = parser.parse_args()
    r = args.radius
    run(
        input_file     = f"data/outputs/phase3_reattributed_{r}.csv",
        phase4_file    = f"data/outputs/phase4_validated_{r}.csv",
        schools_file   = SCHOOLS_FILE,
        output_file    = f"data/outputs/analysis_summary_{r}.csv",
        output_file_p4 = f"data/outputs/analysis_summary_phase4_{r}.csv",
    )
