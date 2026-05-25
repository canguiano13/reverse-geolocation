import csv
import time
import warnings
import re
import ipaddress
from ipwhois import IPWhois

warnings.filterwarnings("ignore", category=UserWarning)
INPUT_FILE = "phase2_filtered.csv"
OUTPUT_FILE = "phase3_final_results.csv"
DELAY = 0.5  
#TODO REPLACE later
# --- MOCK FCC DATA ---
FCC_PROVIDERS_MOCK = {
    "Davis High School": ["Comcast", "AT&T", "Charter"],
    "Armijo High School": ["Comcast", "AT&T", "Frontier"],
    "Napa High School": ["Comcast", "AT&T"],
    "Johns Elementary": ["Charter", "Windstream"],
    "Lincoln High School": ["Comcast", "AT&T"],
    "Oak Tree Middle": ["Comcast", "Charter", "Cox"],
    "Cherry Creek High School": ["Comcast", "CenturyLink", "Xfinity"]
}

#TODO replace with real FCC data, and implement a more robust matching system that can handle variations in provider names (e.g., "Comcast" vs "Comcast Cable")
#Also replace education keywords with 2026-03_categorized_ases.csv data, make sure to extract edu related ASes 
HOSTING_KEYWORDS = ["cloudflare", "google", "amazon", "aws", "microsoft", "azure", "fastly", "akamai", "digitalocean"]
EDUCATIONAL_KEYWORDS = ["cenic", "university", "education", "school", "research", "unified", "merit"]

# Cache to store WHOIS results by /24 prefix to reduce API calls
prefix_cache = {}

def get_24_prefix(ip_str):
    """Converts an IP to its /24 string representation."""
    try:
        network = ipaddress.IPv4Network(f"{ip_str}/24", strict=False)
        return str(network.network_address)
    except ValueError:
        return None

#TODO Might not be good enough, maybe look at alt 
def normalize_org_name(name):
    """
    Simulates the paper's TF-IDF extraction by stripping generic corporate jargon 
    to isolate the canonical brand name.
    """
    if not name or name in ["Error", "Unknown"]:
        return ""
    
    name = name.lower()
    # Strip generic terms that would drag down a TF-IDF score
    generic_terms = [r'\binc\b', r'\bllc\b', r'\bcorp\b', r'\bcorporation\b', r'\bco\b', r'\bltd\b', r'\bnetwork\b', r'\bnetworks\b', r'\bcommunications\b', r'\bservices\b', r'\btelecom\b', r'\bisp\b']
    for term in generic_terms:
        name = re.sub(term, '', name)
    
    # Remove punctuation and extra whitespace
    name = re.sub(r'[^\w\s]', '', name)
    return " ".join(name.split())

def get_whois_data(ip):
    """Extracts ASN and Org Name, utilizing a /24 prefix cache."""
    prefix = get_24_prefix(ip)
    if not prefix:
        return "Error", "Error"

    if prefix in prefix_cache:
        return prefix_cache[prefix]

    try:
        obj = IPWhois(ip)
        results = obj.lookup_rdap(depth=1)
        asn = results.get('asn', 'Unknown')
        org_name = results.get('asn_description', 'Unknown')
        
        prefix_cache[prefix] = (asn, org_name)
        time.sleep(DELAY) # Only delay on fresh network calls
        return asn, org_name
    except Exception as e:
        print(f"Error looking up {ip} (/24: {prefix}): {e}")
        prefix_cache[prefix] = ("Error", "Error")
        return "Error", "Error"

def check_fcc_match(school_name, normalized_whois):
    if not normalized_whois:
        return False
        
    allowed_providers = FCC_PROVIDERS_MOCK.get(school_name, [])
    
    for provider in allowed_providers:
        norm_provider = normalize_org_name(provider)
        if norm_provider in normalized_whois or normalized_whois in norm_provider:
            return True
            
    return False

if __name__ == "__main__":
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    results = []

    for i, row in enumerate(rows, 1):
        ip = row["ip_address"].strip()
        school = row["school_name"].strip()
        rdns_match_type = row["match_type"].strip()

        # 1. WHOIS Data (Cached by /24)
        asn, org_name = get_whois_data(ip)
        row["asn"] = asn
        row["whois_org_name"] = org_name
        
        norm_org = normalize_org_name(org_name)

        # 2. Filtering Logic
        is_hosting = any(hw in norm_org for hw in HOSTING_KEYWORDS)
        is_ed = any(ew in norm_org for ew in EDUCATIONAL_KEYWORDS)
        fcc_matched = check_fcc_match(school, norm_org)

        # If it is a hosting provider, it automatically fails the FCC/Identity checks
        if is_hosting:
            fcc_match_bool = False
            whois_match_bool = False
        else:
            fcc_match_bool = fcc_matched or is_ed 
            whois_match_bool = fcc_match_bool 
        
        rdns_match_bool = rdns_match_type in ["match", "partial_match"]
        
        row["rdns_match"] = rdns_match_bool
        row["whois_match"] = whois_match_bool
        row["fcc_nbm_match"] = fcc_match_bool
        row["is_hosting_provider"] = is_hosting

        # 3. Confidence Metric
        confidence = sum([rdns_match_bool, whois_match_bool, fcc_match_bool])
        row["confidence_score"] = confidence
        row["successfully_identified"] = confidence >= 2

        results.append(row)
        print(f"{i} | IP: {ip:<15} | Org: {str(org_name)[:20]:<20} | Host: {str(is_hosting):<5} | Ed: {str(is_ed):<5} | FCC: {str(fcc_matched):<5} | ID'd: {row['successfully_identified']}")

    # 4. Write Output
    fieldnames = list(results[0].keys())
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)