"""
main.py — runs the full school IP identification pipeline for all radii.

Config:
  RADII            — list of distance thresholds (km) to run
  SCHOOLS_FILE     — all NY K-12 schools (13k) used for phase 1 + 2
  FORCE_RERUN_FROM — re-run all phases >= this number, even if output exists
                     1 = full re-run from scratch
                     2 = re-run from phase 2 onwards (keeps phase 1 cached)
                     3 = re-run from phase 3 onwards (keeps phase 1+2 cached)
                     None = skip any phase whose output file already exists
  TEST_CAP         — only process this many schools in phase 2, then stop.
                     Use this to estimate runtime before committing to the full run.
                     Set to None for a real run.

Pipeline:
  Phase 1 — GeoLite2 geolocation   → phase1_candidates_{R}km.csv
  Phase 2 — Reverse DNS lookup     → phase2_filtered_{R}km.csv
  Phase 3 — WHOIS/ASN confirmation → phase3_confirmed_{R}km.csv
  Phase 4 — RIPE Atlas validation  → phase4_validated_{R}km.csv
  Analysis                         → analysis_summary_{R}km.csv
                                     analysis_summary_phase4_{R}km.csv
  Verification                     → verification_results.csv
  Probe coverage                   → probe_coverage.csv
"""

import os
import shutil

import phase1_geo_lookup      as phase1
import phase2_dns_lookup      as phase2
import phase3_confirm         as phase3
import phase4_ripe_atlas      as phase4
import analysis
import verify_high_confidence as verify
import check_probe_coverage   as probe_check

# ── configuration ────────────────────────────────────────────────────────────

RADII            = [10, 20]                            # km thresholds to run
SCHOOLS_FILE     = "data/inputs/gigamaps_schools_ny.csv"  # all 13k NY schools
FORCE_RERUN_FROM = 2                                   # re-run from phase 2 onwards
                                                       # set to None to skip existing files
TEST_CAP         = 500                                 # set to None for full run
                                                       # 500 lets you time phase 2 and estimate total

# ── helpers ──────────────────────────────────────────────────────────────────

def paths(radius):
    s = f"_{radius}km"
    return {
        "phase1":      f"data/outputs/phase1_candidates{s}.csv",
        "phase2":      f"data/outputs/phase2_filtered{s}.csv",
        "phase3":      f"data/outputs/phase3_confirmed{s}.csv",
        "phase4":      f"data/outputs/phase4_validated{s}.csv",
        "analysis":    f"data/outputs/analysis_summary{s}.csv",
        "analysis_p4": f"data/outputs/analysis_summary_phase4{s}.csv",
    }


def should_run(phase_num, output_file):
    """Return True if this phase should run."""
    if FORCE_RERUN_FROM is not None and phase_num >= FORCE_RERUN_FROM:
        return True
    return not os.path.exists(output_file)


def header(msg):
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}\n")


# ── migration: rename bare phase1 file if needed ────────────────────────────

def migrate_existing_phase1():
    """
    If we previously ran phase1 and saved it as phase1_candidates.csv
    (without a radius suffix), copy it to phase1_candidates_10km.csv
    so main.py can find it and skip re-running phase 1.
    """
    bare   = "data/outputs/phase1_candidates.csv"
    target = "data/outputs/phase1_candidates_10km.csv"
    if not os.path.exists(target) and os.path.exists(bare):
        shutil.copy(bare, target)
        print(f"Migrated {bare} → {target}")


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    migrate_existing_phase1()

    for radius in RADII:
        header(f"PIPELINE — {radius}km RADIUS")
        f = paths(radius)

        # Phase 1 — geo lookup
        if should_run(1, f["phase1"]):
            header(f"Phase 1: Geo Lookup ({radius}km)")
            phase1.run(radius_km=radius, schools_file=SCHOOLS_FILE, output_file=f["phase1"])
        else:
            print(f"Phase 1: skipping — {f['phase1']} already exists")

        # Phase 2 — reverse DNS
        # force_fresh=True clears the checkpoint and starts over when FORCE_RERUN_FROM <= 2
        # force_fresh=False resumes from checkpoint if the run was previously interrupted
        if should_run(2, f["phase2"]):
            header("Phase 2: Reverse DNS Lookup")
            force_fresh = FORCE_RERUN_FROM is not None and FORCE_RERUN_FROM <= 2
            phase2.run(
                input_file  = f["phase1"],
                output_file = f["phase2"],
                test_cap    = TEST_CAP,
                force_fresh = force_fresh,
            )
        else:
            print(f"Phase 2: skipping — {f['phase2']} already exists")

        # Phase 3 — WHOIS/ASN confirmation
        if should_run(3, f["phase3"]):
            header("Phase 3: WHOIS/ASN Confirmation")
            phase3.run(input_file=f["phase2"], output_file=f["phase3"])
        else:
            print(f"Phase 3: skipping — {f['phase3']} already exists")

        # Phase 4 — RIPE Atlas validation
        if should_run(4, f["phase4"]):
            header("Phase 4: RIPE Atlas Validation")
            phase4.run(input_file=f["phase3"], schools_file=SCHOOLS_FILE, output_file=f["phase4"])
        else:
            print(f"Phase 4: skipping — {f['phase4']} already exists")

        # Analysis
        header("Analysis")
        analysis.run(
            input_file    = f["phase3"],
            phase4_file   = f["phase4"],
            schools_file  = SCHOOLS_FILE,
            output_file   = f["analysis"],
            output_file_p4= f["analysis_p4"],
        )

    # Verification — runs across all radii at the end
    header("Manual Verification (ARIN RDAP)")
    verify_files = {f"{r}km": paths(r)["phase3"] for r in RADII}
    verify.run(files=verify_files, output_file="data/outputs/verification_results.csv")

    # Probe coverage — shows how many schools have RIPE Atlas probes nearby
    header("RIPE Atlas Probe Coverage")
    probe_check.run()

    header("ALL DONE")
    for radius in RADII:
        f = paths(radius)
        print(f"\n  {radius}km results:")
        print(f"    Phase 3 : {f['phase3']}")
        print(f"    Phase 4 : {f['phase4']}")
        print(f"    Analysis: {f['analysis']}")
    print(f"\n  Verification: data/outputs/verification_results.csv")
