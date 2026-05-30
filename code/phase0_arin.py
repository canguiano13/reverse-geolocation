"""
Phase 0 — ARIN WHOIS Discovery

Search ARIN for organizations with school-related names and pull all IP blocks
they own. Same method as the "Where's Waldo Library?" paper, applied to schools.

Output: phase0_arin.csv  (cidr, school_name, org_handle)
"""

import csv
import ipaddress
import time
import requests

OUTPUT_FILE = "data/outputs/phase0_arin.csv"
HEADERS     = {"Accept": "application/json"}
SLEEP       = 0.8   # seconds between API calls
RETRY       = 3

# Search terms. NY-specific ones (boces, ufsd) don't need state filtering.
# Generic ones (board of education) need state verification.
KEYWORDS = [
    ("union free school",        True),   # (search term, ny_specific)
    ("boces",                    True),
    ("enlarged city school",     True),
    ("central school district",  False),
    ("city school district",     False),
    ("board of education",       False),
    ("common school district",   False),
]


def arin_get(url):
    """GET from the ARIN API. Retries on failure."""
    for attempt in range(RETRY):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
        except Exception:
            pass
        time.sleep(SLEEP * (attempt + 1))
    return None


def search_orgs(keyword):
    """Return all ARIN orgs whose name contains the keyword."""
    data = arin_get(f"https://whois.arin.net/rest/orgs;name=*{keyword.replace(' ', '%20')}*")
    if not data:
        return []
    orgs = data.get("orgs", {}).get("orgRef", [])
    return [orgs] if isinstance(orgs, dict) else orgs


def is_ny_org(handle):
    """Return True if this org is in NY state, False if confirmed not-NY, None if unknown."""
    data = arin_get(f"https://whois.arin.net/rest/org/{handle}")
    if not data:
        return None
    org   = data.get("org", {})
    state = (org.get("iso3166-2") or {}).get("$", "")
    if not state:
        state = (org.get("state") or {}).get("$", "")
    return "NY" in state.upper()


def get_networks(handle):
    """Return all IP blocks registered to an ARIN org."""
    data = arin_get(f"https://whois.arin.net/rest/org/{handle}/nets")
    if not data:
        return []
    nets = data.get("nets", {}).get("netRef", [])
    return [nets] if isinstance(nets, dict) else nets


def net_to_cidrs(net_ref):
    """Convert an ARIN network (start + end IP) to CIDR notation. IPv4 only."""
    start = net_ref.get("@startAddress", "")
    end   = net_ref.get("@endAddress",   "")
    if not start or not end:
        return []
    try:
        return [
            str(n) for n in ipaddress.summarize_address_range(
                ipaddress.ip_address(start), ipaddress.ip_address(end)
            )
            if isinstance(n, ipaddress.IPv4Network)
        ]
    except Exception:
        return []


def run(output_file=OUTPUT_FILE):

    seen_orgs  = set()
    seen_cidrs = set()
    results    = []

    print("Searching ARIN for NY school organizations...\n")

    for keyword, ny_specific in KEYWORDS:
        print(f"Keyword: '{keyword}'")
        orgs = search_orgs(keyword)
        print(f"  {len(orgs)} orgs found")
        time.sleep(SLEEP)

        for org in orgs:
            handle = org.get("@handle", "")
            name   = org.get("@name",   "")

            if handle in seen_orgs:
                continue
            seen_orgs.add(handle)

            # For generic keywords, verify this org is in NY.
            # None (API failed) = keep it; False (confirmed non-NY) = skip.
            if not ny_specific:
                ny = is_ny_org(handle)
                if ny is False:
                    continue
                time.sleep(SLEEP)

            networks = get_networks(handle)
            time.sleep(SLEEP)
            if not networks:
                continue

            new_cidrs = []
            for net in networks:
                for cidr in net_to_cidrs(net):
                    if cidr not in seen_cidrs:
                        seen_cidrs.add(cidr)
                        new_cidrs.append(cidr)
                        results.append({"cidr": cidr, "school_name": name, "org_handle": handle})

            if new_cidrs:
                print(f"  ✓ {name:<55} {len(new_cidrs)} blocks")

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cidr", "school_name", "org_handle"])
        writer.writeheader()
        writer.writerows(results)

    n_orgs = len({r["org_handle"] for r in results})
    print(f"\nDone. {len(results)} IP blocks from {n_orgs} NY school organizations {output_file}")


if __name__ == "__main__":
    run()
