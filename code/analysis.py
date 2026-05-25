import csv
import os
from collections import defaultdict

INPUT_FILE       = "data/phase3_confirmed.csv"
PHASE4_FILE      = "data/phase4_validated.csv"
SCHOOLS_FILE     = "data/schools_selected.csv"
OUTPUT_FILE      = "data/analysis_summary.csv"
OUTPUT_FILE_P4   = "data/analysis_summary_phase4.csv"


def summarize(rows, all_schools, label):
    by_school = defaultdict(list)
    for row in rows:
        by_school[row["school_name"].strip()].append(row)

    total_ips  = len(rows)
    high       = sum(1 for r in rows if r["confidence"] == "high")
    medium     = sum(1 for r in rows if r["confidence"] == "medium")
    low        = sum(1 for r in rows if r["confidence"] == "low")
    no_results = [s for s in all_schools if s not in by_school]

    print("=" * 50)
    print(f"  {label}")
    print("=" * 50)
    print(f"  Schools processed   : {len(all_schools)}")
    print(f"  Schools with results: {len(by_school)}")
    print(f"  Schools no results  : {len(no_results)}")
    print(f"  Total candidate IPs : {total_ips}")
    print(f"  High confidence     : {high}")
    print(f"  Medium confidence   : {medium}")
    print(f"  Low confidence      : {low}")
    print("=" * 50)

    if no_results:
        print("\nSchools with no IPs identified:")
        for s in no_results:
            print(f"  {s}")

    print("\nPer-school breakdown:")
    summary_rows = []
    for school in all_schools:
        school_rows = by_school.get(school, [])
        if not school_rows:
            summary_rows.append({
                "school_name":   school,
                "total_ips":     0,
                "high":          0,
                "medium":        0,
                "low":           0,
                "best_score":    0,
                "best_ip":       "",
                "best_hostname": "",
            })
            print(f"  {school[:45]:<45}  no results")
            continue

        h = sum(1 for r in school_rows if r["confidence"] == "high")
        m = sum(1 for r in school_rows if r["confidence"] == "medium")
        l = sum(1 for r in school_rows if r["confidence"] == "low")
        best = max(school_rows, key=lambda r: int(r["score"]))

        summary_rows.append({
            "school_name":   school,
            "total_ips":     len(school_rows),
            "high":          h,
            "medium":        m,
            "low":           l,
            "best_score":    best["score"],
            "best_ip":       best["ip_address"],
            "best_hostname": best["hostname"],
        })

        print(f"  {school[:45]:<45}  {len(school_rows)} IPs  high={h} mid={m} low={l}  best={best['ip_address']} (score={best['score']})")

    return summary_rows


def write_summary(summary_rows, output_file):
    fieldnames = ["school_name", "total_ips", "high", "medium", "low", "best_score", "best_ip", "best_hostname"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\nSummary written to {output_file}")


if __name__ == "__main__":
    with open(SCHOOLS_FILE, newline="", encoding="utf-8") as f:
        all_schools = [r["school_name"].strip() for r in csv.DictReader(f)]

    # phase 3 analysis
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        phase3_rows = list(csv.DictReader(f))

    summary = summarize(phase3_rows, all_schools, "Phase 3 — Before RIPE Atlas Validation")
    write_summary(summary, OUTPUT_FILE)

    # phase 4 analysis (only if the file exists)
    if os.path.exists(PHASE4_FILE):
        print("\n")
        with open(PHASE4_FILE, newline="", encoding="utf-8") as f:
            phase4_rows = list(csv.DictReader(f))

        # only count IPs that passed RIPE Atlas validation
        validated_rows = [r for r in phase4_rows if r.get("ripe_validated") == "yes"]
        removed = sum(1 for r in phase4_rows if r.get("ripe_validated") == "no")
        skipped = sum(1 for r in phase4_rows if r.get("ripe_validated") == "skipped")

        print(f"RIPE Atlas removed {removed} IPs  |  {skipped} skipped (no probes available)")

        summary_p4 = summarize(validated_rows, all_schools, "Phase 4 — After RIPE Atlas Validation")
        write_summary(summary_p4, OUTPUT_FILE_P4)
    else:
        print(f"\nNote: {PHASE4_FILE} not found — skipping phase 4 analysis.")
        print("Run phase4_ripe_atlas.py first to generate it.")
