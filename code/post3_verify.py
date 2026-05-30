"""
Verification — Check high-confidence IPs against ARIN RDAP.

For every high-confidence IP in phase 3 results, look up the actual owner
in ARIN and classify it as TRUE_POSITIVE, FALSE_POSITIVE, or MANUAL_CHECK.

Output: verification_results.csv
"""

import csv
import re
import time
import requests

FILES = {
    "10km": "data/outputs/phase3_confirmed_10km.csv",
    "20km": "data/outputs/phase3_confirmed_20km.csv",
}
OUTPUT_FILE = "data/outputs/verification_results.csv"
ARIN_URL    = "https://rdap.arin.net/registry/ip/{}"

# Hardcoded verdicts for IPs already manually verified
KNOWN_VERDICTS = {
    "63.119.227.174": ("FALSE_POSITIVE", "Holmdel Board of Education is in New Jersey, not NY"),
    "24.39.160.166":  ("FALSE_POSITIVE", "Albany Academy is a different private school"),
    "24.103.2.227":   ("FALSE_POSITIVE", "'new' matched 'New Hyde Park' only"),
    "67.55.77.75":    ("FALSE_POSITIVE", "'new' matched 'New Hyde Park' only"),
    "65.242.140.38":  ("FALSE_POSITIVE", "Howard Press is a printing company"),
    "24.103.218.34":  ("TRUE_POSITIVE",  "Mountain Lake Academy is a real K-12 school in Lake Placid, NY"),
    "64.19.74.218":   ("FALSE_POSITIVE", "Slic Network Solutions does not serve Herkimer County"),
}

KNOWN_NON_SCHOOLS = [
    "baker hughes", "howard press", "howardpress", "polaner", "webair",
    "newopportunitiesnow", "mainstreet", "zayo", "shorter university",
    "comcast", "verizon", "alter.net", "lakeworth", "greenwood", "tamworth",
]


def arin_lookup(ip):
    """Ask ARIN who owns an IP. Returns (org_name, country)."""
    try:
        r = requests.get(ARIN_URL.format(ip), timeout=8)
        if r.status_code != 200:
            return "", ""
        data = r.json()
        org = ""
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


def classify(ip, hostname, school_name, org):
    """Return (verdict, reason) for an IP."""
    if ip in KNOWN_VERDICTS:
        return KNOWN_VERDICTS[ip]

    h = hostname.lower()
    o = org.lower()

    if re.search(r'\.k12\.ny\.us', h):
        return "TRUE_POSITIVE", "hostname is .k12.ny.us"

    state = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state and state.group(1) != "ny":
        return "FALSE_POSITIVE", f"hostname is .k12.{state.group(1)}.us (different state)"

    for location in ["florida", "indiana", "texas", "california", ".fl.", ".in.", ".tx.", ".ca."]:
        if location in h and "comcast" in h:
            return "FALSE_POSITIVE", f"Comcast server in another state ({location})"

    if "broadcast.zip.zayo.com" in h:
        return "FALSE_POSITIVE", "Zayo fiber broadcast address"

    if ".edu" in h and not any(ny in h for ny in ["cuny", "suny", "cornell", "columbia", "nyu"]):
        return "FALSE_POSITIVE", "non-NY university hostname"

    if any(w in o for w in ["school", "k12", "district", "education"]):
        if any(w in o for w in ["ny", "new york", "scarsdale", "massapequa"]):
            return "TRUE_POSITIVE", f"ARIN org is a NY school: {org}"

    for name in KNOWN_NON_SCHOOLS:
        if name in o or name in h:
            return "FALSE_POSITIVE", f"known non-school: {name}"

    # Single-word "new" match — New Hyde Park false positive
    school_words   = set(school_name.lower().split())
    hostname_words = set(re.sub(r"[^a-z0-9]", " ", h).split())
    if school_words & hostname_words == {"new"}:
        return "FALSE_POSITIVE", "only 'new' matched — New Hyde Park false positive"

    return "MANUAL_CHECK", f"org={org or 'unknown'}, hostname={hostname}"


def run(files=None, output_file=OUTPUT_FILE):
    if files is None:
        files = FILES

    # Collect unique high-confidence IPs across all runs
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

        org, country = arin_cache[ip]
        verdict, reason = classify(ip, hostname, school, org)
        print(f"{verdict}")

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
            f, fieldnames=["run", "ip_address", "matched_school", "hostname", "arin_org", "verdict", "reason"]
        )
        writer.writeheader()
        writer.writerows(results)

    tp = sum(1 for r in results if r["verdict"] == "TRUE_POSITIVE")
    fp = sum(1 for r in results if r["verdict"] == "FALSE_POSITIVE")
    mc = sum(1 for r in results if r["verdict"] == "MANUAL_CHECK")
    print(f"\nDone {output_file}")
    print(f"TRUE_POSITIVE: {tp}  FALSE_POSITIVE: {fp}  MANUAL_CHECK: {mc}")
    if tp + fp > 0:
        print(f"Precision: {tp / (tp + fp):.1%}")


if __name__ == "__main__":
    run()
