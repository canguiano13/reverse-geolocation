"""
Run the full school IP identification pipeline.

Phases:
  0   ARIN WHOIS discovery     (runs once, no radius)
  1   GeoLite2 geolocation     (per radius)
  2   Reverse DNS lookup       (per radius)
  3   WHOIS/ASN confirmation   (per radius)
  3b  Fix district attribution (per radius)
  4   RIPE Atlas validation    (per radius)

FORCE_RERUN_FROM: re-run from this phase onward. None = skip phases whose
output already exists.
"""

import csv
import os
import shutil

import phase0_arin              as phase0
import phase1_geo_lookup        as phase1
import phase2_dns_lookup        as phase2
import phase3_confirm           as phase3
import phase4_ripe_atlas        as phase4
import phase3b_fix_attribution  as fix_attribution
import post1_analysis           as analysis
import post2_combined_summary   as combined_summary
import post3_verify             as verify
import post4_recall_estimate    as recall_estimate
import post5_probe_coverage     as probe_check
import post6_filter_stats       as filter_stats
import post7_url_verify         as url_verify
import setup2_fcc_blocks        as fcc_blocks
import setup3_fcc_providers     as fcc_providers

RADII            = [5, 10, 20, 30]
SCHOOLS_FILE     = "data/inputs/schools_selected.csv"   # 192 schools, statewide NY grid sample
# SCHOOLS_FILE   = "data/inputs/metro_schools_nyc.csv"  # 5,886 schools, NYC metro only
# SCHOOLS_FILE   = "data/inputs/gigamaps_schools_ny.csv"# 13,143 schools, full NY (slow)
# SCHOOLS_FILE   = "data/inputs/targeted_schools.csv"   # small hand-curated debug list
FORCE_RERUN_FROM = None

PHASE0_FILE         = "data/outputs/phase0_arin.csv"
SCHOOL_PROVIDERS    = "data/inputs/school_providers.csv"


def paths(radius):
    s = f"_{radius}km"
    return {
        "phase1":        f"data/outputs/phase1_candidates{s}.csv",
        "candidates":    f"data/outputs/phase_candidates{s}.csv",
        "phase2":        f"data/outputs/phase2_filtered{s}.csv",
        "phase3":        f"data/outputs/phase3_confirmed{s}.csv",
        "phase3_reattr": f"data/outputs/phase3_reattributed{s}.csv",
        "phase4":        f"data/outputs/phase4_validated{s}.csv",
        "analysis":      f"data/outputs/analysis_summary{s}.csv",
        "analysis_p4":   f"data/outputs/analysis_summary_phase4{s}.csv",
    }


def should_run(phase_num, output_path):
    if FORCE_RERUN_FROM is not None and phase_num >= FORCE_RERUN_FROM:
        return True
    return not os.path.exists(output_path)


def merge_candidates(arin_file, geo_file, output_file):
    """Combine ARIN (phase 0) and GeoLite2 (phase 1) blocks, deduplicated."""
    rows = []
    seen = set()
    for filepath in [arin_file, geo_file]:
        if not os.path.exists(filepath):
            continue
        with open(filepath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["cidr"].strip(), row["school_name"].strip())
                if key not in seen:
                    seen.add(key)
                    rows.append({"cidr": row["cidr"], "school_name": row["school_name"]})
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cidr", "school_name"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Merged candidates: {len(rows)} total blocks -> {output_file}")


