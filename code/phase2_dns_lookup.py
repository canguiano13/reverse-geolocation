"""Phase 2: reverse DNS over Phase 1 candidates.

Probe-first: check 5 IPs per /24 before expanding the block. Checkpoints
per school so a killed run can resume.

ISI Verfploeter hitlist (when present) provides two speed-ups:
  1. Confirmed-dead /24s are skipped entirely before any DNS probe.
  2. Known-responsive IPs are tried first; fall back to first-N scan only
     for blocks absent from the hitlist (new allocations, ICMP-filtered nets).
"""

import bz2
import csv
import gc
import glob
import gzip
import ipaddress
import os
import re
import socket
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
import numpy as np
import dns.resolver
import dns.reversename

INPUT_FILE    = "data/outputs/phase1_candidates.csv"
OUTPUT_FILE   = "data/outputs/phase2_filtered.csv"
ANYCAST_FILE  = "data/inputs/ipinfo/anycast_ranges_only.csv.gz"
ISI_HITLIST_GLOB = "data/inputs/isi/internet_address_verfploeter_hitlist_*.fsdb.bz2"

TIMEOUT     = 1.0
WORKERS     = 8
MAX_CIDRS   = 500  # schools beyond this are GeoLite2 ISP-aggregate noise
N_PROBE_IPS = 5
PROBE_BATCH = 25
IP_BATCH    = 200

socket.setdefaulttimeout(2.0)

# macOS mDNSResponder throttles after ~30 schools of PTR queries and silently
# drops requests, so use public resolvers directly.
_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
_resolver.timeout = 1.0
_resolver.lifetime = 2.0

STOP_WORDS = {
    "a", "an", "the", "of", "and", "in", "at", "for",
    "school", "schools", "district", "unified", "elementary", "middle",
    "high", "junior", "senior", "jr", "sr", "academy", "academies",
    "public", "charter", "magnet", "preparatory", "prep", "independent",
    "international", "institute", "center", "campus",
    "north", "south", "east", "west",
    "long", "island", "new", "york",
}

DOMAIN_NOISE = STOP_WORDS | {
    "central", "city", "county", "board", "education", "educational",
    "technology", "service", "information", "regional", "common",
    "consolidated", "community",
}

DOMAIN_TLDS    = [".edu", ".org", ".net", ".k12.ny.us"]
DOMAIN_SUFFIXES = ["", "schools", "csd", "ufsd", "k12", "sd"]

# "sch" excluded - matched a Greek education domain (.att.sch.gr).
K12_INDICATORS = {
    "k12", "school", "schools", "district", "unified", "elementary",
    "middle", "high", "schl",
    "isd", "usd", "cusd", "pusd",
    "acad", "academy",
    "csd", "ufsd", "boces",
}

# ── ISI Verfploeter hitlist ───────────────────────────────────────────────
# Loaded once at startup. Two numpy uint32 arrays, each sorted by /24 network
# address, let us binary-search in ~39 MB instead of a Python set (~600 MB).
#
#   _hl_resp_net  : /24 network addresses that have at least one responsive IP
#   _hl_resp_oct  : first responsive last-octet for the matching /24
#   _hl_dead_net  : /24 network addresses confirmed non-responsive (all '-')
#
# Probe strategy per /24:
#   dead  -> skip immediately (return False from probe_block)
#   alive -> probe the hitlist IP first, then fill up to N_PROBE_IPS if needed
#   absent-> probe first N_PROBE_IPS as before (new allocation / ICMP-blocked)

_hl_resp_net = np.array([], dtype=np.uint32)
_hl_resp_oct = np.array([], dtype=np.uint8)
_hl_dead_net = np.array([], dtype=np.uint32)


def load_hitlist(path):
    global _hl_resp_net, _hl_resp_oct, _hl_dead_net
    if not path or not os.path.exists(path):
        print(f"Hitlist: not found at {path!r}, probe pre-filter disabled")
        return

    resp_nets, resp_octs, dead_nets = [], [], []
    with bz2.open(path, 'rt', encoding='ascii') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tab = line.find('\t')
            if tab == -1:
                continue
            try:
                block_int = int(line[:tab], 16)
            except ValueError:
                continue
            octets_str = line[tab + 1:]
            if octets_str == '-':
                dead_nets.append(block_int)
            else:
                try:
                    first_oct = int(octets_str.split(',')[0], 16)
                    resp_nets.append(block_int)
                    resp_octs.append(first_oct)
                except ValueError:
                    pass

    order          = np.argsort(np.array(resp_nets, dtype=np.uint32))
    _hl_resp_net   = np.array(resp_nets, dtype=np.uint32)[order]
    _hl_resp_oct   = np.array(resp_octs, dtype=np.uint8)[order]
    _hl_dead_net   = np.sort(np.array(dead_nets, dtype=np.uint32))
    print(f"Hitlist: {len(_hl_resp_net):,} responsive /24s, "
          f"{len(_hl_dead_net):,} confirmed dead  ({os.path.basename(path)})")


