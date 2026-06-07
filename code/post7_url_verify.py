import csv
import ipaddress
import re
import socket
import time
from collections import defaultdict

SCHOOLS_FILE = "data/inputs/schools_selected.csv"
RADII        = [5, 10, 20, 30]
OUTPUT_FILE  = "data/outputs/url_verification.csv"


def resolve(domain):
    try:
        return {info[4][0] for info in socket.getaddrinfo(domain, None, socket.AF_INET)}
    except Exception:
        return set()


def to_24(ip):
    return str(ipaddress.IPv4Network(f"{ip}/24", strict=False).network_address)


def run(schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):
    # Two URL indexes: by school name and by district code (subdomain before .k12.ny.us).
    # The second handles Phase 3b re-attributions to districts not in the sampled set.
    url_by_school   = {}
    url_by_district = {}
    code_re = re.compile(r'([a-z0-9-]+)\.k12\.ny\.us')
    with open(schools_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = row.get("trimmed_url", "").strip()
            if not url:
                continue
            url_by_school[row["school_name"].strip()] = url
            m = code_re.search(url.lower())
            if m and m.group(1) not in url_by_district:
                url_by_district[m.group(1)] = url

    print(f"Loaded URLs for {len(url_by_school)} schools, "
          f"covering {len(url_by_district)} .k12.ny.us district domains")

    seen = {}
    for radius in RADII:
        path = f"data/outputs/phase3_reattributed_{radius}km.csv"
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("confidence") != "high":
                        continue
                    ip = row["ip_address"].strip()
                    if ip in seen:
                        continue
                    seen[ip] = {
                        "geo_school":    row.get("geo_school", row["school_name"]).strip(),
                        "district":      row["school_name"].strip(),
                        "district_code": row.get("district_code", "").strip(),
                        "hostname":      row.get("hostname", "").strip(),
                    }
        except FileNotFoundError:
            print(f"Warning: {path} not found, skipping")

    print(f"Found {len(seen)} unique high-confidence IPs to check")

    cache   = {}
    results = []
    for i, (ip, info) in enumerate(seen.items(), 1):
        # Prefer district_code (Phase 3b) then fall back to sampled-school lookup
        domain, via = "", ""
        if info["district_code"] and info["district_code"] in url_by_district:
            domain, via = url_by_district[info["district_code"]], "district_code"
        elif info["geo_school"] in url_by_school:
            domain, via = url_by_school[info["geo_school"]], "geo_school"
        elif info["district"] in url_by_school:
            domain, via = url_by_school[info["district"]], "district_name"

        a_records = set()
        if not domain:
            verdict = "NO_URL"
        else:
            if domain not in cache:
                cache[domain] = resolve(domain)
                time.sleep(0.05)
            a_records = cache[domain]
            if not a_records:
                verdict = "NXDOMAIN"
            elif ip in a_records:
                verdict = "EXACT_MATCH"
            elif to_24(ip) in {to_24(a) for a in a_records}:
                verdict = "SUBNET_MATCH"
            else:
                verdict = "NO_MATCH"

        if i % 200 == 0 or verdict in ("EXACT_MATCH", "SUBNET_MATCH"):
            print(f"[{i}/{len(seen)}] {ip:<18}  via={via or '-':<13}  "
                  f"{domain:<30}  -> {verdict}")

        results.append({
            "ip_address":    ip,
            "geo_school":    info["geo_school"],
            "district":      info["district"],
            "district_code": info["district_code"],
            "hostname":      info["hostname"],
            "domain":        domain,
            "join_via":      via,
            "a_records":     "|".join(sorted(a_records)),
            "verdict":       verdict,
        })

    tally = defaultdict(int)
    for r in results:
        tally[r["verdict"]] += 1

    order = {"EXACT_MATCH": 0, "SUBNET_MATCH": 1, "NO_MATCH": 2, "NXDOMAIN": 3, "NO_URL": 4}
    results.sort(key=lambda r: (order.get(r["verdict"], 9), r["geo_school"]))

    fields = ["ip_address", "geo_school", "district", "district_code",
              "hostname", "domain", "join_via", "a_records", "verdict"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    print(f"\nDone -> {output_file}")
    for label in ("EXACT_MATCH", "SUBNET_MATCH", "NO_MATCH", "NXDOMAIN", "NO_URL"):
        print(f"  {label:<15}: {tally[label]}")

    matchable = tally["EXACT_MATCH"] + tally["SUBNET_MATCH"] + tally["NO_MATCH"]
    if matchable:
        rate = (tally["EXACT_MATCH"] + tally["SUBNET_MATCH"]) / matchable
        print(f"\n  Match rate (IPs with resolvable URLs): {rate:.1%}")
        print(f"  ({tally['EXACT_MATCH']} exact + {tally['SUBNET_MATCH']} /24 subnet"
              f" out of {matchable} resolvable)")

    if tally["NO_MATCH"] > 0 and tally["EXACT_MATCH"] == 0 and tally["SUBNET_MATCH"] == 0:
        print("\n  NOTE: 0% match is expected for K-12 (websites are CMS/CDN-hosted, "
              "separate from network IPs). NO_MATCH is neutral, not a false positive.")


if __name__ == "__main__":
    run()
