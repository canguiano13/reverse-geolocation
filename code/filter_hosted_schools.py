import csv
import re
import dns.resolver
import dns.reversename

INPUT_FILE  = "data/schools_selected.csv"
OUTPUT_FILE = "data/schools_filtered.csv"
TIMEOUT     = 5.0

# if a school's website resolves through one of these, it's cloud hosted
CLOUD_PROVIDERS = {
    "cloudflare":  ["cloudflare.com", "cloudflare.net"],
    "aws":         ["cloudfront.net", "amazonaws.com", "awsglobalaccelerator.com"],
    "google":      ["googleusercontent.com", "googleapis.com", "1e100.net"],
    "azure":       ["azure.com", "azurewebsites.net", "azureedge.net", "windows.net"],
    "akamai":      ["akamai.net", "akamaized.net", "akamaiedge.net"],
    "fastly":      ["fastly.net", "fastlylb.net"],
    "edlio":       ["edlio.com"],
    "finalsite":   ["finalsite.com", "fsi.io"],
    "schoolwires": ["schoolwires.net"],
    "apptegy":     ["apptegy.com"],
    "wix":         ["wix.com", "wixdns.net"],
    "squarespace": ["squarespace.com", "squarespacedns.com"],
}


def clean_domain(url):
    domain = re.sub(r"https?://", "", url.strip().lower())
    return domain.split("/")[0]


# follow the chain of redirects for a domain
def get_cname_chain(domain):
    chain = []
    target = domain
    for _ in range(10):
        try:
            answers = dns.resolver.resolve(target, "CNAME", lifetime=TIMEOUT)
            cname = str(answers[0].target).rstrip(".")
            chain.append(cname)
            target = cname
        except Exception:
            break
    return chain


def get_ip(domain):
    try:
        answers = dns.resolver.resolve(domain, "A", lifetime=TIMEOUT)
        return str(answers[0])
    except Exception:
        return None


def get_ptr(ip):
    try:
        rev = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev, "PTR", lifetime=TIMEOUT)
        return str(answers[0]).rstrip(".").lower()
    except Exception:
        return ""


def check_cloud(dns_strings):
    combined = " ".join(dns_strings).lower()
    for provider, patterns in CLOUD_PROVIDERS.items():
        for pattern in patterns:
            if pattern in combined:
                return provider
    return None


def classify_hosting(url):
    domain = clean_domain(url)
    if not domain:
        return "unknown", "", "", "", ""

    cnames   = get_cname_chain(domain)
    ip       = get_ip(domain)
    ptr      = get_ptr(ip) if ip else ""
    provider = check_cloud(cnames + ([ptr] if ptr else []))

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
        school = row["school_name"].strip()
        url    = row.get("url", "").strip()

        if not url:
            host_type, provider, ip, cnames, ptr = "unknown", "", "", "", ""
        else:
            host_type, provider, ip, cnames, ptr = classify_hosting(url)

        counts[host_type] = counts.get(host_type, 0) + 1
        results.append({
            "school_name": school,
            "url":         url,
            "ip":          ip,
            "cname_chain": cnames,
            "ptr":         ptr,
            "host_type":   host_type,
            "provider":    provider,
        })

        print(f"{i}/{total}  {school[:40]:<40}  [{host_type}]  {provider}")

    fieldnames = ["school_name", "url", "ip", "cname_chain", "ptr", "host_type", "provider"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. Results written to {OUTPUT_FILE}")
    print(f"cloud: {counts['cloud']}  local: {counts['local']}  unknown: {counts['unknown']}")