def _net24_int(cidr_str):
    """Return the /24 network address as a uint32 int, or None on error."""
    try:
        return int(ipaddress.ip_network(cidr_str, strict=False).network_address) & 0xFFFFFF00
    except Exception:
        return None


def hitlist_is_dead(cidr_str):
    """True if the /24 is confirmed non-responsive in the hitlist."""
    if len(_hl_dead_net) == 0:
        return False
    key = _net24_int(cidr_str)
    if key is None:
        return False
    idx = np.searchsorted(_hl_dead_net, np.uint32(key))
    return idx < len(_hl_dead_net) and int(_hl_dead_net[idx]) == key


def hitlist_probe_ip(cidr_str):
    """Return a known-responsive IP string for this /24, or None if absent."""
    if len(_hl_resp_net) == 0:
        return None
    key = _net24_int(cidr_str)
    if key is None:
        return None
    idx = np.searchsorted(_hl_resp_net, np.uint32(key))
    if idx < len(_hl_resp_net) and int(_hl_resp_net[idx]) == key:
        return str(ipaddress.ip_address(key + int(_hl_resp_oct[idx])))
    return None


# Anycast ranges loaded at startup; used to skip PTR lookups on anycast IPs.
_anycast_ranges = []  # sorted list of (start_int, end_int)


def load_anycast_ranges(path):
    global _anycast_ranges
    ranges = []
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or '-' not in line:
                    continue
                parts = line.split('-')
                if len(parts) != 2:
                    continue
                try:
                    start = int(ipaddress.ip_address(parts[0]))
                    end   = int(ipaddress.ip_address(parts[1]))
                    ranges.append((start, end))
                except ValueError:
                    continue
        ranges.sort()
        _anycast_ranges = ranges
        print(f"Loaded {len(_anycast_ranges)} anycast ranges")
    except FileNotFoundError:
        print(f"Warning: {path} not found, anycast filtering disabled")


def is_anycast(ip_str):
    if not _anycast_ranges:
        return False
    try:
        ip_int = int(ipaddress.ip_address(ip_str))
    except ValueError:
        return False
    lo, hi = 0, len(_anycast_ranges)
    while lo < hi:
        mid = (lo + hi) // 2
        if _anycast_ranges[mid][0] <= ip_int:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return False
    start, end = _anycast_ranges[lo - 1]
    return start <= ip_int <= end


# Only used to categorize rejections in the tally, not for filtering.
HOSTING_KEYWORDS = {
    "cloudflare", "amazonaws", "googleusercontent", "googleapis",
    "akamai", "fastly", "azure", "compute-1", "ec2",
    "linode", "digitalocean", "ovh", "hetzner", "vultr",
}


def get_keywords(school_name):
    cleaned = re.sub(r"[^a-z0-9]", " ", school_name.lower())
    return [w for w in cleaned.split() if w not in STOP_WORDS and len(w) >= 3]


def generate_domain_candidates(school_name):
    tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", school_name.lower()).split()
              if t not in DOMAIN_NOISE and len(t) >= 3]
    if not tokens:
        return set()

    bases = {
        tokens[0],
        "".join(tokens),
        "".join(t[0] for t in tokens),
    }
    if len(tokens) >= 2:
        bases.add(tokens[0] + tokens[1])

    candidates = set()
    for base in bases:
        if len(base) < 2:
            continue
        for suffix in DOMAIN_SUFFIXES:
            for tld in DOMAIN_TLDS:
                candidates.add(f"{base}{suffix}{tld}")

    return candidates


def reverse_dns(ip):
    try:
        rev = dns.reversename.from_address(ip)
        return str(_resolver.resolve(rev, "PTR")[0]).rstrip(".")
    except Exception:
        return None


def has_k12_indicator(hostname):
    return any(re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", hostname)
               for kw in K12_INDICATORS)


def classify(hostname, keywords, domain_candidates=None):
    if hostname is None:
        return "no_record"
    h = hostname.lower()

    # Reject confirmed out-of-state k12 zones
    state = re.search(r'\.k12\.([a-z]{2})\.us', h)
    if state and state.group(1) != 'ny':
        return "no_match_other_state"

    # Require subdomain boundary to avoid "notsomw.k12.ny.us" matching "mw.k12.ny.us"
    if domain_candidates and any(h == d or h.endswith("." + d) for d in domain_candidates):
        return "match"

    if sum(1 for kw in keywords if kw in h) >= 2:
        return "match"

    if has_k12_indicator(h):
        return "partial_match"

    if any(kw in h for kw in HOSTING_KEYWORDS):
        return "no_match_cloud"

    return "no_match_other"


