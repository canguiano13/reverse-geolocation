"""
Phase 4 — RIPE Atlas Validation
---------------------------------
Uses RIPE Atlas (a global network of measurement probes) to ping each
high/medium confidence IP from two directions:
  - Near probes  : probes close to the school (~40 km away)
  - Far probes   : probes far from the school (~100+ km away)

If the far probe gets a very fast response, the IP is probably located
near the far probe — not near the school. We use the Speed of Internet
(SoI) formula from the professor's paper to detect this:
  max possible distance = RTT × 133.2 km/ms + 50 km buffer

If that distance is less than how far the far probe actually is from the
school, the IP is marked as invalid.
"""

import csv
import time
import math
import requests

API_KEY      = "30474c9f-e4c0-4e96-9397-c0158d144694"
INPUT_FILE   = "data/outputs/phase3_confirmed.csv"
SCHOOLS_FILE = "data/inputs/schools_selected.csv"
OUTPUT_FILE  = "data/outputs/phase4_validated.csv"

ATLAS_API    = "https://atlas.ripe.net/api/v2"
HEADERS      = {"Authorization": f"Key {API_KEY}", "Content-Type": "application/json"}

SOI_KM_PER_MS = (4 / 9) * (299792.458 / 1000)  # ~133.2 km/ms (speed of internet constant)
SOI_BUFFER_KM = 50    # extra slack added to the SoI bound (from the paper)
NEAR_KM       = 40    # probes within this range count as "near" the school
FAR_KM        = 100   # probes beyond this range count as "far"

VALIDATE          = {"high"}   # only ping high confidence IPs (~30 credits/IP)
MAX_IPS_PER_SCHOOL = 3         # cap per school — after scoring fix, thousands of IPs become
                               # "high"; we only need a sample per school to validate geography.


