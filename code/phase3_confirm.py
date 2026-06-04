"""Phase 3: ASN/org confirmation.

Scores each IP on four signals: dns_match, strong_dns_match, whois_match, fcc_match.
ISP name matching uses TF-IDF brand keyword extraction (normalized IDF >= 0.7).
Score 3+ = high confidence (or 2+ when strong_dns_match), 1 = low.
ASN lookup uses IPinfo IP-to-ASN dataset (offline, no live RDAP calls).
"""

import csv
import gzip
import ipaddress
import math
import re
from collections import Counter

INPUT_FILE       = "data/outputs/phase2_filtered.csv"
PROVIDERS_FILE   = "data/inputs/school_providers.csv"
IPINFO_ASN_FILE  = "data/inputs/ipinfo/ipinfo_asn.csv.gz"
OUTPUT_FILE      = "data/outputs/phase3_confirmed.csv"

_GENERIC_TOKENS = {
    "inc", "llc", "corp", "corporation", "co", "ltd", "the",
    "network", "networks", "communications", "communication",
    "services", "service", "telecom", "telecommunications", "isp",
    "cable", "internet", "broadband", "company", "group",
}

# Backbone/transit providers that IPinfo sometimes tags as 'hosting'.
# These are wholesale bandwidth providers that legitimately serve schools.
# Verified against school_providers.csv -- only Zayo appears as an actual
# NY school provider and is misclassified on some prefixes.
_BACKBONE_OVERRIDE = {"zayo"}

# Module-level ASN lookup state
_asn_entries = []   # sorted list of (net_start, net_end, prefix_len, asn, name, asn_type)
_asn_cache   = {}   # /24 network_address_int -> (asn, name, asn_type)


def load_ipinfo_asn(path):
    global _asn_entries, _asn_cache
    _asn_cache = {}
    entries = []
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                net = ipaddress.ip_network(row['network'], strict=False)
            except ValueError:
                continue
            if net.version != 4:
                continue
            asn_str = row['asn'].lstrip('AS').lstrip('as')
            entries.append((
                int(net.network_address),
                int(net.broadcast_address),
                net.prefixlen,
                asn_str, row['name'], row['type'],
            ))
    entries.sort()
    _asn_entries = entries
    print(f"Loaded {len(_asn_entries)} IPinfo ASN entries")


