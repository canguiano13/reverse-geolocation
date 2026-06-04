"""post11_pipeline_stats.py

Generates three metrics that mirror the Waldo paper's key tables/figures:

  1. Stage-by-stage /24 reduction table  (paper Table 4)
  2. FCC provider match breakdown        (paper Table 5)
  3. ISI hitlist reduction %             (paper §4.2.3 -- 34.9% benchmark)

No pipeline reruns needed -- reads existing CSV outputs.
Run after Phase 3 completes (Phase 4 optional, adds one row to table 1).
"""

import csv
import ipaddress
import os
from collections import defaultdict

RADIUS = 20   # change to match whichever radius you ran

# ── Input files ───────────────────────────────────────────────────────────
CANDIDATES_FILE  = f"data/outputs/phase_candidates_{RADIUS}km.csv"
PHASE2_FILE      = f"data/outputs/phase2_filtered_{RADIUS}km.csv"
PHASE3_FILE      = f"data/outputs/phase3_confirmed_{RADIUS}km.csv"
PHASE3B_FILE     = f"data/outputs/phase3_reattributed_{RADIUS}km.csv"
PHASE4_FILE      = f"data/outputs/phase4_validated_{RADIUS}km.csv"

OUT_REDUCTION    = f"data/outputs/post11_stage_reduction_{RADIUS}km.csv"
OUT_PROVIDER     = f"data/outputs/post11_provider_match_{RADIUS}km.csv"
OUT_HITLIST      = f"data/outputs/post11_hitlist_reduction_{RADIUS}km.csv"


# ── Helpers ───────────────────────────────────────────────────────────────

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def net24(cidr_or_ip, is_ip=False):
    """Return the /24 network string for a CIDR or IP address."""
    try:
        if is_ip:
            return str(ipaddress.IPv4Network(f"{cidr_or_ip}/24", strict=False))
        net = ipaddress.ip_network(cidr_or_ip.strip(), strict=False)
        return str(ipaddress.IPv4Network(f"{net.network_address}/24", strict=False))
    except Exception:
        return None


def unique_24s(rows, field, is_ip=False):
    s = set()
    for r in rows:
        v = r.get(field, "").strip()
        if v:
            n = net24(v, is_ip=is_ip)
            if n:
                s.add(n)
    return s


def unique_schools(rows, field="school_name"):
    return {r.get(field, "").strip() for r in rows if r.get(field, "").strip()}


def median_asns(rows):
    """Median unique ASNs per school (Phase 3+ only, has 'asn' column)."""
    per_school = defaultdict(set)
    for r in rows:
        school = r.get("school_name", "").strip()
        asn    = r.get("asn", "").strip()
        if school and asn:
            per_school[school].add(asn)
    if not per_school:
        return "n/a"
    counts = sorted(len(v) for v in per_school.values())
    mid = len(counts) // 2
    return counts[mid] if len(counts) % 2 else (counts[mid-1] + counts[mid]) / 2


# ── 1. Stage-by-stage /24 reduction ──────────────────────────────────────

