import csv
import ipaddress
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import dns.resolver
import dns.reversename

INPUT_FILE  = "data/phase1_candidates.csv"
OUTPUT_FILE = "data/phase2_filtered.csv"
TIMEOUT     = 1.0
WORKERS     = 50
MAX_CIDRS   = 1000  # skip schools with too many IP blocks (usually dense urban areas)

# words to ignore when pulling keywords out of a school name
STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
}

# words in a hostname that suggest it belongs to a school
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "sch", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
}


def extract_keywords(name):
    cleaned = re.sub(r"[^a-z0-9]", " ", name.lower())
    return [t for t in cleaned.split() if t not in STOP_WORDS and len(t) >= 3]


# get what hostname is registered to this IP
def reverse_dns(ip):
    try:
        rev = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev, "PTR", lifetime=TIMEOUT)
        return str(answers[0]).rstrip(".")
    except Exception:
        return None


def has_k12_indicator(hostname):
    for kw in K12_INDICATORS:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname):
            return True
    return False


def classify(hostname, school_kws):
    if hostname is None:
        return "no_record"
    if any(kw in hostname for kw in school_kws):
        return "match"
    if has_k12_indicator(hostname):
        return "partial_match"
    return "no_match"


def lookup(ip_str, school, school_kws):
    hostname   = reverse_dns(ip_str)
    match_type = classify(hostname, school_kws)
    return {"ip_address": ip_str, "school_name": school, "district_name": "", "hostname": hostname or "", "match_type": match_type}


if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # count how many blocks each school has, skip ones with too many
    cidr_counts = defaultdict(int)
    for row in rows:
        cidr_counts[row["school_name"].strip()] += 1

    skipped = [s for s, c in cidr_counts.items() if c > MAX_CIDRS]
    if skipped:
        print(f"Skipping {len(skipped)} schools with >{MAX_CIDRS} blocks")

    # expand each IP block into individual IPs
    jobs = []
    for row in rows:
        school = row["school_name"].strip()
        if cidr_counts[school] > MAX_CIDRS:
            continue
        school_kws = extract_keywords(school)
        try:
            for ip in ipaddress.ip_network(row["cidr"].strip(), strict=False).hosts():
                jobs.append((str(ip), school, school_kws))
        except ValueError:
            continue

    total = len(jobs)
    print(f"Processing {len(cidr_counts) - len(skipped)} schools → {total} IPs to check")

    counts    = defaultdict(int)
    completed = 0

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["ip_address", "school_name", "district_name", "hostname", "match_type"])
        writer.writeheader()

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(lookup, ip, school, kws): ip for ip, school, kws in jobs}

            for future in as_completed(futures):
                result = future.result()
                match_type = result["match_type"]
                counts[match_type] += 1
                completed += 1

                if match_type in ("match", "partial_match"):
                    writer.writerow(result)
                    print(f"  {result['ip_address']}  [{match_type}]  {result['hostname']}")

                if completed % 10000 == 0 or completed == total:
                    print(f"Progress: {completed}/{total} IPs checked")

    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"match: {counts['match']}  partial: {counts['partial_match']}  no_match: {counts['no_match']}  no_record: {counts['no_record']}")
