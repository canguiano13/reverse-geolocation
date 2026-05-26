import csv
import re
import requests
import time
from collections import defaultdict

FILES = {
    "10km": "data/phase3_confirmed_10km.csv",
    "20km": "data/phase3_confirmed_20km.csv",
}
OUTPUT_FILE = "data/verification_results.csv"

RDAP_URL = "https://rdap.arin.net/registry/ip/{}"


def rdap_lookup(ip):
    try:
        r = requests.get(RDAP_URL.format(ip), timeout=8)
        if r.status_code != 200:
            return "", ""
        data = r.json()

        # org name: try entities first, then name field
        org = ""
        for entity in data.get("entities", []):
            roles = entity.get("roles", [])
            if "registrant" in roles or "administrative" in roles:
                vcard = entity.get("vcardArray", [])
                if isinstance(vcard, list) and len(vcard) > 1:
                    for entry in vcard[1]:
                        if entry[0] == "fn":
                            org = entry[3]
                            break
            if org:
                break
        if not org:
            org = data.get("name", "")

        # country from cidr0_cidrs or handle
        country = ""
        port43 = data.get("port43", "")
        if "arin" in port43.lower():
            country = "US"
        remarks = data.get("remarks", [])
        for remark in remarks:
            desc = " ".join(remark.get("description", []))
            m = re.search(r'\b([A-Z]{2})\b', desc)
            if m:
                country = m.group(1)
                break

        return org, country
    except Exception as e:
        return "", ""


# manually resolved verdicts for IPs that fell into MANUAL_CHECK
KNOWN_VERDICTS = {
    "63.119.227.174": ("FALSE_POSITIVE", "Holmdel Board of Education is in New Jersey, not NY"),
    "24.39.160.166":  ("FALSE_POSITIVE", "Albany Academy is a different private school in Albany — wrong school matched"),
    "24.103.2.227":   ("FALSE_POSITIVE", "queenscp.org — 'new' in hostname matched 'New Hyde Park' only"),
    "67.55.77.75":    ("FALSE_POSITIVE", "newopportunitiesnow.com — 'new' matched 'New Hyde Park' only"),
    "65.242.140.38":  ("FALSE_POSITIVE", "Howard Press is a printing company, not a school"),
    "24.103.218.34":  ("TRUE_POSITIVE",  "Mountain Lake Academy is a real K-12 school in Lake Placid, NY (different school, same town as target)"),
    "64.19.74.218":   ("FALSE_POSITIVE", "Slic Network Solutions serves northern NY only — does not cover Herkimer County where Ohio, NY is located"),
}


def classify(ip, hostname, school_name, org):
    if ip in KNOWN_VERDICTS:
        return KNOWN_VERDICTS[ip]

    hostname_l = hostname.lower()
    org_l      = org.lower()
    school_l   = school_name.lower()

    # --- hostname-based rules (most reliable) ---

    # NY K-12 hostname → true positive
    if re.search(r'\.k12\.ny\.us', hostname_l):
        return "TRUE_POSITIVE", "hostname is .k12.ny.us"

    # another state's K-12 → false positive
    m = re.search(r'\.k12\.([a-z]{2})\.us', hostname_l)
    if m and m.group(1) != "ny":
        return "FALSE_POSITIVE", f"hostname is .k12.{m.group(1)}.us (different state)"

    # comcast with state name in hostname
    for state in ["florida", "indiana", "texas", "california", "ohio", ".fl.", ".in.", ".tx.", ".ca."]:
        if state in hostname_l and "comcast" in hostname_l:
            return "FALSE_POSITIVE", f"Comcast server in another state ({state})"

    # broadcast / infrastructure hostnames from ISPs
    if "broadcast.zip.zayo.com" in hostname_l:
        return "FALSE_POSITIVE", "Zayo fiber broadcast address — 'new' in IP matched school name"

    # .edu that isn't a NY school
    if hostname_l.endswith(".edu") or ".edu." in hostname_l:
        if "ny" not in hostname_l and not any(kw in hostname_l for kw in ["cuny", "suny", "cornell", "columbia", "nyu"]):
            return "FALSE_POSITIVE", "non-NY university hostname"

    # --- org-based rules ---

    # NY school district in org name
    if ("school" in org_l or "k12" in org_l or "district" in org_l or "education" in org_l) and \
       ("ny" in org_l or "new york" in org_l or "scarsdale" in org_l or "massapequa" in org_l):
        return "TRUE_POSITIVE", f"ARIN org is NY school/district: {org}"

    # any school district org (could be another state)
    if "k12" in org_l and re.search(r'\.k12\.[a-z]{2}\.us', hostname_l):
        return "FALSE_POSITIVE", f"K-12 org but not NY: {org}"

    # known companies / non-school orgs
    company_keywords = [
        "baker hughes", "howard press", "polaner", "webair", "mainstreet",
        "zayo", "shorter university", "comcast", "verizon", "alter.net",
        "lakeworth", "greenwood", "tamworth",
    ]
    for kw in company_keywords:
        if kw in org_l or kw in hostname_l:
            return "FALSE_POSITIVE", f"known non-school org/hostname: {kw}"

    # "new" in hostname matching "New Hyde Park" pattern
    school_tokens = set(school_l.replace("-", " ").split())
    hostname_tokens = set(re.sub(r"[^a-z0-9]", " ", hostname_l).split())
    matching_tokens = school_tokens & hostname_tokens
    if matching_tokens == {"new"} or (len(matching_tokens) == 1 and "new" in matching_tokens):
        return "FALSE_POSITIVE", "only 'new' matched — likely 'New Hyde Park' false positive"

    return "MANUAL_CHECK", f"org={org or 'unknown'}, hostname={hostname}"


