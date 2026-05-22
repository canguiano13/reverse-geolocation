import csv
import re
import time
import dns.resolver
import dns.reversename

INPUT_FILE  = "schools.csv"
OUTPUT_FILE = "schools_hosting.csv"
DELAY       = 0.2
TIMEOUT     = 5.0

# CNAME/PTR patterns mapped to provider name
CLOUD_PROVIDERS = {
    "cloudflare":       ["cloudflare.com", "cloudflare.net"],
    "aws_cloudfront":   ["cloudfront.net", "amazonaws.com", "awsglobalaccelerator.com"],
    "google":           ["googleusercontent.com", "googleapis.com", "1e100.net"],
    "azure":            ["azure.com", "azurewebsites.net", "azureedge.net", "windows.net"],
    "akamai":           ["akamai.net", "akamaized.net", "akamaiedge.net"],
    "fastly":           ["fastly.net", "fastlylb.net"],
    
    "edlio":            ["edlio.com"],
    "finalsite":        ["finalsite.com", "fsi.io"],
    "schoolwires":      ["schoolwires.net"],
    "apptegy":          ["apptegy.com"],
    "wix":              ["wix.com", "wixdns.net"],
    "squarespace":      ["squarespace.com", "squarespacedns.com"],
}


# strips http/https and trailing slashes to get a bare hostname
def clean_domain(website):
    domain = re.sub(r"https?://", "", website.strip().lower())
    domain = domain.split("/")[0]
    return domain

# returns list of CNAMEs in the resolution chain for a domain
def get_cname_chain(domain):
    chain = []
    target = domain
    for _ in range(10):  # max 10 hops to avoid loops
        try:
            answers = dns.resolver.resolve(target, "CNAME", lifetime=TIMEOUT)
            cname = str(answers[0].target).rstrip(".")
            chain.append(cname)
            target = cname
        except Exception:
            break
    return chain

# returns the A record IP for a domain
def get_ip(domain):
    try:
        answers = dns.resolver.resolve(domain, "A", lifetime=TIMEOUT)
        return str(answers[0])
    except Exception:
        return None

# returns the PTR hostname for an IP
def get_ptr(ip):
    try:
        rev = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev, "PTR", lifetime=TIMEOUT)
        return str(answers[0]).rstrip(".").lower()
    except Exception:
        return ""

# checks a list of DNS strings (cnames, ptr) against cloud provider patterns
# returns (provider_name, matched_string) or (None, None)
def match_cloud_provider(dns_strings):
    combined = " ".join(dns_strings).lower()
    for provider, patterns in CLOUD_PROVIDERS.items():
        for pattern in patterns:
            if pattern in combined:
                return provider, pattern
    return None, None

# main classification: returns (host_type, provider, ip, cname_chain, ptr)
def classify_hosting(website):
    domain = clean_domain(website)
    if not domain:
        return "unknown", "", "", "", ""

    cnames = get_cname_chain(domain)
    ip     = get_ip(domain)
    ptr    = get_ptr(ip) if ip else ""

    all_dns = cnames + ([ptr] if ptr else [])
    provider, _ = match_cloud_provider(all_dns)

    if provider:
        host_type = "cloud"
    elif ip:
        host_type = "local"
    else:
        host_type = "unknown"

    return host_type, provider or "", ip or "", ", ".join(cnames), ptr


if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    print(f"Loaded {total} schools from {INPUT_FILE}")

    results = []
    counts = {"cloud": 0, "local": 0, "unknown": 0}

    for i, row in enumerate(rows, 1):
        school   = row["school_name"].strip()
        district = row["district_name"].strip()
        website  = row.get("website", "").strip()

        if not website:
            host_type, provider, ip, cnames, ptr = "unknown", "", "", "", ""
        else:
            host_type, provider, ip, cnames, ptr = classify_hosting(website)

        counts[host_type] = counts.get(host_type, 0) + 1
        results.append({
            "school_name":   school,
            "district_name": district,
            "website":       website,
            "ip":            ip,
            "cname_chain":   cnames,
            "ptr":           ptr,
            "host_type":     host_type,
            "provider":      provider,
        })

        print(f"{i}/{total}  {school[:40]:<40}  [{host_type}]  {provider}")
        time.sleep(DELAY)

    fieldnames = ["school_name", "district_name", "website", "ip", "cname_chain", "ptr", "host_type", "provider"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"cloud: {counts['cloud']}  local: {counts['local']}  unknown: {counts['unknown']}")
