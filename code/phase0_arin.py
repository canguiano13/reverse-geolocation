"""
Phase 0 — ARIN WHOIS Discovery
---------------------------------
The professor's primary method from the Waldo paper, applied to schools.

The paper searched ARIN for organizations whose name contains "library" →
got their registered IP blocks → high-confidence library IPs.

We do the same for schools: search ARIN for organizations whose name
contains school-related keywords → get their registered IP blocks.

This finds IPs that Phase 1 (GeoLite2) would NEVER find, because they
don't depend on geolocation accuracy at all — the school district literally
registered these blocks in ARIN under their name.

Output: data/outputs/phase0_arin.csv  (cidr, school_name, org_handle)
"""

import csv
import ipaddress
import time
import requests

OUTPUT_FILE = "data/outputs/phase0_arin.csv"
HEADERS     = {"Accept": "application/json"}
SLEEP       = 0.4   # seconds between ARIN API calls — be polite

# Search keywords and whether they are NY-specific.
# NY-specific terms (UFSD, BOCES) don't need state filtering.
# Generic terms (board of education) do — we check via API.
KEYWORDS = [
    ("union free school",       True),   # (search term, ny_specific)
    ("boces",                   True),
    ("central school district", False),
    ("city school district",    False),
    ("board of education",      False),
]


def arin_get(url):
    """GET a URL from ARIN's REST API. Returns parsed JSON or None."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def search_orgs(keyword):
    """Return all ARIN orgs whose name contains keyword."""
    data = arin_get(f"https://whois.arin.net/rest/orgs;name=*{keyword.replace(' ', '+')}*")
    if not data:
        return []
    orgs = data.get("orgs", {}).get("orgRef", [])
    return [orgs] if isinstance(orgs, dict) else orgs


def is_ny_org(handle):
    """Return True if this ARIN org is registered in New York state."""
    data = arin_get(f"https://whois.arin.net/rest/org/{handle}")
    if not data:
        return False
    org   = data.get("org", {})
    state = (org.get("iso3166-2") or {}).get("$", "")
    if not state:
        state = (org.get("state") or {}).get("$", "")
    return "NY" in state.upper()


def get_networks(handle):
    """Return all IP network blocks registered to an ARIN org."""
    data = arin_get(f"https://whois.arin.net/rest/org/{handle}/nets")
    if not data:
        return []
    nets = data.get("nets", {}).get("netRef", [])
    return [nets] if isinstance(nets, dict) else nets


def net_to_cidrs(net_ref):
    """
    Convert an ARIN network reference to CIDR notation.
    ARIN gives us start + end address; Python converts that to CIDR.
    Only returns IPv4 networks.
    """
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

    seen_orgs  = set()   # avoid re-looking up the same org twice
    seen_cidrs = set()   # avoid duplicate blocks in output
    results    = []

    print("Searching ARIN for NY school organizations...\n")

    for keyword, ny_specific in KEYWORDS:
        print(f"Keyword: '{keyword}'")
        orgs = search_orgs(keyword)
        print(f"  {len(orgs)} orgs found in ARIN")
        time.sleep(SLEEP)

        for org in orgs:
            handle = org.get("@handle", "")
            name   = org.get("@name",   "")

            if handle in seen_orgs:
                continue
            seen_orgs.add(handle)

            # For non-NY-specific keywords, verify this org is in New York
            if not ny_specific:
                if not is_ny_org(handle):
                    continue
                time.sleep(SLEEP)

            # Get all IP blocks registered to this org
            networks = get_networks(handle)
            time.sleep(SLEEP)
            if not networks:
                continue

            # Convert each network to CIDR and save
            new_cidrs = []
            for net in networks:
                for cidr in net_to_cidrs(net):
                    if cidr not in seen_cidrs:
                        seen_cidrs.add(cidr)
                        new_cidrs.append(cidr)
                        results.append({
                            "cidr":        cidr,
                            "school_name": name,
                            "org_handle":  handle,
                        })

            if new_cidrs:
                print(f"  ✓ {name:<55} {len(new_cidrs)} blocks")

    # Save results
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cidr", "school_name", "org_handle"])
        writer.writeheader()
        writer.writerows(results)

    n_orgs = len({r["org_handle"] for r in results})
    print(f"\nDone. {len(results)} IP blocks from {n_orgs} NY school organizations")
    print(f"Written to {output_file}")


if __name__ == "__main__":
    run()
