"""
Phase 3 — WHOIS / ASN Confirmation
-------------------------------------
Takes the DNS matches from phase 2 and scores each IP on three signals:
  1. DNS match    — the hostname contains the school name or a k12 keyword
  2. WHOIS match  — the IP is registered to an educational network, OR
                    the ISP serving it is known to serve that school's area (FCC data)
  3. FCC match    — the ISP in the FCC broadband map matches the school's location

Score 3 → high confidence, 2 → medium, 1 → low.
IPs owned by hosting providers (Cloudflare, AWS, etc.) are flagged and scored down.
"""

import csv
import re
import ipaddress
from ipwhois import IPWhois

INPUT_FILE     = "data/phase2_filtered.csv"
PROVIDERS_FILE = "data/school_providers.csv"   # ISPs serving each school (from FCC data)
ASDB_FILE      = "data/2026-03_categorized_ases.csv"  # ASNs categorized as education
OUTPUT_FILE    = "data/phase3_confirmed.csv"

# IPs owned by these hosting/CDN providers are not school IPs
HOSTING_PROVIDERS = {
    "cloudflare", "google", "amazon", "aws", "microsoft",
    "azure", "fastly", "akamai", "digitalocean",
}

# Cache WHOIS results by /24 prefix — IPs in the same block usually have the same owner,
# so we avoid repeating the same lookup hundreds of times
whois_cache = {}


def load_edu_asns():
    """Load ASNs classified as education/research from the Stanford ASdb dataset."""
    edu_asns = set()
    try:
        with open(ASDB_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                categories = " ".join(v for k, v in row.items() if "Category" in k and v).lower()
                if "education" in categories or "research" in categories:
                    asn = str(row.get("ASN", "")).strip().lstrip("AS").lstrip("as")
                    if asn:
                        edu_asns.add(asn)
        print(f"Loaded {len(edu_asns)} educational ASNs")
    except FileNotFoundError:
        print(f"Warning: {ASDB_FILE} not found — educational ASN check disabled")
    return edu_asns


def load_fcc_providers():
    """Load which ISPs serve each school's area (output of fcc_get_providers.py)."""
    providers = {}
    try:
        with open(PROVIDERS_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                school = row["school_name"].strip()
                providers[school] = [p.strip() for p in row["providers"].split("|") if p.strip()]
        print(f"Loaded FCC providers for {len(providers)} schools")
    except FileNotFoundError:
        print(f"Warning: {PROVIDERS_FILE} not found — FCC matching disabled")
    return providers


def whois_lookup(ip):
    """
    Look up who owns an IP address using WHOIS/RDAP.
    Caches results by /24 prefix to avoid redundant lookups.
    Returns (asn, org_name).
    """
    # use the first 3 octets as a cache key (e.g. 192.168.1.0 for any 192.168.1.x)
    try:
        prefix = str(ipaddress.IPv4Network(f"{ip}/24", strict=False).network_address)
    except ValueError:
        prefix = ip

    if prefix in whois_cache:
        return whois_cache[prefix]

    try:
        result   = IPWhois(ip).lookup_rdap(depth=1)
        asn      = result.get("asn", "") or ""
        org_name = result.get("asn_description", "") or ""
    except Exception:
        asn, org_name = "", ""

    whois_cache[prefix] = (asn, org_name)
    return asn, org_name


def org_matches_provider(org_name, allowed_providers):
    """
    Check if an org name fuzzy-matches any of the school's known ISPs.
    Strips common legal suffixes (Inc, LLC, etc.) before comparing.
    """
    def clean(name):
        name = name.lower()
        for suffix in ["inc", "llc", "corp", "corporation", "co", "ltd",
                       "network", "networks", "communications", "services",
                       "telecom", "isp"]:
            name = re.sub(r'\b' + suffix + r'\b', '', name)
        return " ".join(re.sub(r"[^\w\s]", "", name).split())

    cleaned_org = clean(org_name)
    if not cleaned_org:
        return False

    for provider in allowed_providers:
        cleaned_provider = clean(provider)
        if cleaned_provider and (cleaned_provider in cleaned_org or cleaned_org in cleaned_provider):
            return True
    return False


def score_ip(hostname, asn, org_name, school_name, edu_asns, fcc_providers):
    """
    Score an IP on three signals and return (score, confidence_label).
    Also returns whether it's a hosting provider IP (those are scored down).
    """
    org_lower = org_name.lower()

    # Check if this IP belongs to a hosting/CDN provider — not a school
    is_hosting = any(h in org_lower for h in HOSTING_PROVIDERS)

    # Signal 1: DNS match (always true here since phase 2 already filtered)
    dns_match = True

    # Signal 2: WHOIS/ASN match — educational ASN or known local ISP
    if is_hosting:
        whois_match = False
    else:
        is_edu_asn  = str(asn) in edu_asns
        allowed_isps = fcc_providers.get(school_name, [])
        isp_match   = org_matches_provider(org_name, allowed_isps)
        whois_match = is_edu_asn or isp_match

    # Signal 3: FCC match — ISP specifically listed for this school's area
    if is_hosting or not fcc_providers.get(school_name):
        fcc_match = False
    else:
        fcc_match = org_matches_provider(org_name, fcc_providers[school_name])

    # Defense: out-of-state k12 hostnames override all signals
    # (phase 2 should have caught these, but just in case)
    state_match = re.search(r'\.k12\.([a-z]{2})\.us', hostname.lower())
    if state_match and state_match.group(1) != 'ny':
        whois_match = False
        fcc_match   = False

    score = sum([dns_match, whois_match, fcc_match])
    label = "high" if score >= 3 else "medium" if score >= 2 else "low"

    return score, label, is_hosting, whois_match, fcc_match


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):

    # Step 1: Load phase 2 results and supporting data
    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    edu_asns     = load_edu_asns()
    fcc_providers = load_fcc_providers()

    candidates = [r for r in rows if r["match_type"] in {"match", "partial_match"}]
    print(f"Processing {len(candidates)} IPs")

    # Step 2: Score each IP
    results = []
    for i, row in enumerate(candidates, 1):
        ip     = row["ip_address"].strip()
        school = row["school_name"].strip()

        asn, org_name = whois_lookup(ip)
        score, confidence, is_hosting, whois_match, fcc_match = score_ip(
            row.get("hostname", ""), asn, org_name, school, edu_asns, fcc_providers
        )

        results.append({
            "ip_address":   ip,
            "school_name":  school,
            "hostname":     row.get("hostname", ""),
            "phase2_match": row["match_type"],
            "asn":          asn,
            "whois_org":    org_name,
            "is_hosting":   "yes" if is_hosting else "no",
            "whois_match":  "yes" if whois_match else "no",
            "fcc_match":    "yes" if fcc_match else "no",
            "score":        score,
            "confidence":   confidence,
        })

        print(f"{i}/{len(candidates)}  {ip:<18}  {org_name[:30]:<30}  "
              f"fcc={'yes' if fcc_match else 'no'}  score={score}  [{confidence}]")

    # Step 3: Sort and save
    results.sort(key=lambda r: (r["school_name"], -r["score"]))

    fieldnames = [
        "ip_address", "school_name", "hostname", "phase2_match",
        "asn", "whois_org", "is_hosting", "whois_match", "fcc_match", "score", "confidence",
    ]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    high   = sum(1 for r in results if r["confidence"] == "high")
    medium = sum(1 for r in results if r["confidence"] == "medium")
    low    = sum(1 for r in results if r["confidence"] == "low")
    print(f"\nDone. Results written to {output_file}")
    print(f"high: {high}  medium: {medium}  low: {low}")


if __name__ == "__main__":
    run()