def distance_km(lat1, lon1, lat2, lon2):
    """Haversine distance between two GPS coordinates (km)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def get_nearby_probes(lat, lon, max_count=3):
    """Find active RIPE Atlas probes within NEAR_KM of a location."""
    try:
        r = requests.get(f"{ATLAS_API}/probes/", timeout=10, params={
            "status": 1,
            "radius": f"{lat},{lon}:{NEAR_KM}",
            "fields": "id,geometry",
            "page_size": max_count,
        })
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"    near probe error: {e}")
        return []


def get_far_probes(lat, lon, max_count=2):
    """Find active RIPE Atlas anchor probes at least FAR_KM away from a location."""
    try:
        r = requests.get(f"{ATLAS_API}/probes/", timeout=10, params={
            "status": 1, "is_anchor": True,
            "fields": "id,geometry", "page_size": 100,
        })
        r.raise_for_status()
        all_probes = r.json().get("results", [])
    except Exception as e:
        print(f"    far probe error: {e}")
        return []

    far_probes = []
    for probe in all_probes:
        coords = probe.get("geometry", {}).get("coordinates", [])
        if len(coords) == 2:
            d = distance_km(lat, lon, coords[1], coords[0])
            if d >= FAR_KM:
                probe["_dist"] = d
                far_probes.append(probe)

    far_probes.sort(key=lambda p: p["_dist"], reverse=True)
    return far_probes[:max_count]


def send_ping(target_ip, probe_ids):
    """Create a one-off RIPE Atlas ping measurement to target_ip from the given probes."""
    try:
        r = requests.post(f"{ATLAS_API}/measurements/", headers=HEADERS, timeout=15, json={
            "definitions": [{
                "type": "ping", "af": 4,
                "target": target_ip, "packets": 3,
                "description": f"school-validation {target_ip}",
            }],
            "probes": [{
                "type": "probes",
                "value": ",".join(str(p) for p in probe_ids),
                "requested": len(probe_ids),
            }],
            "is_oneoff": True,
        })
        if not r.ok:
            print(f"    ping error: {r.status_code} — {r.text}")
            return None
        ids = r.json().get("measurements", [])
        return ids[0] if ids else None
    except Exception as e:
        print(f"    ping error: {e}")
        return None


def wait_for_results(measurement_id, timeout_s=180, poll_s=15):
    """Poll RIPE Atlas until ping results arrive or we time out."""
    url      = f"{ATLAS_API}/measurements/{measurement_id}/results/"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.json()
            if data:
                return data
        except Exception:
            pass
        time.sleep(poll_s)
    return []


def min_rtt_for_probes(results, probe_ids):
    """Extract the minimum average RTT (ms) from ping results for a set of probe IDs."""
    rtts = [
        res["avg"] for res in results
        if res.get("prb_id") in probe_ids
        and isinstance(res.get("avg"), (int, float))
        and res["avg"] > 0
    ]
    return min(rtts) if rtts else None


def is_soi_violation(far_rtt_ms, school_lat, school_lon, far_probe):
    """
    Returns True if the IP is provably NOT at the school location.
    Logic: if the far probe's RTT implies the IP is within X km of the far probe,
    but the far probe is farther than X km from the school — contradiction.
    """
    coords = far_probe.get("geometry", {}).get("coordinates", [])
    if len(coords) != 2 or far_rtt_ms is None:
        return False

    fp_lat, fp_lon       = coords[1], coords[0]
    far_to_school_km     = distance_km(school_lat, school_lon, fp_lat, fp_lon)
    soi_max_distance_km  = SOI_KM_PER_MS * far_rtt_ms + SOI_BUFFER_KM

    # If IP must be within soi_max_distance_km of far probe,
    # but far probe is farther than that from the school → IP can't be at the school
    return soi_max_distance_km < far_to_school_km


def run(input_file=INPUT_FILE, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    # Step 1: Load school coordinates and phase 3 results
    school_coords = {}
    with open(schools_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            school_coords[row["school_name"].strip()] = (
                float(row["latitude"]), float(row["longitude"])
            )

    with open(input_file, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    to_validate_all = [r for r in all_rows if r["confidence"] in VALIDATE]
    skip_rows       = [r for r in all_rows if r["confidence"] not in VALIDATE]

    # Apply per-school cap: take up to MAX_IPS_PER_SCHOOL IPs per school.
    # Prioritise IPs with a ny_k12_domain hit (strongest signal), then by score.
    from collections import defaultdict
    per_school = defaultdict(list)
    for r in to_validate_all:
        per_school[r["school_name"]].append(r)

    to_validate = []
    capped_rows = []
    for school, ips in per_school.items():
        ips_sorted = sorted(ips,
                            key=lambda r: (-int(r.get("ny_k12_domain") == "yes"),
                                          -int(r.get("score", 0))))
        to_validate.extend(ips_sorted[:MAX_IPS_PER_SCHOOL])
        capped_rows.extend(ips_sorted[MAX_IPS_PER_SCHOOL:])

    print(f"Validating {len(to_validate)} IPs via RIPE Atlas "
          f"(capped at {MAX_IPS_PER_SCHOOL}/school, {len(capped_rows)} capped, "
          f"{len(skip_rows)} low-confidence skipped)")
    print(f"Estimated credit cost: ~{len(to_validate) * 30} credits")

    # Step 2: Ping each IP and check results
    output_rows = []
    n_valid = n_invalid = n_skipped = 0

    for i, row in enumerate(to_validate, 1):
        ip     = row["ip_address"].strip()
        school = row["school_name"].strip()
        print(f"[{i}/{len(to_validate)}] {ip}  {school[:40]}", flush=True)

        if school not in school_coords:
            print(f"    no coordinates found, skipping")
            row["ripe_validated"] = "skipped"
            output_rows.append(row)
            n_skipped += 1
            continue

        lat, lon     = school_coords[school]
        near_probes  = get_nearby_probes(lat, lon, max_count=3)
        far_probes   = get_far_probes(lat, lon, max_count=2)
        all_probe_ids = [p["id"] for p in near_probes[:2]] + [p["id"] for p in far_probes[:1]]

        if not all_probe_ids:
            print(f"    no probes available nearby, skipping")
            row["ripe_validated"] = "skipped"
            output_rows.append(row)
            n_skipped += 1
            continue

        measurement_id = send_ping(ip, all_probe_ids)
        if not measurement_id:
            row["ripe_validated"] = "skipped"
            output_rows.append(row)
            n_skipped += 1
            continue

        print(f"    measurement {measurement_id} created, waiting...", flush=True)
        ping_results = wait_for_results(measurement_id)

        if not ping_results:
            print(f"    no results received, skipping")
            row["ripe_validated"] = "skipped"
            output_rows.append(row)
            n_skipped += 1
            continue

        near_ids    = {p["id"] for p in near_probes[:2]}
        far_ids     = {p["id"] for p in far_probes[:1]}
        near_rtt    = min_rtt_for_probes(ping_results, near_ids)
        far_rtt     = min_rtt_for_probes(ping_results, far_ids)

        print(f"    near_rtt={near_rtt}ms  far_rtt={far_rtt}ms", flush=True, end="")

        # If neither probe got a response, we have no data — skip rather than assume yes
        if near_rtt is None and far_rtt is None:
            print(f"  → skipped (no ping response)")
            row["ripe_validated"] = "skipped"
            output_rows.append(row)
            n_skipped += 1
            time.sleep(2)
            continue

        # Check if the IP is provably in the wrong location via SoI violation
        invalid = bool(far_probes and far_rtt is not None
                       and is_soi_violation(far_rtt, lat, lon, far_probes[0]))

        status = "no" if invalid else "yes"
        n_invalid += int(invalid)
        n_valid   += int(not invalid)

        print(f"  → {status}")
        row["ripe_validated"] = status
        output_rows.append(row)
        time.sleep(2)

    # Step 3: Pass capped and low-confidence rows through unchanged
    for r in capped_rows:
        r["ripe_validated"] = "not_run"
        output_rows.append(r)
    for r in skip_rows:
        r["ripe_validated"] = "not_run"
        output_rows.append(r)

    # Step 4: Save results
    fieldnames = list(all_rows[0].keys()) + ["ripe_validated"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nDone. Results written to {output_file}")
    print(f"valid={n_valid}  invalid={n_invalid}  skipped={n_skipped}")


if __name__ == "__main__":
    run()
