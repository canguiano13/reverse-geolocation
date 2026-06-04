"""Phase 0: ARIN WHOIS discovery - search for NY school orgs and pull their IP blocks."""

import csv
import ipaddress
import time
import requests

OUTPUT_FILE = "data/outputs/phase0_arin.csv"
HEADERS     = {"Accept": "application/json"}
SLEEP       = 0.8
RETRY       = 3

# NY-specific terms (boces, ufsd) skip state verification.
# Generic terms get checked against the org's state field.
KEYWORDS = [
    ("union free school",        True),
    ("boces",                    True),
    ("enlarged city school",     True),
    ("central school district",  False),
    ("city school district",     False),
    ("board of education",       False),
    ("common school district",   False),
]


def arin_get(url):
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
    data = arin_get(f"https://whois.arin.net/rest/orgs;name=*{keyword.replace(' ', '%20')}*")
    if not data:
        return []
    orgs = data.get("orgs", {}).get("orgRef", [])
    return [orgs] if isinstance(orgs, dict) else orgs


def is_ny_org(handle):
    """True if org is in NY, False if confirmed not-NY, None if lookup failed."""
    data = arin_get(f"https://whois.arin.net/rest/org/{handle}")
    if not data:
        return None
    org   = data.get("org", {})
    state = (org.get("iso3166-2") or {}).get("$", "") or (org.get("state") or {}).get("$", "")
    return "NY" in state.upper()


def get_networks(handle):
    data = arin_get(f"https://whois.arin.net/rest/org/{handle}/nets")
    if not data:
        return []
    nets = data.get("nets", {}).get("netRef", [])
    return [nets] if isinstance(nets, dict) else nets


def net_to_cidrs(net_ref):
    """Convert ARIN net (start + end IP) to IPv4 CIDR notation."""
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

            # For generic keywords, verify NY. None means lookup failed, keep it.
            if not ny_specific:
                if is_ny_org(handle) is False:
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
                print(f"  ok  {name:<55} {len(new_cidrs)} blocks")

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cidr", "school_name", "org_handle"])
        writer.writeheader()
        writer.writerows(results)

    n_orgs = len({r["org_handle"] for r in results})
    print(f"\nDone. {len(results)} IP blocks from {n_orgs} NY school orgs -> {output_file}")


if __name__ == "__main__":
    run()