def stage_reduction():
    print("\n=== 1. Stage-by-stage /24 reduction (mirrors paper Table 4) ===")

    stages = [
        ("Candidates (Phase 0+1)",     CANDIDATES_FILE,  "cidr",       False),
        ("After Phase 2 (DNS filter)", PHASE2_FILE,      "ip_address", True),
        ("After Phase 3 (ASN confirm)",PHASE3_FILE,      "ip_address", True),
        ("After Phase 3b (reattr.)",   PHASE3B_FILE,     "ip_address", True),
        ("After Phase 4 (Atlas valid)",PHASE4_FILE,      "ip_address", True),
    ]

    rows_out = []
    prev_24s = None

    print(f"\n{'Stage':<35} {'Unique /24s':>12} {'Schools':>9} {'Med. ASNs':>11} {'Reduction':>11}")
    print("-" * 83)

    for label, path, field, is_ip in stages:
        rows = read_csv(path)
        if not rows:
            print(f"  {label:<33} {'(file missing)':>12}")
            continue

        u24   = unique_24s(rows, field, is_ip=is_ip)
        schs  = unique_schools(rows)
        masn  = median_asns(rows) if is_ip else "n/a"
        reduc = f"{(1 - len(u24)/len(prev_24s))*100:.1f}%" if prev_24s else "baseline"

        print(f"  {label:<33} {len(u24):>12,} {len(schs):>9} {str(masn):>11} {reduc:>11}")

        rows_out.append({
            "stage":        label,
            "unique_24s":   len(u24),
            "n_schools":    len(schs),
            "median_asns":  masn,
            "reduction_pct": reduc,
        })
        prev_24s = u24

    with open(OUT_REDUCTION, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["stage","unique_24s","n_schools",
                                          "median_asns","reduction_pct"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"\nWritten -> {OUT_REDUCTION}")
    return rows_out


# ── 2. FCC provider match breakdown ──────────────────────────────────────

def provider_match():
    print("\n=== 2. FCC provider match breakdown (mirrors paper Table 5) ===")

    rows = read_csv(PHASE3_FILE)
    if not rows:
        print(f"  Missing: {PHASE3_FILE}")
        return

    total      = len(rows)
    commercial = sum(1 for r in rows if r.get("fcc_match") == "yes")
    edu_asn    = sum(1 for r in rows
                     if r.get("fcc_match") != "yes"
                     and r.get("whois_match") == "yes")
    no_match   = total - commercial - edu_asn

    # School-level (at least one IP matched)
    sch_commercial = {r["school_name"] for r in rows if r.get("fcc_match") == "yes"}
    sch_edu        = {r["school_name"] for r in rows
                      if r.get("fcc_match") != "yes" and r.get("whois_match") == "yes"}
    sch_none       = {r["school_name"] for r in rows} - sch_commercial - sch_edu
    n_sch          = len({r["school_name"] for r in rows})

    print(f"\n  {'Category':<30} {'IPs':>8} {'% IPs':>7}   {'Schools':>8} {'% Schools':>10}")
    print("  " + "-" * 68)
    rows_out = []
    for label, ip_n, sch_set in [
        ("Commercial FCC match",    commercial, sch_commercial),
        ("Non-commercial (edu/gov)", edu_asn,   sch_edu),
        ("No match",                no_match,   sch_none),
    ]:
        ip_pct  = f"{ip_n/total*100:.1f}%" if total else "0%"
        sch_pct = f"{len(sch_set)/n_sch*100:.1f}%" if n_sch else "0%"
        print(f"  {label:<30} {ip_n:>8,} {ip_pct:>7}   {len(sch_set):>8} {sch_pct:>10}")
        rows_out.append({"category": label, "ips": ip_n, "ip_pct": ip_pct,
                          "schools": len(sch_set), "school_pct": sch_pct})

    print(f"  {'Total':<30} {total:>8,} {'100%':>7}   {n_sch:>8} {'100%':>10}")

    # Confidence breakdown
    high   = sum(1 for r in rows if r.get("confidence") == "high")
    medium = sum(1 for r in rows if r.get("confidence") == "medium")
    low    = sum(1 for r in rows if r.get("confidence") == "low")
    print(f"\n  Confidence:  high={high:,}  medium={medium:,}  low={low:,}")
    rows_out.append({"category": "-- high confidence",   "ips": high,   "ip_pct": "", "schools": "", "school_pct": ""})
    rows_out.append({"category": "-- medium confidence", "ips": medium, "ip_pct": "", "schools": "", "school_pct": ""})
    rows_out.append({"category": "-- low confidence",    "ips": low,    "ip_pct": "", "schools": "", "school_pct": ""})

    with open(OUT_PROVIDER, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["category","ips","ip_pct","schools","school_pct"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"\nWritten -> {OUT_PROVIDER}")


# ── 3. ISI hitlist reduction ──────────────────────────────────────────────

def hitlist_reduction():
    print("\n=== 3. ISI hitlist reduction (paper benchmark: 34.9%) ===")

    pre  = read_csv(CANDIDATES_FILE)
    post = read_csv(PHASE2_FILE)

    if not pre:
        print(f"  Missing: {CANDIDATES_FILE}")
        return
    if not post:
        print(f"  Missing: {PHASE2_FILE} -- run after Phase 2 completes")
        return

    # /24s in candidates (pre-hitlist pre-filter)
    pre_24s  = unique_24s(pre,  "cidr",       is_ip=False)
    # /24s that produced at least one PTR match (survived Phase 2)
    post_24s = unique_24s(post, "ip_address", is_ip=True)

    removed    = pre_24s - post_24s
    removed_n  = len(removed)
    pct        = removed_n / len(pre_24s) * 100 if pre_24s else 0

    print(f"\n  Candidate /24s before Phase 2 : {len(pre_24s):>10,}")
    print(f"  /24s with PTR match after Ph2  : {len(post_24s):>10,}")
    print(f"  Removed (dead + no PTR match)  : {removed_n:>10,}  ({pct:.1f}%)")
    print(f"\n  Paper benchmark (ISI filter only): 34.9%")
    print(f"  Our reduction (hitlist + DNS)    : {pct:.1f}%")
    print(f"  Note: our number includes both hitlist dead-skip AND failed PTR probes,")
    print(f"        so it will be higher than the paper's hitlist-only figure.")

    # Per-school median reduction
    pre_per_school  = defaultdict(set)
    post_per_school = defaultdict(set)
    for r in pre:
        s = r.get("school_name", "").strip()
        n = net24(r.get("cidr", ""), is_ip=False)
        if s and n:
            pre_per_school[s].add(n)
    for r in post:
        s = r.get("school_name", "").strip()
        n = net24(r.get("ip_address", ""), is_ip=True)
        if s and n:
            post_per_school[s].add(n)

    reductions = []
    for school, pre_set in pre_per_school.items():
        post_set = post_per_school.get(school, set())
        if pre_set:
            reductions.append((len(pre_set) - len(post_set)) / len(pre_set) * 100)

    if reductions:
        reductions.sort()
        mid = len(reductions) // 2
        med_red = reductions[mid] if len(reductions) % 2 else \
                  (reductions[mid-1] + reductions[mid]) / 2
        print(f"\n  Median per-school /24 reduction  : {med_red:.1f}%")

    rows_out = [
        {"metric": "candidate_24s",          "value": len(pre_24s)},
        {"metric": "surviving_24s",           "value": len(post_24s)},
        {"metric": "removed_24s",             "value": removed_n},
        {"metric": "reduction_pct",           "value": round(pct, 2)},
        {"metric": "paper_hitlist_benchmark", "value": 34.9},
        {"metric": "median_per_school_pct",   "value": round(med_red, 2) if reductions else "n/a"},
    ]
    with open(OUT_HITLIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["metric","value"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"\nWritten -> {OUT_HITLIST}")


# ── Main ──────────────────────────────────────────────────────────────────

def run():
    print(f"Pipeline stats for {RADIUS}km run")
    print(f"{'='*60}")
    stage_reduction()
    provider_match()
    hitlist_reduction()
    print(f"\n{'='*60}")
    print("All done. Output files:")
    print(f"  {OUT_REDUCTION}")
    print(f"  {OUT_PROVIDER}")
    print(f"  {OUT_HITLIST}")


if __name__ == "__main__":
    run()
