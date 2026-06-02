"""
Post-7: URL-based forward DNS verification.

Independent check: for each high-confidence IP, look up the A record of the
corresponding school's website URL and check if the IP (or its /24) matches.

This is methodologically independent from every other phase:
  - Phase 2 uses reverse PTR lookup
  - Phase 3 uses ARIN/WHOIS
  - Phase 4 uses RIPE Atlas RTT
  - Phase 0 uses ARIN registry search
  This phase uses forward DNS A records — a completely different query direction.

Verdicts:
  EXACT_MATCH   — school URL's A record resolves exactly to the found IP
  SUBNET_MATCH  — A record resolves to the same /24 as the found IP
  NO_MATCH      — URL resolves, but to a different IP entirely
                  (common: school website hosted on CDN or third-party server)
  NXDOMAIN      — URL does not resolve (domain inactive or mis-entered)
  NO_URL        — school had no URL in schools_selected.csv

Important caveat: NO_MATCH ≠ FALSE_POSITIVE. Schools frequently outsource
their public website to hosted platforms. The IP we found (via PTR/ARIN) is
the school's *network* IP; the website A record may point elsewhere entirely.
Use EXACT_MATCH and SUBNET_MATCH as precision evidence; NO_MATCH is neutral.

Inputs:
  data/inputs/schools_selected.csv          (school URLs from peer team)
  data/outputs/phase3_reattributed_*km.csv  (high-confidence IPs)

Output:
  data/outputs/url_verification.csv
"""

import csv
import ipaddress
import re
import socket
import time
from collections import defaultdict

SCHOOLS_FILE = "data/inputs/schools_selected.csv"
RADII        = [5, 10, 20, 30]
OUTPUT_FILE  = "data/outputs/url_verification.csv"


def extract_district_code(url):
    """Extract the .k12.ny.us subdomain from a URL (e.g. mw, smithtown).
    Returns None if the URL doesn't match the NYSED zone pattern.
    """
    if not url:
        return None
    m = re.search(r'([a-z0-9-]+)\.k12\.ny\.us', url.lower())
    return m.group(1) if m else None


def ip_to_24(ip):
    return str(ipaddress.IPv4Network(f"{ip}/24", strict=False).network_address)


def resolve_domain(domain):
    """Return set of IPv4 A record IPs for domain, or empty set."""
    try:
        infos = socket.getaddrinfo(domain, None, socket.AF_INET)
        return {info[4][0] for info in infos}
    except Exception:
        return set()


