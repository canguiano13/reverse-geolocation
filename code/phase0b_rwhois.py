# finds NY school IP blocks from IPinfo RWHOIS sub-allocation dataset

import csv
import gzip
import ipaddress

RWHOIS_FILE = "data/inputs/ipinfo/rwhois.csv.gz"
OUTPUT_FILE = "data/outputs/phase0b_rwhois.csv"

# NY state zip code range
NY_ZIP_MIN = 10000
NY_ZIP_MAX = 14999

# Keywords that suggest a K-12 school or district
INCLUDE_KEYWORDS = [
    "school district",
    "central school",
    "common school",
    "union free",
    "boces",
    "charter school",
    "high school",
    "middle school",
    "elementary school",
    "preparatory school",
    "academy",
    "k12",
]

# Keywords that indicate NOT a K-12 school -- exclude these
EXCLUDE_KEYWORDS = [
    "medical",
    "medicine",
    "law school",
    "university",
    "college",
    "graduate school",
    "seminary",
    "carpenters",
    "fire district",
    "justice",
    "regulatory",
    "attorney",
    "nursing",
    "dental",
    "pharmacy",
    "optometry",
    "tutoring",
    "securities",
    "gymnastics",
    "academy of music",
    "academy of sciences",
    "academy of dramatic",
    "military academy",
    "chinese academy",
    "private limited",
    "dramatic arts",
    "west point",
    "academy of music",
    "charter school center",
    "brooklyn_academy",
    "sciences (nyas)",
    "global stars",
]


def is_ny(row):
    postal = row.get("postal", "").strip()
    try:
        zip5 = int(postal[:5])
        return NY_ZIP_MIN <= zip5 <= NY_ZIP_MAX
    except (ValueError, TypeError):
        return False


def is_k12_school(name, descr):
    text = (name + " " + descr).lower().replace("_", " ")
    if any(excl in text for excl in EXCLUDE_KEYWORDS):
        return False
    return any(incl in text for incl in INCLUDE_KEYWORDS)


def normalize_cidr(range_str):
    try:
        net = ipaddress.IPv4Network(range_str.strip(), strict=False)
        return str(net)
    except Exception:
        return None


def run(rwhois_file=RWHOIS_FILE, output_file=OUTPUT_FILE):
    results    = []
    seen_cidrs = set()
    total_rows = 0
    ny_rows    = 0
    k12_rows   = 0

    print(f"Loading RWHOIS data from {rwhois_file} ...")

    with gzip.open(rwhois_file, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1

            if not is_ny(row):
                continue
            ny_rows += 1

            name  = row.get("name",  "").strip()
            descr = row.get("descr", "").strip()

            if not is_k12_school(name, descr):
                continue
            k12_rows += 1

            cidr = normalize_cidr(row.get("range", ""))
            if not cidr or cidr in seen_cidrs:
                continue
            seen_cidrs.add(cidr)

            results.append({
                "cidr":        cidr,
                "school_name": name,
                "org_handle":  row.get("id", "").strip(),
            })

    # Write output
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cidr", "school_name", "org_handle"])
        writer.writeheader()
        writer.writerows(results)

    print(f"Total RWHOIS rows:        {total_rows:,}")
    print(f"In NY (by zip):           {ny_rows}")
    print(f"K-12 school matches:      {k12_rows}")
    print(f"Unique CIDRs written:     {len(results)}")
    print()

    # Print what we found
    for r in results:
        net  = ipaddress.IPv4Network(r["cidr"])
        size = net.num_addresses
        print(f"  {r['cidr']:<22}  {size:>6} IPs   {r['school_name']}")

    print(f"\nOutput -> {output_file}")


if __name__ == "__main__":
    run()
