"""
Per-school IP summary.

Reads phase 3 (and optionally phase 4) results and prints/writes a breakdown
of IPs found per school at each confidence level.
"""

import csv
import os
from collections import defaultdict

INPUT_FILE     = "data/outputs/phase3_confirmed.csv"
PHASE4_FILE    = "data/outputs/phase4_validated.csv"
SCHOOLS_FILE   = "data/inputs/metro_schools_nyc.csv"
OUTPUT_FILE    = "data/outputs/analysis_summary.csv"
OUTPUT_FILE_P4 = "data/outputs/analysis_summary_phase4.csv"


def summarize(rows, all_schools, label):
    by_school = defaultdict(list)
    for row in rows:
        by_school[row["school_name"].strip()].append(row)

    high   = sum(1 for r in rows if r["confidence"] == "high")
    medium = sum(1 for r in rows if r["confidence"] == "medium")
    low    = sum(1 for r in rows if r["confidence"] == "low")

    print("=" * 50)
    print(f"  {label}")
    print("=" * 50)
    print(f"  Schools processed   : {len(all_schools)}")
    print(f"  Schools with results: {len(by_school)}")
    print(f"  Schools no results  : {len(all_schools) - len(by_school)}")
    print(f"  Total IPs           : {len(rows)}")
    print(f"  High / Medium / Low : {high} / {medium} / {low}")
    print("=" * 50)

    summary_rows = []
    for school in all_schools:
        school_rows = by_school.get(school, [])
        if not school_rows:
            summary_rows.append({
                "school_name": school, "total_ips": 0,
                "high": 0, "medium": 0, "low": 0,
                "best_score": 0, "best_ip": "", "best_hostname": "",
            })
            print(f"  {school[:45]:<45}  no results")
            continue

        h    = sum(1 for r in school_rows if r["confidence"] == "high")
        m    = sum(1 for r in school_rows if r["confidence"] == "medium")
        l    = sum(1 for r in school_rows if r["confidence"] == "low")
        best = max(school_rows, key=lambda r: int(r["score"]))

        summary_rows.append({
            "school_name":   school,
            "total_ips":     len(school_rows),
            "high":          h, "medium": m, "low": l,
            "best_score":    best["score"],
            "best_ip":       best["ip_address"],
            "best_hostname": best["hostname"],
        })
        print(f"  {school[:45]:<45}  {len(school_rows)} IPs  H={h} M={m} L={l}  best={best['ip_address']} (score={best['score']})")

    return summary_rows


def write_summary(summary_rows, output_file):
    fieldnames = ["school_name", "total_ips", "high", "medium", "low",
                  "best_score", "best_ip", "best_hostname"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Summary written to {output_file}")


def run(input_file=INPUT_FILE, phase4_file=PHASE4_FILE,
        schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE,
        output_file_p4=OUTPUT_FILE_P4):

    with open(schools_file, newline="", encoding="utf-8") as f:
        all_schools = [r["school_name"].strip() for r in csv.DictReader(f)]

    with open(input_file, newline="", encoding="utf-8") as f:
        phase3_rows = list(csv.DictReader(f))

    write_summary(summarize(phase3_rows, all_schools, "Phase 3: Before RIPE Atlas Validation"), output_file)

    if os.path.exists(phase4_file):
        with open(phase4_file, newline="", encoding="utf-8") as f:
            phase4_rows = list(csv.DictReader(f))
        validated_rows = [r for r in phase4_rows if r.get("ripe_validated") == "yes"]
        removed = sum(1 for r in phase4_rows if r.get("ripe_validated") == "no")
        skipped = sum(1 for r in phase4_rows if r.get("ripe_validated") == "skipped")
        print(f"\nRIPE Atlas: removed {removed} IPs, {skipped} skipped (no probes)")
        write_summary(summarize(validated_rows, all_schools, "Phase 4: After RIPE Atlas Validation"), output_file_p4)
    else:
        print(f"\nNote: {phase4_file} not found, skipping phase 4 analysis.")


if __name__ == "__main__":
    run()
