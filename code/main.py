"""
main.py — runs the full school IP identification pipeline for all radii.

Config:
  RADII            — list of distance thresholds (km) to run
  SCHOOLS_FILE     — all NY K-12 schools (13k) used for phase 1 + 2
  FORCE_RERUN_FROM — re-run all phases >= this number, even if output exists
                     0 = full re-run including ARIN discovery
                     1 = re-run from phase 1 onwards (keeps ARIN cached)
                     2 = re-run from phase 2 onwards (keeps phase 1 cached)
                     3 = re-run from phase 3 onwards (keeps phase 1+2 cached)
                     None = skip any phase whose output file already exists
  TEST_CAP         — only process this many schools in phase 2, then stop.
                     Use this to estimate runtime before committing to the full run.
                     Set to None for a real run.

Pipeline:
  Phase 0 — ARIN discovery         → phase0_arin.csv          (runs once, no radius)
  Phase 1 — GeoLite2 geolocation   → phase1_candidates_{R}km.csv
            (merge phase 0 + 1)    → phase_candidates_{R}km.csv
  Phase 2 — Reverse DNS lookup     → phase2_filtered_{R}km.csv
  Phase 3 — WHOIS/ASN confirmation → phase3_confirmed_{R}km.csv
  Phase 4 — RIPE Atlas validation  → phase4_validated_{R}km.csv
  Analysis                         → analysis_summary_{R}km.csv
  Verification                     → verification_results.csv
  Probe coverage                   → probe_coverage.csv
"""

import csv
import os
import shutil

import phase0_arin             as phase0
import phase1_geo_lookup       as phase1
import phase2_dns_lookup       as phase2
import phase3_confirm          as phase3
import phase4_ripe_atlas       as phase4
import analysis
import verify_high_confidence  as verify
import check_probe_coverage    as probe_check

# ── configuration ────────────────────────────────────────────────────────────

RADII            = [10, 20]                               # km thresholds to run
SCHOOLS_FILE     = "data/inputs/gigamaps_schools_ny.csv"  # all 13k NY schools
FORCE_RERUN_FROM = 1                                      # full re-run from phase 1
                                                          # set to None to skip existing files
TEST_CAP         = None                                   # set to e.g. 500 to test runtime

PHASE0_FILE      = "data/outputs/phase0_arin.csv"         # ARIN results (radius-independent)

# ── helpers ──────────────────────────────────────────────────────────────────

def paths(radius):
    s = f"_{radius}km"
    return {
        "phase1":      f"data/outputs/phase1_candidates{s}.csv",
        "candidates":  f"data/outputs/phase_candidates{s}.csv",   # phase0 + phase1 merged
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


def merge_candidates(arin_file, geo_file, output_file):
    """
    Merge ARIN-discovered blocks (Phase 0) and GeoLite2 blocks (Phase 1)
    into a single candidates file for Phase 2.
    Deduplicates by (cidr, school_name) pair.
    """
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

    n_arin = sum(1 for r in rows if any(
        row["cidr"] == r["cidr"] and row["school_name"] == r["school_name"]
        for row in (csv.DictReader(open(arin_file)) if os.path.exists(arin_file) else [])
    )) if os.path.exists(arin_file) else 0

    print(f"Merged candidates: {len(rows)} total blocks → {output_file}")


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

    # Phase 0 — ARIN discovery (runs once, not per-radius)
    # This is the professor's primary method: org name → registered IP blocks
    if should_run(0, PHASE0_FILE):
        header("Phase 0: ARIN WHOIS Discovery")
        phase0.run(output_file=PHASE0_FILE)
    else:
        print(f"Phase 0: skipping — {PHASE0_FILE} already exists")

    for radius in RADII:
        header(f"PIPELINE — {radius}km RADIUS")
        f = paths(radius)

        # Phase 1 — geo lookup
        if should_run(1, f["phase1"]):
            header(f"Phase 1: Geo Lookup ({radius}km)")
            phase1.run(radius_km=radius, schools_file=SCHOOLS_FILE, output_file=f["phase1"])
        else:
            print(f"Phase 1: skipping — {f['phase1']} already exists")

        # Merge Phase 0 (ARIN) + Phase 1 (GeoLite2) into combined candidates
        header("Merging ARIN + GeoLite2 candidates")
        merge_candidates(PHASE0_FILE, f["phase1"], f["candidates"])

        # Phase 2 — reverse DNS on merged candidates
        # force_fresh clears the checkpoint when doing a forced re-run
        if should_run(2, f["phase2"]):
            header("Phase 2: Reverse DNS Lookup")
            force_fresh = FORCE_RERUN_FROM is not None and FORCE_RERUN_FROM <= 2
            phase2.run(
                input_file  = f["candidates"],
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
            input_file     = f["phase3"],
            phase4_file    = f["phase4"],
            schools_file   = SCHOOLS_FILE,
            output_file    = f["analysis"],
            output_file_p4 = f["analysis_p4"],
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
    print(f"\n  ARIN blocks  : {PHASE0_FILE}")
    print(f"  Verification : data/outputs/verification_results.csv")