if __name__ == "__main__":

    # One-time migration: copy old no-suffix phase1 file to _10km
    bare   = "data/outputs/phase1_candidates.csv"
    target = "data/outputs/phase1_candidates_10km.csv"
    if not os.path.exists(target) and os.path.exists(bare):
        shutil.copy(bare, target)
        print(f"Migrated {bare} -> {target}")

    # One-time setup: generate school_providers.csv (FCC data) if missing
    if not os.path.exists(SCHOOL_PROVIDERS):
        print("\n=== Setup: FCC census blocks ===")
        fcc_blocks.run(input_file=SCHOOLS_FILE)
        print("\n=== Setup: FCC providers ===")
        fcc_providers.run()
    else:
        print(f"Setup: skipping, {SCHOOL_PROVIDERS} already exists")

    if should_run(0, PHASE0_FILE):
        print("\n=== Phase 0: ARIN WHOIS Discovery ===")
        phase0.run(output_file=PHASE0_FILE)
    else:
        print(f"Phase 0: skipping, {PHASE0_FILE} already exists")

    for radius in RADII:
        print(f"\n{'='*60}\n  PIPELINE: {radius}km RADIUS\n{'='*60}")
        f = paths(radius)

        if should_run(1, f["phase1"]):
            print(f"\n=== Phase 1: Geo Lookup ({radius}km) ===")
            phase1.run(radius_km=radius, schools_file=SCHOOLS_FILE, output_file=f["phase1"])
        else:
            print(f"Phase 1: skipping, {f['phase1']} already exists")

        merge_candidates(PHASE0_FILE, f["phase1"], f["candidates"])

        if should_run(2, f["phase2"]):
            print("\n=== Phase 2: Reverse DNS Lookup ===")
            force_fresh = FORCE_RERUN_FROM is not None and FORCE_RERUN_FROM <= 2
            phase2.run(
                input_file  = f["candidates"],
                output_file = f["phase2"],
                force_fresh = force_fresh,
            )
        else:
            print(f"Phase 2: skipping, {f['phase2']} already exists")

        if should_run(3, f["phase3"]):
            print("\n=== Phase 3: WHOIS/ASN Confirmation ===")
            phase3.run(input_file=f["phase2"], output_file=f["phase3"])
        else:
            print(f"Phase 3: skipping, {f['phase3']} already exists")

        print("\n=== Phase 3b: Fix Attribution ===")
        fix_attribution.run(
            input_file   = f["phase3"],
            schools_file = "data/inputs/gigamaps_schools_ny.csv",
            output_file  = f["phase3_reattr"],
        )

        if should_run(4, f["phase4"]):
            print("\n=== Phase 4: RIPE Atlas Validation ===")
            phase4.run(
                input_file   = f["phase3_reattr"],
                schools_file = "data/inputs/gigamaps_schools_ny.csv",
                output_file  = f["phase4"],
            )
        else:
            print(f"Phase 4: skipping, {f['phase4']} already exists")

        print("\n=== Analysis ===")
        analysis.run(
            input_file     = f["phase3_reattr"],
            phase4_file    = f["phase4"],
            schools_file   = SCHOOLS_FILE,
            output_file    = f["analysis"],
            output_file_p4 = f["analysis_p4"],
        )

        print(f"\n=== Combined Summary ({radius}km) ===")
        combined_summary.run(
            phase0_file  = PHASE0_FILE,
            phase3_file  = f["phase3_reattr"],
            phase4_file  = f["phase4"],
            output_file  = f"data/outputs/combined_results_{radius}km.csv",
        )

    print("\n=== Manual Verification (ARIN RDAP) ===")
    verify_files = {f"{r}km": paths(r)["phase3"] for r in RADII}
    verify.run(files=verify_files, output_file="data/outputs/verification_results.csv")

    print("\n=== RIPE Atlas Probe Coverage ===")
    probe_check.run()

    print("\n=== Filter Impact Stats ===")
    filter_stats.run()

    print("\n=== Recall Estimate ===")
    recall_estimate.run()

    print("\n=== URL Forward DNS Verification ===")
    url_verify.run()

    print("\n=== ALL DONE ===")
    for radius in RADII:
        f = paths(radius)
        print(f"\n  {radius}km:")
        print(f"    Phase 3 (fixed)  : {f['phase3_reattr']}")
        print(f"    Phase 4          : {f['phase4']}")
        print(f"    Combined summary : data/outputs/combined_results_{radius}km.csv")
    print(f"\n  ARIN blocks  : {PHASE0_FILE}")
    print(f"  Verification : data/outputs/verification_results.csv")
    print(f"  Recall       : data/outputs/recall_estimate.csv")
    print(f"  URL verify   : data/outputs/url_verification.csv")
