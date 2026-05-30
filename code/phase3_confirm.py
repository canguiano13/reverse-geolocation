"""
Phase 3 — WHOIS / ASN Confirmation
-------------------------------------
Takes the DNS matches from phase 2 and scores each IP on four signals:
  1. DNS match      — the hostname contains the school name or a k12 keyword
  2. NY K12 domain  — hostname is in *.k12.ny.us (NY state-managed school domain)
                      This is the strongest possible signal — state-assigned PTR records
  3. WHOIS match    — the IP is registered to an educational network, OR
                      the ISP serving it is known to serve that school's area (FCC data)
  4. FCC match      — the ISP in the FCC broadband map matches the school's location

Score 3+ high confidence, 2 medium, 1 low.
IPs owned by hosting providers (Cloudflare, AWS, etc.) are flagged and scored down.
"""

import csv
import re
import ipaddress
from ipwhois import IPWhois

INPUT_FILE     = "data/outputs/phase2_filtered.csv"
PROVIDERS_FILE = "data/inputs/school_providers.csv"    # ISPs serving each school (from FCC data)
ASDB_FILE      = "data/inputs/2026-03_categorized_ases.csv"   # ASNs categorized as education
OUTPUT_FILE    = "data/outputs/phase3_confirmed.csv"

# IPs owned by these hosting/CDN providers are not school IPs
HOSTING_PROVIDERS = {
    "cloudflare", "google", "amazon", "aws", "microsoft",
    "azure", "fastly", "akamai", "digitalocean",
}

# Cache WHOIS results by /24 prefix — IPs in the same block usually have the same owner,
# so we avoid repeating the same lookup hundreds of times
whois_cache = {}


def load_edu_asns():
    """
    Load ASNs classified as education from the Stanford ASdb dataset.

    Uses exact category string matching (from peer code / ASdbFilter.py) rather
    than loose substring matching on "education" or "research".  The targeted
    categories are:
      - "Education and Research"
      - "Elementary and Secondary Schools"
      - "Colleges, Universities, and Professional Schools"
      - "Other Schools, Instruction, and Exam Preparation..."

    Exact matching avoids false positives like "Research Hospitals" or
    "Defense Research" being pulled in as school ASNs.
    """
    EDU_CATEGORIES = {
        "Education and Research",
        "Elementary and Secondary Schools",
        "Colleges, Universities, and Professional Schools",
        "Other Schools, Instruction, and Exam Preparation and Testing",
    }

    edu_asns = set()
    try:
        with open(ASDB_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Collect all category columns (handles variable column names)
                row_cats = {v.strip() for k, v in row.items()
                            if "Category" in k and v and v.strip()}
                if row_cats & EDU_CATEGORIES:          # exact intersection
                    asn = str(row.get("ASN", "")).strip().lstrip("AS").lstrip("as")
                    if asn:
                        edu_asns.add(asn)
        print(f"Loaded {len(edu_asns)} educational ASNs (exact category match)")
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
    Score an IP on four signals and return (score, confidence_label).
    Also returns whether it's a hosting provider IP (those are scored down).

    Signal breakdown:
      dns_match     — Phase 2 found a k12 keyword or school name in the PTR record
      ny_k12_domain — hostname is in *.k12.ny.us (NY state-assigned school domain)
                      This is the strongest signal: state-managed, unambiguous.
      whois_match   — educational ASN or known local ISP
      fcc_match     — ISP specifically listed in FCC data for this school's area
    """
    h = hostname.lower()
    org_lower = org_name.lower()

    # Check if this IP belongs to a hosting/CDN provider — not a school
    is_hosting = any(h in org_lower for h in HOSTING_PROVIDERS)

    # Signal 1: DNS match (always true here since phase 2 already filtered)
    dns_match = True

    # Signal 2: NY K12 domain — hostname is in *.k12.ny.us
    # This is NY's state-managed school domain. Any IP with a PTR in this domain
    # is definitively a NY school IP — no school-name matching required.
    state_match = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state_match and state_match.group(1) != 'ny':
        # Out-of-state k12 domain — reject entirely
        return 0, "low", is_hosting, False, False
    ny_k12_domain = bool(state_match and state_match.group(1) == 'ny')

    # Signal 3: WHOIS/ASN match — educational ASN or known local ISP
    if is_hosting:
        whois_match = False
    else:
        is_edu_asn   = str(asn) in edu_asns
        allowed_isps = fcc_providers.get(school_name, [])
        isp_match    = org_matches_provider(org_name, allowed_isps)
        whois_match  = is_edu_asn or isp_match

    # Signal 4: FCC match — ISP specifically listed for this school's area
    if is_hosting or not fcc_providers.get(school_name):
        fcc_match = False
    else:
        fcc_match = org_matches_provider(org_name, fcc_providers[school_name])

    score = sum([dns_match, ny_k12_domain, whois_match, fcc_match])

    # *.k12.ny.us is a NY State-managed DNS zone — only actual NY school districts
    # are delegated subdomains. An IP with a PTR in that zone is definitively a NY
    # school IP regardless of whether the transit ISP is "educational".
    # Lower the high-confidence threshold to 2 when ny_k12_domain is confirmed.
    if ny_k12_domain:
        label = "high" if score >= 2 else "medium"
    else:
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
        hostname = row.get("hostname", "")
        score, confidence, is_hosting, whois_match, fcc_match = score_ip(
            hostname, asn, org_name, school, edu_asns, fcc_providers
        )
        ny_k12 = bool(re.search(r'\.k12\.ny\.us', hostname.lower()))

        results.append({
            "ip_address":   ip,
            "school_name":  school,
            "hostname":     hostname,
            "phase2_match": row["match_type"],
            "asn":          asn,
            "whois_org":    org_name,
            "is_hosting":   "yes" if is_hosting else "no",
            "ny_k12_domain":"yes" if ny_k12 else "no",
            "whois_match":  "yes" if whois_match else "no",
            "fcc_match":    "yes" if fcc_match else "no",
            "score":        score,
            "confidence":   confidence,
        })

        print(f"{i}/{len(candidates)}  {ip:<18}  {org_name[:30]:<30}  "
              f"fcc={'yes' if fcc_match else 'no'}  score={score}  [{confidence}]")

    # Step 3: Deduplicate by ip_address — the same IP can appear under multiple
    # nearby schools when the same CIDR block geo-matches more than one school.
    # Keep the row with the highest score; break ties by preferring ny_k12_domain.
    ip_best = {}
    for r in results:
        ip = r["ip_address"]
        if ip not in ip_best:
            ip_best[ip] = r
        else:
            prev = ip_best[ip]
            if (r["score"] > prev["score"] or
                    (r["score"] == prev["score"] and
                     r["ny_k12_domain"] == "yes" and prev["ny_k12_domain"] != "yes")):
                ip_best[ip] = r
    dedup_count = len(results) - len(ip_best)
    if dedup_count:
        print(f"Deduplicated {dedup_count} IPs that appeared under multiple schools")
    results = list(ip_best.values())

    # Sort and save
    results.sort(key=lambda r: (r["school_name"], -r["score"]))

    fieldnames = [
        "ip_address", "school_name", "hostname", "phase2_match",
        "asn", "whois_org", "is_hosting", "ny_k12_domain", "whois_match", "fcc_match",
        "score", "confidence",
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
