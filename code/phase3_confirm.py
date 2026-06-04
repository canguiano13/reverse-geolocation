

import csv
import math
import re
import ipaddress
from collections import Counter
from ipwhois import IPWhois

INPUT_FILE     = "data/outputs/phase2_filtered.csv"
PROVIDERS_FILE = "data/inputs/school_providers.csv"
ASDB_FILE      = "data/inputs/2026-03_categorized_ases.csv"
OUTPUT_FILE    = "data/outputs/phase3_confirmed.csv"

HOSTING_PROVIDERS = {
    "cloudflare", "google", "amazon", "aws", "microsoft",
    "azure", "fastly", "akamai", "digitalocean",
}

# WHOIS results cached by /24 prefix: IPs in the same block usually share an owner
whois_cache = {}

# Generic tokens stripped before TF-IDF tokenization
_GENERIC_TOKENS = {
    "inc", "llc", "corp", "corporation", "co", "ltd", "the",
    "network", "networks", "communications", "communication",
    "services", "service", "telecom", "telecommunications", "isp",
    "cable", "internet", "broadband", "company", "group",
}


def load_edu_asns():
    """Load ASNs classified as education from Stanford ASdb (exact category match)."""
    EDU_CATEGORIES = {
        "Education and Research",
        "Elementary and Secondary Schools",
        "Colleges, Universities, and Professional Schools",
        "Other Schools, Instruction, and Exam Preparation and Testing",
    }
    edu_asns = set()
    try:
        with open(ASDB_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row_cats = {v.strip() for k, v in row.items()
                            if "Category" in k and v and v.strip()}
                if row_cats & EDU_CATEGORIES:
                    asn = str(row.get("ASN", "")).strip().lstrip("AS").lstrip("as")
                    if asn:
                        edu_asns.add(asn)
        print(f"Loaded {len(edu_asns)} educational ASNs")
    except FileNotFoundError:
        print(f"Warning: {ASDB_FILE} not found, educational ASN check disabled")
    return edu_asns


def load_fcc_providers():
    """Load ISPs serving each school's area (from fcc_get_providers.py)."""
    providers = {}
    try:
        with open(PROVIDERS_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                school = row["school_name"].strip()
                providers[school] = [p.strip() for p in row["providers"].split("|") if p.strip()]
        print(f"Loaded FCC providers for {len(providers)} schools")
    except FileNotFoundError:
        print(f"Warning: {PROVIDERS_FILE} not found, FCC matching disabled")
    return providers


def whois_lookup(ip):
    """Returns (asn, org_name). Cached by /24."""
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


def _tokenize_name(name):
    if not name:
        return []
    tokens = re.findall(r'[a-z0-9]{3,}', name.lower())
    return [t for t in tokens if t not in _GENERIC_TOKENS]


def build_brand_extractor(corpus):
    """
    Build a TF-IDF brand keyword extractor from a corpus of provider/org names.
    Returns a function: name -> set of brand keywords (normalized IDF >= 0.7,
    or top-1 fallback).
    """
    docs = [_tokenize_name(n) for n in corpus if n]
    n_docs = max(len(docs), 1)

    doc_freq = Counter()
    for tokens in docs:
        for t in set(tokens):
            doc_freq[t] += 1

    max_idf = math.log(n_docs) if n_docs > 1 else 1.0

    def extract(name, threshold=0.7):
        tokens = _tokenize_name(name)
        if not tokens:
            return set()
        scores = {}
        for t in tokens:
            df = doc_freq.get(t, 0)
            idf = max_idf if df == 0 else math.log(n_docs / df)
            scores[t] = idf / max_idf if max_idf > 0 else 0.0
        above = {t for t, s in scores.items() if s >= threshold}
        return above if above else {max(scores, key=scores.get)}

    return extract


def org_matches_provider(org_name, allowed_providers, brand_extractor):
    """True if org name shares a brand keyword with any allowed provider."""
    if not org_name or not allowed_providers:
        return False
    org_brands = brand_extractor(org_name)
    if not org_brands:
        return False
    for provider in allowed_providers:
        if org_brands & brand_extractor(provider):
            return True
    return False


def score_ip(hostname, asn, org_name, school_name, edu_asns, fcc_providers, brand_extractor):
    """Returns (score, confidence, is_hosting, whois_match, fcc_match)."""
    h = hostname.lower()
    org_lower = org_name.lower()

    is_hosting = any(p in org_lower for p in HOSTING_PROVIDERS)

    # Signal 1: phase 2 already filtered, so dns match is implicit
    dns_match = True

    # Signal 2: .k12.ny.us hostname. Other states get rejected outright.
    state_match = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state_match and state_match.group(1) != 'ny':
        return 0, "low", is_hosting, False, False
    ny_k12_domain = bool(state_match and state_match.group(1) == 'ny')

    # Signal 3: educational ASN or known local ISP
    if is_hosting:
        whois_match = False
    else:
        is_edu_asn   = str(asn) in edu_asns
        allowed_isps = fcc_providers.get(school_name, [])
        whois_match  = is_edu_asn or org_matches_provider(org_name, allowed_isps, brand_extractor)

    # Signal 4: ISP specifically in FCC data for this school's area
    if is_hosting or not fcc_providers.get(school_name):
        fcc_match = False
    else:
        fcc_match = org_matches_provider(org_name, fcc_providers[school_name], brand_extractor)

    score = sum([dns_match, ny_k12_domain, whois_match, fcc_match])

    # *.k12.ny.us is state-managed; lower the threshold when confirmed
    if ny_k12_domain:
        label = "high" if score >= 2 else "medium"
    else:
        label = "high" if score >= 3 else "medium" if score >= 2 else "low"

    return score, label, is_hosting, whois_match, fcc_match


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    edu_asns      = load_edu_asns()
    fcc_providers = load_fcc_providers()

    # Build TF-IDF brand extractor. Include previous WHOIS orgs from any
    # existing output to enrich the corpus.
    corpus = []
    for plist in fcc_providers.values():
        corpus.extend(plist)
    try:
        with open(output_file, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("whois_org"):
                    corpus.append(r["whois_org"])
    except FileNotFoundError:
        pass
    brand_extractor = build_brand_extractor(corpus)
    print(f"Built TF-IDF brand extractor from {len(corpus)} provider/org names")

    candidates = [r for r in rows if r["match_type"] in {"match", "partial_match"}]
    print(f"Processing {len(candidates)} IPs")

    results = []
    for i, row in enumerate(candidates, 1):
        ip     = row["ip_address"].strip()
        school = row["school_name"].strip()

        asn, org_name = whois_lookup(ip)
        hostname = row.get("hostname", "")
        score, confidence, is_hosting, whois_match, fcc_match = score_ip(
            hostname, asn, org_name, school, edu_asns, fcc_providers, brand_extractor
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

    # Dedup by ip_address: same IP can match multiple nearby schools.
    # Keep highest score; break ties preferring ny_k12_domain.
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
