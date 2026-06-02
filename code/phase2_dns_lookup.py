"""Phase 2: reverse DNS over Phase 1 candidates.

Probe-first: check 5 IPs per /24 before expanding the block. Checkpoints
per school so a killed run can resume.
"""

import csv
import gc
import ipaddress
import os
import re
import socket
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
import dns.resolver
import dns.reversename

INPUT_FILE = "data/outputs/phase1_candidates.csv"
OUTPUT_FILE = "data/outputs/phase2_filtered.csv"

TIMEOUT = 1.0
WORKERS = 20
MAX_CIDRS = 500          # schools beyond this are GeoLite2 ISP-aggregate noise
N_PROBE_IPS = 5
PROBE_BATCH = 100
IP_BATCH = 500

socket.setdefaulttimeout(2.0)

# macOS mDNSResponder throttles after ~30 schools of PTR queries and silently
# drops requests, so use public resolvers directly.
_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
_resolver.timeout = 1.5
_resolver.lifetime = 3.0

STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
    "long", "island", "new", "york",
}

# "sch" excluded — matched a Greek education domain (.att.sch.gr).
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
    "csd", "ufsd", "boces",
}

# Only used to categorize rejections in the tally — not for filtering.
HOSTING_KEYWORDS = {
    "cloudflare", "amazonaws", "googleusercontent", "googleapis",
    "akamai", "fastly", "azure", "compute-1", "ec2",
    "linode", "digitalocean", "ovh", "hetzner", "vultr",
}


def get_keywords(school_name):
    cleaned = re.sub(r"[^a-z0-9]", " ", school_name.lower())
    return [w for w in cleaned.split() if w not in STOP_WORDS and len(w) >= 3]


def reverse_dns(ip):
    try:
        rev = dns.reversename.from_address(ip)
        return str(_resolver.resolve(rev, "PTR")[0]).rstrip(".")
    except Exception:
        return None


def has_k12_indicator(hostname):
    return any(re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname)
               for kw in K12_INDICATORS)


def classify(hostname, keywords):
    if hostname is None:
        return "no_record"
    h = hostname.lower()

    state = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state and state.group(1) != 'ny':
        return "no_match_other_state"

    if sum(1 for kw in keywords if kw in h) >= 2:
        return "match"

    if has_k12_indicator(h):
        return "partial_match"

    if any(kw in h for kw in HOSTING_KEYWORDS):
        return "no_match_cloud"

    return "no_match_other"


def probe_block(cidr, keywords):
    # Sequential probes per block — nested ThreadPoolExecutors here previously
    # caused OOM (WORKERS × N_PROBE_IPS threads in flight).
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
        if not hosts or not isinstance(net, ipaddress.IPv4Network):
            return False
        for ip in (str(h) for h in hosts[:N_PROBE_IPS]):
            host = reverse_dns(ip)
            if host and classify(host, keywords) in ("match", "partial_match"):
                return True
        return False
    except Exception:
        return False


