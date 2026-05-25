import csv
import re
import ipaddress
from ipwhois import IPWhois

INPUT_FILE     = "data/phase2_filtered.csv"
PROVIDERS_FILE = "data/school_providers.csv"
OUTPUT_FILE    = "data/phase3_confirmed.csv"

# if the IP is owned by one of these, it's a hosting provider, not a school
HOSTING_KEYWORDS = {
    "cloudflare", "google", "amazon", "aws", "microsoft",
    "azure", "fastly", "akamai", "digitalocean"
}

# org names that suggest an educational network
EDU_KEYWORDS = {"cenic", "university", "education", "school", "research", "unified", "merit"}

# noise words to strip before comparing company names
GENERIC_TERMS = [
    r'\binc\b', r'\bllc\b', r'\bcorp\b', r'\bcorporation\b',
    r'\bco\b', r'\bltd\b', r'\bnetwork\b', r'\bnetworks\b',
    r'\bcommunications\b', r'\bservices\b', r'\btelecom\b', r'\bisp\b',
]

# cache WHOIS results so we don't repeat lookups for IPs in the same block
whois_cache = {}


def normalize_org(name):
    if not name or name in ("Error", "Unknown"):
        return ""
    name = name.lower()
    for term in GENERIC_TERMS:
        name = re.sub(term, "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return " ".join(name.split())


# IPs sharing the first 3 octets usually have the same owner
def get_24_prefix(ip):
    try:
        return str(ipaddress.IPv4Network(f"{ip}/24", strict=False).network_address)
    except ValueError:
        return None


def whois_lookup(ip):
    prefix = get_24_prefix(ip)
    if prefix and prefix in whois_cache:
        return whois_cache[prefix]
    try:
        result = IPWhois(ip).lookup_rdap(depth=1)
        asn      = result.get("asn", "") or ""
        org_name = result.get("asn_description", "") or ""
        data = (asn, org_name)
    except Exception:
        data = ("", "")
    if prefix:
        whois_cache[prefix] = data
    return data


# check if the WHOIS owner is one of the ISPs that serves this school's area
def fcc_match(school_name, norm_org, providers):
    allowed = providers.get(school_name, [])
    for provider in allowed:
        norm_provider = normalize_org(provider)
        if norm_provider and (norm_provider in norm_org or norm_org in norm_provider):
            return True
    return False


# score 0-3: dns match=+1, whois/edu match=+1, fcc match=+1
def compute_confidence(rdns_match, whois_match, fcc_matched):
    score = sum([rdns_match, whois_match, fcc_matched])
    if score >= 3:
        label = "high"
    elif score >= 2:
        label = "medium"
    else:
        label = "low"
    return score, label


if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # load ISP providers per school from fcc_get_providers.py output
    providers = {}
    try:
        with open(PROVIDERS_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                school = row["school_name"].strip()
                provider_list = [p.strip() for p in row["providers"].split("|") if p.strip()]
                providers[school] = provider_list
        print(f"Loaded FCC providers for {len(providers)} schools")
    except FileNotFoundError:
        print(f"Warning: {PROVIDERS_FILE} not found, FCC matching disabled")

    candidates = [r for r in rows if r["match_type"] in {"match", "partial_match"}]
    total = len(candidates)
    print(f"Processing {total} IPs from {INPUT_FILE}")

    results = []

    for i, row in enumerate(candidates, 1):
        ip     = row["ip_address"].strip()
        school = row["school_name"].strip()
        rdns   = row["match_type"].strip()

        asn, org_name = whois_lookup(ip)
        norm_org = normalize_org(org_name)

        is_hosting = any(h in norm_org for h in HOSTING_KEYWORDS)
        is_edu     = any(e in norm_org for e in EDU_KEYWORDS)

        if is_hosting:
            whois_match = False
            fcc_matched = False
        else:
            whois_match = is_edu or fcc_match(school, norm_org, providers)
            fcc_matched = bool(providers.get(school)) and fcc_match(school, norm_org, providers)

        rdns_match = rdns in ("match", "partial_match")
        score, confidence = compute_confidence(rdns_match, whois_match, fcc_matched)

        results.append({
            "ip_address":   ip,
            "school_name":  school,
            "hostname":     row.get("hostname", ""),
            "phase2_match": rdns,
            "asn":          asn,
            "whois_org":    org_name,
            "is_hosting":   "yes" if is_hosting else "no",
            "whois_match":  "yes" if whois_match else "no",
            "fcc_match":    "yes" if fcc_matched else "no",
            "score":        score,
            "confidence":   confidence,
        })

        print(f"{i}/{total}  {ip:<18}  {org_name[:30]:<30}  fcc={'yes' if fcc_matched else 'no'}  score={score}  [{confidence}]")

    results.sort(key=lambda r: (r["school_name"], -r["score"]))

    fieldnames = [
        "ip_address", "school_name", "hostname", "phase2_match",
        "asn", "whois_org", "is_hosting", "whois_match", "fcc_match", "score", "confidence"
    ]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    high   = sum(1 for r in results if r["confidence"] == "high")
    medium = sum(1 for r in results if r["confidence"] == "medium")
    low    = sum(1 for r in results if r["confidence"] == "low")
    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"high: {high}  medium: {medium}  low: {low}")
