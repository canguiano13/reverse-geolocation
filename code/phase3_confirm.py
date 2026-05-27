import csv
import re
import ipaddress
from ipwhois import IPWhois

INPUT_FILE     = "data/phase2_filtered.csv"
PROVIDERS_FILE = "data/school_providers.csv"
ASDB_FILE      = "data/2026-03_categorized_ases.csv"
OUTPUT_FILE    = "data/phase3_confirmed.csv"

HOSTING_KEYWORDS = {
    "cloudflare", "google", "amazon", "aws", "microsoft",
    "azure", "fastly", "akamai", "digitalocean"
}

GENERIC_TERMS = [
    r'\binc\b', r'\bllc\b', r'\bcorp\b', r'\bcorporation\b',
    r'\bco\b', r'\bltd\b', r'\bnetwork\b', r'\bnetworks\b',
    r'\bcommunications\b', r'\bservices\b', r'\btelecom\b', r'\bisp\b',
]

whois_cache = {}


def load_edu_asns():
    edu_asns = set()
    try:
        with open(ASDB_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                all_cats = " ".join(v for k, v in row.items() if "Category" in k and v).lower()
                if "education" in all_cats or "research" in all_cats:
                    asn = str(row.get("ASN", "")).strip()
                    if asn.upper().startswith("AS"):
                        asn = asn[2:]
                    if asn:
                        edu_asns.add(asn)
        print(f"Loaded {len(edu_asns)} educational ASNs from {ASDB_FILE}")
    except FileNotFoundError:
        print(f"Warning: {ASDB_FILE} not found, educational ASN check disabled")
    return edu_asns


def normalize_org(name):
    if not name or name in ("Error", "Unknown"):
        return ""
    name = name.lower()
    for term in GENERIC_TERMS:
        name = re.sub(term, "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return " ".join(name.split())


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


def fcc_match(school_name, norm_org, providers):
    allowed = providers.get(school_name, [])
    for provider in allowed:
        norm_provider = normalize_org(provider)
        if norm_provider and (norm_provider in norm_org or norm_org in norm_provider):
            return True
    return False


def compute_confidence(rdns_match, whois_match, fcc_matched):
    score = sum([rdns_match, whois_match, fcc_matched])
    if score >= 3:
        label = "high"
    elif score >= 2:
        label = "medium"
    else:
        label = "low"
    return score, label


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    edu_asns = load_edu_asns()

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
    print(f"Processing {total} IPs from {input_file}")

    results = []

    for i, row in enumerate(candidates, 1):
        ip     = row["ip_address"].strip()
        school = row["school_name"].strip()
        rdns   = row["match_type"].strip()

        asn, org_name = whois_lookup(ip)
        norm_org = normalize_org(org_name)

        is_hosting = any(h in norm_org for h in HOSTING_KEYWORDS)
        is_edu     = str(asn) in edu_asns

        if is_hosting:
            whois_match = False
            fcc_matched = False
        else:
            whois_match = is_edu or fcc_match(school, norm_org, providers)
            fcc_matched = bool(providers.get(school)) and fcc_match(school, norm_org, providers)

        # defense-in-depth: out-of-state k12 hostnames cannot be NY schools
        # (phase2 should have filtered these already, but this catches any edge cases)
        hostname_l = row.get("hostname", "").lower()
        m = re.search(r'\.k12\.([a-z]{2})\.us', hostname_l)
        if m and m.group(1) != 'ny':
            whois_match = False
            fcc_matched = False

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
