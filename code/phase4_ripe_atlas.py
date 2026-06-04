"""Phase 4: RIPE Atlas validation via Speed-of-Internet (SoI) constraint.

Pings each high-confidence IP from near probes (<40km) and far probes (>100km).
If RTT from a far probe implies the IP is physically closer to that probe than
to the school, it's flagged invalid. SoI constant: 4/9 * c ~= 133.2 km/ms.
"""

import csv
import time
import math
from collections import defaultdict
import requests

API_KEY      = "30474c9f-e4c0-4e96-9397-c0158d144694"
INPUT_FILE   = "data/outputs/phase3_confirmed.csv"
SCHOOLS_FILE = "data/inputs/gigamaps_schools_ny.csv"
OUTPUT_FILE  = "data/outputs/phase4_validated.csv"

ATLAS_API = "https://atlas.ripe.net/api/v2"
HEADERS   = {"Authorization": f"Key {API_KEY}", "Content-Type": "application/json"}

SOI_KM_PER_MS      = (4 / 9) * (299792.458 / 1000)  # ~133.2 km/ms
SOI_BUFFER_KM      = 50
NEAR_KM            = 40
FAR_KM             = 100
VALIDATE           = {"high"}
MAX_IPS_PER_SCHOOL = 20


def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def get_nearby_probes(lat, lon, max_count=3):
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
            print(f"    ping error: {r.status_code}: {r.text}")
            return None
        ids = r.json().get("measurements", [])
        return ids[0] if ids else None
    except Exception as e:
        print(f"    ping error: {e}")
        return None


def wait_for_results(measurement_id, timeout_s=180, poll_s=15):
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
    rtts = [
        res["avg"] for res in results
        if res.get("prb_id") in probe_ids
        and isinstance(res.get("avg"), (int, float))
        and res["avg"] > 0
    ]
    return min(rtts) if rtts else None


def is_soi_violation(far_rtt_ms, school_lat, school_lon, far_probe):
    """True if RTT proves the IP is closer to the far probe than the school is."""
    coords = far_probe.get("geometry", {}).get("coordinates", [])
    if len(coords) != 2 or far_rtt_ms is None:
        return False
    fp_lat, fp_lon      = coords[1], coords[0]
    far_to_school_km    = distance_km(school_lat, school_lon, fp_lat, fp_lon)
    soi_max_distance_km = SOI_KM_PER_MS * far_rtt_ms + SOI_BUFFER_KM
    return soi_max_distance_km < far_to_school_km


def run(input_file=INPUT_FILE, schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

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

    # Cap per school. Prioritize strong_dns_match, then ny_k12_domain, then score.
    per_school = defaultdict(list)
    for r in to_validate_all:
        per_school[r["school_name"]].append(r)

    to_validate = []
    capped_rows = []
    for school, ips in per_school.items():
        ips_sorted = sorted(ips,
                            key=lambda r: (-int(r.get("strong_dns_match") == "yes"),
                                          -int(r.get("ny_k12_domain") == "yes"),
                                          -int(r.get("score", 0))))
        to_validate.extend(ips_sorted[:MAX_IPS_PER_SCHOOL])
        capped_rows.extend(ips_sorted[MAX_IPS_PER_SCHOOL:])

    print(f"Validating {len(to_validate)} IPs via RIPE Atlas "
          f"(capped at {MAX_IPS_PER_SCHOOL}/school, {len(capped_rows)} capped, "
          f"{len(skip_rows)} low-confidence skipped)")
    print(f"Estimated credit cost: ~{len(to_validate) * 30} credits")

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

        lat, lon      = school_coords[school]
        near_probes   = get_nearby_probes(lat, lon, max_count=3)
        far_probes    = get_far_probes(lat, lon, max_count=2)
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

        near_ids = {p["id"] for p in near_probes[:2]}
        far_ids  = {p["id"] for p in far_probes[:1]}
        near_rtt = min_rtt_for_probes(ping_results, near_ids)
        far_rtt  = min_rtt_for_probes(ping_results, far_ids)

        print(f"    near_rtt={near_rtt}ms  far_rtt={far_rtt}ms", flush=True, end="")

        if near_rtt is None and far_rtt is None:
            print(f"  -> skipped (no ping response)")
            row["ripe_validated"] = "skipped"
            output_rows.append(row)
            n_skipped += 1
            time.sleep(2)
            continue

        invalid = bool(far_probes and far_rtt is not None
                       and is_soi_violation(far_rtt, lat, lon, far_probes[0]))

        status = "no" if invalid else "yes"
        n_invalid += int(invalid)
        n_valid   += int(not invalid)

        print(f"  -> {status}")
        row["ripe_validated"] = status
        output_rows.append(row)
        time.sleep(2)

    for r in capped_rows:
        r["ripe_validated"] = "not_run"
        output_rows.append(r)
    for r in skip_rows:
        r["ripe_validated"] = "not_run"
        output_rows.append(r)

    fieldnames = list(all_rows[0].keys()) + ["ripe_validated"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nDone. Results written to {output_file}")
    print(f"valid={n_valid}  invalid={n_invalid}  skipped={n_skipped}")


if __name__ == "__main__":
    run()