def lookup_ip_asn(ip_str):
    """Return (asn, org_name, asn_type) for ip_str. Results cached by /24."""
    try:
        key = int(ipaddress.ip_address(ip_str)) & 0xFFFFFF00
    except ValueError:
        return '', '', ''

    if key in _asn_cache:
        return _asn_cache[key]

    ip_int = key  # /24 network address representative

    # Binary search: rightmost entry where net_start <= ip_int
    lo, hi = 0, len(_asn_entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if _asn_entries[mid][0] <= ip_int:
            lo = mid + 1
        else:
            hi = mid

    # Scan backwards for the most specific (longest prefix) entry containing ip_int.
    # Stop once net_start falls outside the span of a /8 block -- no broader prefix
    # is relevant for org-level attribution.
    min_start = ip_int - (1 << 24)
    best_plen = -1
    result = ('', '', '')
    for i in range(lo - 1, -1, -1):
        net_start, net_end, plen, asn, name, asn_type = _asn_entries[i]
        if net_start < min_start:
            break
        if net_end >= ip_int and plen > best_plen:
            best_plen = plen
            result = (asn, name, asn_type)

    _asn_cache[key] = result
    return result


def load_fcc_providers():
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


def _tokenize_name(name):
    if not name:
        return []
    tokens = re.findall(r'[a-z0-9]{3,}', name.lower())
    return [t for t in tokens if t not in _GENERIC_TOKENS]


def build_brand_extractor(corpus):
    docs   = [_tokenize_name(n) for n in corpus if n]
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
    if not org_name or not allowed_providers:
        return False
    org_brands = brand_extractor(org_name)
    if not org_brands:
        return False
    return any(org_brands & brand_extractor(p) for p in allowed_providers)


def score_ip(hostname, asn, org_name, asn_type, school_name, fcc_providers, brand_extractor,
             strong_dns_match=False):
    """Returns (score, confidence, is_hosting, whois_match, fcc_match)."""
    h         = hostname.lower()
    org_lower = org_name.lower()

    is_hosting = (asn_type == "hosting") and not any(
        kw in org_name.lower() for kw in _BACKBONE_OVERRIDE
    )

    dns_match = True  # phase 2 already filtered for this

    # Belt-and-suspenders: reject other-state k12 zones that slipped through phase 2
    state_match = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state_match and state_match.group(1) != 'ny':
        return 0, "low", is_hosting, False, False

    if is_hosting:
        whois_match = False
    else:
        is_edu_asn   = (asn_type == "education")
        allowed_isps = fcc_providers.get(school_name, [])
        whois_match  = is_edu_asn or org_matches_provider(org_name, allowed_isps, brand_extractor)

    if is_hosting or not fcc_providers.get(school_name):
        fcc_match = False
    else:
        fcc_match = org_matches_provider(org_name, fcc_providers[school_name], brand_extractor)

    score = sum([dns_match, strong_dns_match, whois_match, fcc_match])

    if strong_dns_match:
        label = "high" if score >= 2 else "medium"
    else:
        label = "high" if score >= 3 else "medium" if score >= 2 else "low"

    return score, label, is_hosting, whois_match, fcc_match


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    load_ipinfo_asn(IPINFO_ASN_FILE)
    fcc_providers = load_fcc_providers()

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

        asn, org_name, asn_type = lookup_ip_asn(ip)
        hostname                = row.get("hostname", "")
        strong_dns_match        = (row["match_type"] == "match")
        ny_k12                  = bool(re.search(r'\.k12\.ny\.us', hostname.lower()))

        score, confidence, is_hosting, whois_match, fcc_match = score_ip(
            hostname, asn, org_name, asn_type, school, fcc_providers, brand_extractor,
            strong_dns_match=strong_dns_match,
        )

        results.append({
            "ip_address":       ip,
            "school_name":      school,
            "hostname":         hostname,
            "phase2_match":     row["match_type"],
            "asn":              asn,
            "whois_org":        org_name,
            "is_hosting":       "yes" if is_hosting else "no",
            "strong_dns_match": "yes" if strong_dns_match else "no",
            "ny_k12_domain":    "yes" if ny_k12 else "no",
            "whois_match":      "yes" if whois_match else "no",
            "fcc_match":        "yes" if fcc_match else "no",
            "score":            score,
            "confidence":       confidence,
            "distance_km":      row.get("distance_km", ""),
        })

        print(f"{i}/{len(candidates)}  {ip:<18}  {org_name[:30]:<30}  "
              f"fcc={'yes' if fcc_match else 'no'}  score={score}  [{confidence}]")

    # Dedup by ip_address: same IP can match multiple nearby schools.
    # Keep highest score; break ties preferring strong_dns_match.
    ip_best = {}
    for r in results:
        ip = r["ip_address"]
        if ip not in ip_best:
            ip_best[ip] = r
        else:
            prev = ip_best[ip]
            if (r["score"] > prev["score"] or
                    (r["score"] == prev["score"] and
                     r["strong_dns_match"] == "yes" and prev["strong_dns_match"] != "yes")):
                ip_best[ip] = r
    dedup_count = len(results) - len(ip_best)
    if dedup_count:
        print(f"Deduplicated {dedup_count} IPs that appeared under multiple schools")
    results = list(ip_best.values())

    results.sort(key=lambda r: (r["school_name"], -r["score"]))

    fieldnames = [
        "ip_address", "school_name", "hostname", "phase2_match",
        "asn", "whois_org", "is_hosting", "strong_dns_match", "ny_k12_domain",
        "whois_match", "fcc_match", "score", "confidence", "distance_km",
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