def run(schools_file=SCHOOLS_FILE, output_file=OUTPUT_FILE):

    # Load TWO URL indexes from schools_selected.csv:
    #   url_by_school    : school_name -> URL (original join key)
    #   url_by_district  : district code -> any URL on that domain
    #                      (district code = the subdomain before .k12.ny.us,
    #                      e.g. "mw", "smithtown")
    # The second index handles the common case where Phase 3b re-attributes
    # IPs to a district whose specific name is not in the sampled set, but
    # OTHER schools in the same district ARE sampled and share the URL domain.
    url_by_school   = {}
    url_by_district = {}
    with open(schools_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["school_name"].strip()
            url  = row.get("trimmed_url", "").strip()
            if not url:
                continue
            url_by_school[name] = url
            code = extract_district_code(url)
            if code and code not in url_by_district:
                url_by_district[code] = url

    print(f"Loaded URLs for {len(url_by_school)} schools, "
          f"covering {len(url_by_district)} .k12.ny.us district domains")

    # Collect unique high-confidence IPs across all radii.
    # Carry both geo_school AND district_code so we can try multiple join keys.
    seen = {}
    for radius in RADII:
        filepath = f"data/outputs/phase3_reattributed_{radius}km.csv"
        try:
            with open(filepath, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("confidence") != "high":
                        continue
                    ip            = row["ip_address"].strip()
                    geo_school    = row.get("geo_school", row["school_name"]).strip()
                    district      = row["school_name"].strip()
                    district_code = row.get("district_code", "").strip()
                    hostname      = row.get("hostname", "").strip()
                    if ip not in seen:
                        seen[ip] = {
                            "ip":            ip,
                            "geo_school":    geo_school,
                            "district":      district,
                            "district_code": district_code,
                            "hostname":      hostname,
                        }
        except FileNotFoundError:
            print(f"Warning: {filepath} not found, skipping")

    print(f"Found {len(seen)} unique high-confidence IPs to check")

    domain_cache = {}
    results      = []

    for i, (ip, info) in enumerate(seen.items(), 1):
        geo_school    = info["geo_school"]
        district      = info["district"]
        district_code = info["district_code"]

        # Try join keys in order of preference:
        #   1. district_code (Phase 3b output) -> any URL on that .k12.ny.us domain.
        #      This is the strongest join: it matches IPs whose attribution comes
        #      from the NYSED zone to URLs on the same NYSED zone.
        #   2. geo_school     -> URL of that sampled school (original logic).
        #   3. district name  -> URL of a sampled school with that exact name.
        domain     = ""
        join_via   = ""
        if district_code and district_code in url_by_district:
            domain   = url_by_district[district_code]
            join_via = "district_code"
        elif geo_school in url_by_school:
            domain   = url_by_school[geo_school]
            join_via = "geo_school"
        elif district in url_by_school:
            domain   = url_by_school[district]
            join_via = "district_name"

        if not domain:
            verdict   = "NO_URL"
            a_records = set()
        else:
            if domain not in domain_cache:
                a_records = resolve_domain(domain)
                domain_cache[domain] = a_records
                time.sleep(0.05)   # be polite to DNS resolvers
            else:
                a_records = domain_cache[domain]

            if not a_records:
                verdict = "NXDOMAIN"
            elif ip in a_records:
                verdict = "EXACT_MATCH"
            elif ip_to_24(ip) in {ip_to_24(a) for a in a_records}:
                verdict = "SUBNET_MATCH"
            else:
                verdict = "NO_MATCH"

        if i % 200 == 0 or verdict in ("EXACT_MATCH", "SUBNET_MATCH"):
            print(f"[{i}/{len(seen)}] {ip:<18}  via={join_via or '-':<13}  "
                  f"{domain:<30}  -> {verdict}")

        results.append({
            "ip_address":    ip,
            "geo_school":    geo_school,
            "district":      district,
            "district_code": district_code,
            "hostname":      info["hostname"],
            "domain":        domain,
            "join_via":      join_via,
            "a_records":     "|".join(sorted(a_records)),
            "verdict":       verdict,
        })

    tally = defaultdict(int)
    for r in results:
        tally[r["verdict"]] += 1

    _order = {"EXACT_MATCH": 0, "SUBNET_MATCH": 1, "NO_MATCH": 2,
              "NXDOMAIN": 3, "NO_URL": 4}
    results.sort(key=lambda r: (_order.get(r["verdict"], 9), r["geo_school"]))

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ip_address", "geo_school", "district", "district_code",
                           "hostname", "domain", "join_via", "a_records", "verdict"]
        )
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone -> {output_file}")
    for label in ("EXACT_MATCH", "SUBNET_MATCH", "NO_MATCH", "NXDOMAIN", "NO_URL"):
        n = tally[label]
        print(f"  {label:<15}: {n}")

    matchable = tally["EXACT_MATCH"] + tally["SUBNET_MATCH"] + tally["NO_MATCH"]
    if matchable > 0:
        match_rate = (tally["EXACT_MATCH"] + tally["SUBNET_MATCH"]) / matchable
        print(f"\n  Match rate (IPs with resolvable URLs): {match_rate:.1%}")
        print(f"  ({tally['EXACT_MATCH']} exact + {tally['SUBNET_MATCH']} /24 subnet"
              f" out of {matchable} resolvable)")

    # Frame the result for readers who don't already know the K-12 architecture
    # pattern.  A 0% or near-0% match rate is the EXPECTED outcome for K-12
    # institutions: public marketing websites are hosted on CMS platforms
    # (Apptegy, Finalsite, etc.) fronted by CDNs (Fastly, Cloudflare), while
    # the operational network IPs we identify are on county BOCES / WAN
    # provider blocks.  NO_MATCH is therefore neutral evidence; it does not
    # indicate a false positive.
    if tally["NO_MATCH"] > 0 and tally["EXACT_MATCH"] == 0 and tally["SUBNET_MATCH"] == 0:
        print()
        print("  NOTE: A 0% match rate is the EXPECTED outcome for K-12.")
        print("  Public school websites are typically CMS-hosted (Apptegy, Finalsite, etc.)")
        print("  on commercial CDNs, while the network IPs identified by reverse-DNS")
        print("  belong to county BOCES / WAN provider blocks. NO_MATCH is therefore")
        print("  NEUTRAL evidence — it does NOT indicate the identified IPs are wrong.")
        print("  Positive precision evidence in this script requires EXACT_MATCH or")
        print("  SUBNET_MATCH, which is unlikely for K-12 institutions by design.")


if __name__ == "__main__":
    run()