def probe_block(cidr, keywords, domain_candidates):
    """Return True if any probe IP in this /24 has a matching PTR record.

    Uses ISI hitlist when available to pick a better first probe IP.
    We do NOT skip dead blocks: the hitlist measures ICMP responsiveness,
    but school networks routinely block ICMP while still having valid PTR
    records.  Skipping dead blocks causes severe recall loss for schools.

    Hitlist benefit retained: when the hitlist knows a responsive IP for
    this /24, we probe that IP first (more likely to have a PTR record),
    then fill remaining probe slots from the start of the host range.
    Blocks absent from the hitlist fall back to first-N_PROBE_IPS probing.
    """
    # Sequential probes per block - nested ThreadPoolExecutors previously caused OOM.
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        if not isinstance(net, ipaddress.IPv4Network):
            return False

        # Build probe list: hitlist IP first (if known), then first-N fallback.
        hl_ip    = hitlist_probe_ip(cidr)
        all_host = [str(h) for h in net.hosts()]
        if not all_host:
            return False

        if hl_ip and hl_ip in all_host:
            probe_ips = [hl_ip] + [ip for ip in all_host[:N_PROBE_IPS] if ip != hl_ip]
        else:
            probe_ips = all_host[:N_PROBE_IPS]

        for ip in probe_ips:
            host = reverse_dns(ip)
            if host and classify(host, keywords, domain_candidates) in ("match", "partial_match"):
                return True
        return False
    except Exception:
        return False


def check_ip(ip, school_name, keywords, domain_candidates):
    if is_anycast(ip):
        return {"ip_address": ip, "school_name": school_name, "district_name": "",
                "hostname": "", "match_type": "no_match_anycast"}
    hostname = reverse_dns(ip)
    return {
        "ip_address": ip,
        "school_name": school_name,
        "district_name": "",
        "hostname": hostname or "",
        "match_type": classify(hostname, keywords, domain_candidates),
    }


def run(input_file=INPUT_FILE, output_file=OUTPUT_FILE, force_fresh=False):
    hl_path = next(iter(sorted(glob.glob(ISI_HITLIST_GLOB))), None)
    load_hitlist(hl_path)
    load_anycast_ranges(ANYCAST_FILE)
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

    cidr_to_distance = {row["cidr"].strip(): row.get("distance_km", "")
                        for row in rows}

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

    probe_batch_timeout = (PROBE_BATCH / WORKERS) * (N_PROBE_IPS * _resolver.lifetime) + 10
    ip_batch_timeout    = (IP_BATCH / WORKERS) * _resolver.lifetime + 10

    mode = "a" if completed else "w"
    with open(output_file, mode, newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=[
            "ip_address", "school_name", "district_name", "hostname", "match_type", "distance_km"
        ])
        if not completed:
            writer.writeheader()
        out_f.flush()

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for idx, school in enumerate(remaining, 1):
                keywords          = get_keywords(school)
                domain_candidates = generate_domain_candidates(school)
                print(f"[{idx}/{len(remaining)}] {school[:45]} ...", flush=True)

                all_cidrs = blocks_per_school[school]

                # Probe phase, batched. Wall-clock timeout per batch so a hung
                # DNS query can't stall everything.
                promising = []
                for start in range(0, max(len(all_cidrs), 1), PROBE_BATCH):
                    batch = all_cidrs[start:start + PROBE_BATCH]
                    futures = {pool.submit(probe_block, c, keywords, domain_candidates): c for c in batch}
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
                ip_to_distance = {}
                for cidr in promising:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        if isinstance(net, ipaddress.IPv4Network):
                            d = cidr_to_distance.get(cidr, "")
                            for ip in net.hosts():
                                ip_str = str(ip)
                                ips.append(ip_str)
                                ip_to_distance[ip_str] = d
                    except ValueError:
                        continue
                print(f"  {len(ips)} IPs to check", flush=True)

                done = 0
                for start in range(0, max(len(ips), 1), IP_BATCH):
                    batch = ips[start:start + IP_BATCH]
                    futures = {pool.submit(check_ip, ip, school, keywords, domain_candidates): ip for ip in batch}
                    try:
                        for f in as_completed(futures, timeout=ip_batch_timeout):
                            r = f.result()
                            r["distance_km"] = ip_to_distance.get(r["ip_address"], "")
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

                # PTR cache grows fast; flush per school.
                try:
                    if _resolver.cache:
                        _resolver.cache.flush()
                except Exception:
                    pass
                gc.collect()

    print(f"\nDone -> {output_file}")
    print(f"match: {tally['match']}  partial: {tally['partial_match']}  "
          f"cloud: {tally['no_match_cloud']}  "
          f"anycast: {tally['no_match_anycast']}  "
          f"other-state-k12: {tally['no_match_other_state']}  "
          f"no_match: {tally['no_match_other']}  "
          f"no_record: {tally['no_record']}")


if __name__ == "__main__":
    run()
