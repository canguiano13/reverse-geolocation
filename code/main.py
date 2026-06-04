"""Run the full school IP identification pipeline."""

import csv
import gzip
import ipaddress
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
import post8_abandoned_cidrs    as abandoned_cidrs
import post9_radius_sensitivity as radius_sensitivity
import post10_recall_vs_arin    as recall_vs_arin
import post11_pipeline_stats as pipeline_stats
import setup2_fcc_blocks        as fcc_blocks
import setup3_fcc_providers     as fcc_providers

RADII = [20]  # single run; post9_radius_sensitivity filters by distance_km for 5/10km
SCHOOLS_FILE = "data/inputs/schools_selected.csv"
# SCHOOLS_FILE = "data/inputs/metro_schools_nyc.csv"
# SCHOOLS_FILE = "data/inputs/gigamaps_schools_ny.csv"
# SCHOOLS_FILE = "data/inputs/targeted_schools.csv"
FORCE_RERUN_FROM = None

PHASE0_FILE      = "data/outputs/phase0_arin.csv"
SCHOOL_PROVIDERS = "data/inputs/school_providers.csv"
IPINFO_ASN_FILE  = "data/inputs/ipinfo/ipinfo_asn.csv.gz"


def load_hosting_set(asn_file):
    """Build a set of /24 network address ints classified as hosting by IPinfo."""
    hosting = set()
    try:
        with gzip.open(asn_file, 'rt', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if row.get('type') != 'hosting':
                    continue
                try:
                    net = ipaddress.ip_network(row['network'], strict=False)
                except ValueError:
                    continue
                if net.version != 4 or net.prefixlen < 16:
                    continue  # skip overly broad prefixes; caught in Phase 3 if missed
                if net.prefixlen <= 24:
                    for subnet in net.subnets(new_prefix=24):
                        hosting.add(int(subnet.network_address))
                else:
                    parent = ipaddress.ip_network(
                        f"{net.network_address}/24", strict=False)
                    hosting.add(int(parent.network_address))
        print(f"Hosting pre-filter: {len(hosting)} /24 blocks flagged as hosting")
    except FileNotFoundError:
        print(f"Warning: {asn_file} not found, hosting pre-filter disabled")
    return hosting


def filter_hosting_cidrs(candidates_file, hosting_set):
    """Remove hosting-type CIDRs from candidates file in-place."""
    if not hosting_set:
        return
    with open(candidates_file, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    filtered = []
    n_removed = 0
    for row in rows:
        try:
            net = ipaddress.ip_network(row['cidr'].strip(), strict=False)
            key = int(net.network_address)
        except ValueError:
            filtered.append(row)
            continue
        if key in hosting_set:
            n_removed += 1
        else:
            filtered.append(row)
    with open(candidates_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['cidr', 'school_name', 'distance_km'])
        writer.writeheader()
        writer.writerows(filtered)
    print(f"Hosting pre-filter: removed {n_removed}/{len(rows)} CIDRs, "
          f"{len(filtered)} remain")


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
    """Combine ARIN (phase 0) and geo (phase 1) blocks, deduplicated.

    ARIN blocks have no geo distance; distance_km is left empty for them.
    Phase 1 blocks carry their distance_km from the geo scan.
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
                    rows.append({
                        "cidr":        row["cidr"],
                        "school_name": row["school_name"],
                        "distance_km": row.get("distance_km", ""),
                    })
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cidr", "school_name", "distance_km"])
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

    if not os.path.exists(SCHOOL_PROVIDERS):
        print("\n=== Setup: FCC census blocks ===")
        fcc_blocks.run(input_file=SCHOOLS_FILE)
        print("\n=== Setup: FCC providers ===")
        fcc_providers.run()
    else:
        print(f"Setup: skipping, {SCHOOL_PROVIDERS} already exists")

    print("\n=== Loading IPinfo hosting pre-filter ===")
    hosting_set = load_hosting_set(IPINFO_ASN_FILE)

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
        filter_hosting_cidrs(f["candidates"], hosting_set)

        if should_run(2, f["phase2"]):
            print("\n=== Phase 2: Reverse DNS Lookup ===")
            force_fresh = FORCE_RERUN_FROM is not None and FORCE_RERUN_FROM <= 2
            phase2.run(
                input_file=f["candidates"],
                output_file=f["phase2"],
                force_fresh=force_fresh,
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
            input_file=f["phase3"],
            schools_file="data/inputs/gigamaps_schools_ny.csv",
            output_file=f["phase3_reattr"],
        )

        if should_run(4, f["phase4"]):
            print("\n=== Phase 4: RIPE Atlas Validation ===")
            phase4.run(
                input_file=f["phase3_reattr"],
                schools_file="data/inputs/gigamaps_schools_ny.csv",
                output_file=f["phase4"],
            )
        else:
            print(f"Phase 4: skipping, {f['phase4']} already exists")

        print("\n=== Analysis ===")
        analysis.run(
            input_file=f["phase3_reattr"],
            phase4_file=f["phase4"],
            schools_file=SCHOOLS_FILE,
            output_file=f["analysis"],
            output_file_p4=f["analysis_p4"],
        )

        print(f"\n=== Combined Summary ({radius}km) ===")
        combined_summary.run(
            phase0_file=PHASE0_FILE,
            phase3_file=f["phase3_reattr"],
            phase4_file=f["phase4"],
            output_file=f"data/outputs/combined_results_{radius}km.csv",
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

    print("\n=== Abandoned CIDRs (Phase 2 timeout/reject transparency) ===")
    abandoned_cidrs.run()

    print("\n=== Radius Sensitivity Comparison ===")
    radius_sensitivity.run()

    print("\n=== RIG Recall vs ARIN Ground Truth ===")
    recall_vs_arin.run()

    print("\n=== Pipeline Stats (Waldo comparison) ===")
    pipeline_stats.run()

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
