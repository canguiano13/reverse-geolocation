import csv
import time
import math
import requests

API_KEY      = "30474c9f-e4c0-4e96-9397-c0158d144694"
INPUT_FILE   = "data/phase3_confirmed.csv"
SCHOOLS_FILE = "data/schools_selected.csv"
OUTPUT_FILE  = "data/phase4_validated.csv"

ATLAS_BASE  = "https://atlas.ripe.net/api/v2"
HEADERS     = {"Authorization": f"Key {API_KEY}", "Content-Type": "application/json"}

# speed of internet constant from the paper (4/9 * speed of light in km/ms)
SOI_KM_PER_MS = (4 / 9) * (299792.458 / 1000)  # ~133.2 km/ms
SOI_BUFFER_KM = 50  # extra buffer from the paper

NEAR_KM = 40   # probes within this range are considered "near"
FAR_KM  = 100  # probes beyond this are considered "far"

# only run RIPE Atlas on these confidence levels to save credits
VALIDATE = {"high", "medium"}


def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def find_near_probes(lat, lon, limit=3):
    """Find active RIPE Atlas probes within NEAR_KM of the school."""
    try:
        r = requests.get(f"{ATLAS_BASE}/probes/", params={
            "status": 1,
            "radius": f"{lat},{lon}:{NEAR_KM}",
            "fields": "id,geometry",
            "page_size": limit,
        }, timeout=10)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"    near probe lookup error: {e}")
        return []


def find_far_probes(lat, lon, limit=2):
    """Find anchor probes that are at least FAR_KM away from the school."""
    try:
        r = requests.get(f"{ATLAS_BASE}/probes/", params={
            "status": 1,
            "is_anchor": True,
            "fields": "id,geometry",
            "page_size": 100,
        }, timeout=10)
        r.raise_for_status()
        probes = r.json().get("results", [])

        far = []
        for p in probes:
            coords = p.get("geometry", {}).get("coordinates", [])
            if len(coords) == 2:
                d = distance_km(lat, lon, coords[1], coords[0])
                if d >= FAR_KM:
                    p["_dist"] = d
                    far.append(p)

        # prefer probes that are very far away
        far.sort(key=lambda x: x["_dist"], reverse=True)
        return far[:limit]
    except Exception as e:
        print(f"    far probe lookup error: {e}")
        return []


def create_ping(target_ip, probe_ids):
    """Create a one-off ping measurement from the given probes to target_ip."""
    try:
        r = requests.post(f"{ATLAS_BASE}/measurements/", headers=HEADERS, timeout=15, json={
            "definitions": [{
                "type": "ping",
                "af": 4,
                "target": target_ip,
                "packets": 3,
                "description": f"school-ip-validation {target_ip}",
            }],
            "probes": [{
                "type": "probes",
                "value": ",".join(str(pid) for pid in probe_ids),
                "requested": len(probe_ids),
            }],
            "is_oneoff": True,
        })
        if not r.ok:
            print(f"    measurement creation error: {r.status_code} — {r.text}")
            return None
        ids = r.json().get("measurements", [])
        return ids[0] if ids else None
    except Exception as e:
        print(f"    measurement creation error: {e}")
        return None


def fetch_results(msm_id, wait_s=180, poll_s=15):
    """Poll for measurement results until they arrive or we time out."""
    url = f"{ATLAS_BASE}/measurements/{msm_id}/results/"
    deadline = time.time() + wait_s
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


def min_rtt(results, probe_ids):
    """Extract the minimum avg RTT for a set of probe IDs."""
    rtts = []
    for res in results:
        if res.get("prb_id") in probe_ids:
            avg = res.get("avg")
            if isinstance(avg, (int, float)) and avg > 0:
                rtts.append(avg)
    return min(rtts) if rtts else None


def soi_violation(far_rtt_ms, school_lat, school_lon, far_probe):
    """
    Returns True if the IP is provably closer to the far probe than to the school.
    Uses the Speed of Internet (SoI) constraint from the paper:
    if the IP can only be within (SoI * RTT) km of the far probe,
    but the school is farther than that — the IP can't be at the school.
    """
    coords = far_probe.get("geometry", {}).get("coordinates", [])
    if len(coords) != 2 or far_rtt_ms is None:
        return False

    fp_lat, fp_lon = coords[1], coords[0]
    d_far_to_school = distance_km(school_lat, school_lon, fp_lat, fp_lon)
    soi_bound = SOI_KM_PER_MS * far_rtt_ms

    return (soi_bound + SOI_BUFFER_KM) < d_far_to_school


if __name__ == "__main__":
    # load school coordinates
    school_coords = {}
    with open(SCHOOLS_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            school_coords[row["school_name"].strip()] = (
                float(row["latitude"]), float(row["longitude"])
            )

    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    to_validate = [r for r in all_rows if r["confidence"] in VALIDATE]
    skip_rows   = [r for r in all_rows if r["confidence"] not in VALIDATE]

    print(f"Running RIPE Atlas validation on {len(to_validate)} IPs (high + medium confidence)")
    print(f"Skipping {len(skip_rows)} low-confidence IPs to save credits")

    results_out = []
    n_valid = n_invalid = n_skipped = 0

    for i, row in enumerate(to_validate, 1):
        ip     = row["ip_address"].strip()
        school = row["school_name"].strip()

        if school not in school_coords:
            print(f"[{i}/{len(to_validate)}] {ip} — no coordinates for school, skipping")
            row["ripe_validated"] = "skipped"
            results_out.append(row)
            n_skipped += 1
            continue

        lat, lon = school_coords[school]
        print(f"[{i}/{len(to_validate)}] {ip}  {school[:40]}", flush=True)

        near_probes = find_near_probes(lat, lon, limit=3)
        far_probes  = find_far_probes(lat, lon, limit=2)
        probe_ids   = [p["id"] for p in near_probes[:2]] + [p["id"] for p in far_probes[:1]]

        if not probe_ids:
            print(f"    no probes available, skipping")
            row["ripe_validated"] = "skipped"
            results_out.append(row)
            n_skipped += 1
            continue

        msm_id = create_ping(ip, probe_ids)
        if not msm_id:
            row["ripe_validated"] = "skipped"
            results_out.append(row)
            n_skipped += 1
            continue

        print(f"    measurement {msm_id} created, waiting for results...", flush=True)
        msm_results = fetch_results(msm_id)

        if not msm_results:
            print(f"    no results received, skipping")
            row["ripe_validated"] = "skipped"
            results_out.append(row)
            n_skipped += 1
            continue

        near_ids = {p["id"] for p in near_probes[:2]}
        far_ids  = {p["id"] for p in far_probes[:1]}

        near_rtt_val = min_rtt(msm_results, near_ids)
        far_rtt_val  = min_rtt(msm_results, far_ids)

        # check if the IP is provably not at the school
        invalid = False
        if far_probes and far_rtt_val is not None:
            invalid = soi_violation(far_rtt_val, lat, lon, far_probes[0])

        if invalid:
            status = "no"
            n_invalid += 1
        else:
            status = "yes"
            n_valid += 1

        print(f"    near_rtt={near_rtt_val}ms  far_rtt={far_rtt_val}ms  → {status}")
        row["ripe_validated"] = status
        results_out.append(row)

        time.sleep(2)  # be polite to the API

    # pass low confidence rows through unchanged
    for r in skip_rows:
        r["ripe_validated"] = "not_run"
        results_out.append(r)

    fieldnames = list(all_rows[0].keys()) + ["ripe_validated"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results_out)

    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"valid={n_valid}  invalid={n_invalid}  skipped={n_skipped}")