if __name__ == "__main__":
    # collect unique (ip, hostname, school, run) combos — deduplicate by ip+hostname
    seen      = {}   # (ip, hostname) → (school, run)
    run_label = {}   # (ip, hostname) → run

    for run, filepath in FILES.items():
        try:
            with open(filepath, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["confidence"] != "high":
                        continue
                    key = (row["ip_address"].strip(), row["hostname"].strip())
                    if key not in seen:
                        seen[key]      = row["school_name"].strip()
                        run_label[key] = run
                    else:
                        # appears in both — label as both
                        run_label[key] = "both"
        except FileNotFoundError:
            print(f"Warning: {filepath} not found, skipping")

    print(f"Unique high-confidence IP+hostname pairs: {len(seen)}")

    # RDAP cache per IP (not per hostname)
    rdap_cache = {}

    results = []
    for i, ((ip, hostname), school) in enumerate(seen.items(), 1):
        if ip not in rdap_cache:
            print(f"[{i}/{len(seen)}] RDAP lookup: {ip} ...", end=" ", flush=True)
            org, country = rdap_lookup(ip)
            rdap_cache[ip] = (org, country)
            time.sleep(0.3)   # be polite to ARIN
        else:
            org, country = rdap_cache[ip]
            print(f"[{i}/{len(seen)}] cached: {ip}", end=" ", flush=True)

        verdict, reason = classify(ip, hostname, school, org)
        print(f"→ {verdict}")

        results.append({
            "run":          run_label[(ip, hostname)],
            "ip_address":   ip,
            "matched_school": school,
            "hostname":     hostname,
            "arin_org":     org,
            "verdict":      verdict,
            "reason":       reason,
        })

    # sort: true positives first, then manual, then false
    order = {"TRUE_POSITIVE": 0, "MANUAL_CHECK": 1, "FALSE_POSITIVE": 2}
    results.sort(key=lambda r: (order.get(r["verdict"], 9), r["matched_school"]))

    fieldnames = ["run", "ip_address", "matched_school", "hostname", "arin_org", "verdict", "reason"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    tp = sum(1 for r in results if r["verdict"] == "TRUE_POSITIVE")
    fp = sum(1 for r in results if r["verdict"] == "FALSE_POSITIVE")
    mc = sum(1 for r in results if r["verdict"] == "MANUAL_CHECK")

    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"TRUE_POSITIVE : {tp}")
    print(f"FALSE_POSITIVE: {fp}")
    print(f"MANUAL_CHECK  : {mc}")
    if tp + fp > 0:
        print(f"Precision (excluding MANUAL_CHECK): {tp / (tp + fp):.1%}")
