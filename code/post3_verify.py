import csv
import re
import time
import requests

OUTPUT_FILE = "data/outputs/verification_results.csv"
ARIN_URL    = "https://rdap.arin.net/registry/ip/{}"

NY_INDICATORS = {"ny", "new york", "newyork"}
EDU_KEYWORDS  = {"school", "k12", "district", "education", "academy", "boces", "ufsd"}


def arin_lookup(ip):
    try:
        r = requests.get(ARIN_URL.format(ip), timeout=8)
        if r.status_code != 200:
            return "", ""
        data = r.json()
        org  = ""
        for entity in data.get("entities", []):
            if "registrant" in entity.get("roles", []) or "administrative" in entity.get("roles", []):
                for entry in entity.get("vcardArray", [None, []])[1]:
                    if entry[0] == "fn":
                        org = entry[3]
                        break
            if org:
                break
        if not org:
            org = data.get("name", "")
        country = "US" if "arin" in data.get("port43", "").lower() else ""
        return org, country
    except Exception:
        return "", ""


def classify(hostname, org):
    h = hostname.lower()
    o = org.lower()

    if re.search(r'\.k12\.ny\.us', h):
        return "TRUE_POSITIVE", "hostname is .k12.ny.us"

    state = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state and state.group(1) != "ny":
        return "FALSE_POSITIVE", f"hostname is .k12.{state.group(1)}.us (different state)"

    if any(kw in o for kw in EDU_KEYWORDS) and any(ny in o for ny in NY_INDICATORS):
        return "TRUE_POSITIVE", f"ARIN org is a NY school: {org}"

    return "MANUAL_CHECK", f"org={org or 'unknown'}, hostname={hostname}"


def run(files=None, output_file=OUTPUT_FILE):
    if files is None:
        files = {
            "5km":  "data/outputs/phase3_confirmed_5km.csv",
            "10km": "data/outputs/phase3_confirmed_10km.csv",
            "20km": "data/outputs/phase3_confirmed_20km.csv",
            "30km": "data/outputs/phase3_confirmed_30km.csv",
        }

    # Deduplicate across radii - same IP+hostname only verified once
    seen      = {}
    run_label = {}
    for run_name, filepath in files.items():
        try:
            with open(filepath, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["confidence"] != "high":
                        continue
                    key = (row["ip_address"].strip(), row["hostname"].strip())
                    if key not in seen:
                        seen[key]      = row["school_name"].strip()
                        run_label[key] = run_name
                    else:
                        run_label[key] = "both"
        except FileNotFoundError:
            print(f"Warning: {filepath} not found, skipping")

    print(f"Found {len(seen)} unique high-confidence IP+hostname pairs")

    arin_cache = {}
    results    = []

    for i, ((ip, hostname), school) in enumerate(seen.items(), 1):
        if ip not in arin_cache:
            print(f"[{i}/{len(seen)}] ARIN lookup: {ip} ...", end=" ", flush=True)
            arin_cache[ip] = arin_lookup(ip)
            time.sleep(0.3)
        else:
            print(f"[{i}/{len(seen)}] cached: {ip}", end=" ", flush=True)

        org, country    = arin_cache[ip]
        verdict, reason = classify(hostname, org)
        print(f"-> {verdict}")

        results.append({
            "run":            run_label[(ip, hostname)],
            "ip_address":     ip,
            "matched_school": school,
            "hostname":       hostname,
            "arin_org":       org,
            "verdict":        verdict,
            "reason":         reason,
        })

    order = {"TRUE_POSITIVE": 0, "MANUAL_CHECK": 1, "FALSE_POSITIVE": 2}
    results.sort(key=lambda r: (order.get(r["verdict"], 9), r["matched_school"]))

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["run", "ip_address", "matched_school", "hostname",
                           "arin_org", "verdict", "reason"]
        )
        writer.writeheader()
        writer.writerows(results)

    tp = sum(1 for r in results if r["verdict"] == "TRUE_POSITIVE")
    fp = sum(1 for r in results if r["verdict"] == "FALSE_POSITIVE")
    mc = sum(1 for r in results if r["verdict"] == "MANUAL_CHECK")
    print(f"\nDone -> {output_file}")
    print(f"TRUE_POSITIVE: {tp}  FALSE_POSITIVE: {fp}  MANUAL_CHECK: {mc}")
    if tp + fp > 0:
        print(f"Precision: {tp / (tp + fp):.1%}")


if __name__ == "__main__":
    run()
