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
MAX_CIDRS   = 2000

STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
}

K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "sch", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
    "csd", "ufsd", "boces",
}


def extract_keywords(name):
    cleaned = re.sub(r"[^a-z0-9]", " ", name.lower())
    return [t for t in cleaned.split() if t not in STOP_WORDS and len(t) >= 3]


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

    hostname_l = hostname.lower()

    # precision fix 1: out-of-state k12 domains are never a match for NY schools
    # ex: .k12.sc.us = South Carolina, .k12.nh.us = New Hampshire
    m = re.search(r'\.k12\.([a-z]{2})\.us', hostname_l)
    if m and m.group(1) != 'ny':
        return "no_match"

    # precision fix 2: require at least 2 school keywords to match
    # prevents single-word false positives like "new" matching "New Hyde Park"
    # or "howard" matching a printing company called Howard Press
    matching = [kw for kw in school_kws if kw in hostname_l]
    if len(matching) >= 2:
        return "match"

    if has_k12_indicator(hostname_l):
        return "partial_match"

    return "no_match"


def lookup(ip_str, school, school_kws):
    hostname   = reverse_dns(ip_str)
    match_type = classify(hostname, school_kws)
    return {"ip_address": ip_str, "school_name": school, "district_name": "", "hostname": hostname or "", "match_type": match_type}


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    cidr_counts = defaultdict(int)
    for row in rows:
        cidr_counts[row["school_name"].strip()] += 1

    skipped = [s for s, c in cidr_counts.items() if c > MAX_CIDRS]
    if skipped:
        print(f"Skipping {len(skipped)} schools with >{MAX_CIDRS} blocks")

    by_school = defaultdict(list)
    for row in rows:
        school = row["school_name"].strip()
        if cidr_counts[school] > MAX_CIDRS:
            continue
        by_school[school].append(row["cidr"].strip())

    schools_to_run = list(by_school.keys())
    total_schools  = len(schools_to_run)
    print(f"Processing {total_schools} schools")

    counts    = defaultdict(int)
    completed = 0

    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["ip_address", "school_name", "district_name", "hostname", "match_type"])
        writer.writeheader()
        out_f.flush()

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            for s_idx, school in enumerate(schools_to_run, 1):
                school_kws = extract_keywords(school)
                print(f"[{s_idx}/{total_schools}] {school[:45]} ...", flush=True)

                jobs = []
                for cidr in by_school[school]:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        if not isinstance(net, ipaddress.IPv4Network):
                            continue
                        for ip in net.hosts():
                            jobs.append(str(ip))
                    except ValueError:
                        continue

                print(f"  expanding done: {len(jobs)} IPs to check", flush=True)

                futures = {executor.submit(lookup, ip, school, school_kws): ip for ip in jobs}
                school_done = 0
                for future in as_completed(futures):
                    result = future.result()
                    match_type = result["match_type"]
                    counts[match_type] += 1
                    completed += 1
                    school_done += 1

                    if match_type in ("match", "partial_match"):
                        writer.writerow(result)
                        out_f.flush()
                        print(f"  {result['ip_address']}  [{match_type}]  {result['hostname']}", flush=True)

                    if school_done % 10000 == 0:
                        print(f"  ... {school_done}/{len(jobs)} IPs checked for this school", flush=True)

                print(f"  done. total checked so far: {completed}", flush=True)

    print(f"\nDone. Results written to {output_file}")
    print(f"match: {counts['match']}  partial: {counts['partial_match']}  no_match: {counts['no_match']}  no_record: {counts['no_record']}")


if __name__ == "__main__":
    run()