def check_ip(ip, school_name, keywords):
    hostname = reverse_dns(ip)
    return {
        "ip_address": ip,
        "school_name": school_name,
        "district_name": "",
        "hostname": hostname or "",
        "match_type": classify(hostname, keywords),
    }


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, force_fresh=False):
    checkpoint_file = output_file.replace(".csv", "_checkpoint.txt")

    completed = set()
    if not force_fresh and os.path.exists(checkpoint_file) and os.path.exists(output_file):
        with open(checkpoint_file) as f:
            completed = {line.strip() for line in f if line.strip()}
        print(f"Resuming: {len(completed)} schools already done")
    elif os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    with open(input_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    block_count = defaultdict(int)
    for row in rows:
        block_count[row["school_name"].strip()] += 1

    blocks_per_school = defaultdict(list)
    for row in rows:
        s = row["school_name"].strip()
        if block_count[s] <= MAX_CIDRS:
            blocks_per_school[s].append(row["cidr"].strip())

    skipped = sum(1 for c in block_count.values() if c > MAX_CIDRS)
    if skipped:
        print(f"Skipping {skipped} schools with >{MAX_CIDRS} blocks")

    remaining = [s for s in blocks_per_school if s not in completed]
    print(f"Processing {len(remaining)} schools ({len(completed)} already done)")

    total_ips = 0
    tally = defaultdict(int)

    probe_batch_timeout = (PROBE_BATCH / WORKERS) * (N_PROBE_IPS * 2.5) + 10
    ip_batch_timeout = (IP_BATCH / WORKERS) * 2.5 + 10

    mode = "a" if completed else "w"
    with open(output_file, mode, newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=[
            "ip_address", "school_name", "district_name", "hostname", "match_type"
        ])
        if not completed:
            writer.writeheader()
        out_f.flush()

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for idx, school in enumerate(remaining, 1):
                keywords = get_keywords(school)
                print(f"[{idx}/{len(remaining)}] {school[:45]} ...", flush=True)

                all_cidrs = blocks_per_school[school]

                # Probe phase, batched. Wall-clock timeout per batch so a single
                # hung DNS query can't stall everything.
                promising = []
                for start in range(0, max(len(all_cidrs), 1), PROBE_BATCH):
                    batch = all_cidrs[start:start + PROBE_BATCH]
                    futures = {pool.submit(probe_block, c, keywords): c for c in batch}
                    try:
                        for f in as_completed(futures, timeout=probe_batch_timeout):
                            try:
                                if f.result():
                                    promising.append(futures[f])
                            except Exception:
                                pass
                    except FuturesTimeout:
                        hung = sum(1 for f in futures if not f.done())
                        print(f"  probe batch timed out ({hung} futures still running, skipping)",
                              flush=True)

                print(f"  {len(promising)}/{len(all_cidrs)} blocks passed probe", flush=True)

                ips = []
                for cidr in promising:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        if isinstance(net, ipaddress.IPv4Network):
                            ips.extend(str(ip) for ip in net.hosts())
                    except ValueError:
                        continue
                print(f"  {len(ips)} IPs to check", flush=True)

                done = 0
                for start in range(0, max(len(ips), 1), IP_BATCH):
                    batch = ips[start:start + IP_BATCH]
                    futures = {pool.submit(check_ip, ip, school, keywords): ip for ip in batch}
                    try:
                        for f in as_completed(futures, timeout=ip_batch_timeout):
                            r = f.result()
                            tally[r["match_type"]] += 1
                            total_ips += 1
                            done += 1
                            if r["match_type"] in ("match", "partial_match"):
                                writer.writerow(r)
                                out_f.flush()
                                print(f"  {r['ip_address']}  [{r['match_type']}]  {r['hostname']}",
                                      flush=True)
                            if done % 10000 == 0:
                                print(f"  ... {done}/{len(ips)} done", flush=True)
                    except FuturesTimeout:
                        hung = sum(1 for f in futures if not f.done())
                        print(f"  IP batch timed out ({hung} futures still running, skipping)",
                              flush=True)

                print(f"  done. total IPs checked: {total_ips}", flush=True)
                with open(checkpoint_file, "a") as ckpt:
                    ckpt.write(school + "\n")

                # Cache + GC reset per school — PTR cache grows fast otherwise.
                try:
                    if _resolver.cache:
                        _resolver.cache.flush()
                except Exception:
                    pass
                gc.collect()

    print(f"\nDone -> {output_file}")
    print(f"match: {tally['match']}  partial: {tally['partial_match']}  "
          f"cloud: {tally['no_match_cloud']}  "
          f"other-state-k12: {tally['no_match_other_state']}  "
          f"no_match: {tally['no_match_other']}  "
          f"no_record: {tally['no_record']}")


if __name__ == "__main__":
    run()
