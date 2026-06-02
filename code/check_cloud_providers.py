"""
Cloud Provider IP Range Check
---------------------
For each IP address in the input CSV, checks if it falls within any known cloud provider IP ranges.

Run this once to generate the coverage table for the paper.
"""

import csv
import subprocess
import sys
from pathlib import Path

INPUT_DIR = "data/outputs/phase2_filtered_5km.csv"
OUTPUT_DIR = "data/outputs/phase2_filtered_5km_cloud_val.csv"
CLOUD_SEARCH_SCRIPT = "../cloud-provider-ip-addresses/lookup.py"
DATA_DIR = "../cloud-provider-ip-addresses/"

def lookup_ip(ip, lookup_script):
    try:
        result = subprocess.run(
            [
                sys.executable,
                "../cloud-provider-ip-addresses/lookup.py",
                "--data-dir",
                "../cloud-provider-ip-addresses/",
                ip,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        result = result.stdout.strip()
        
        if "-" in result:
            result = result.split("-", 1)[1]

        return result

    except subprocess.CalledProcessError as e:
        print(f"Lookup failed for {ip}")
        print("Return code:", e.returncode)
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return "error"


def process_csv(input_csv, output_csv, lookup_script):
    with open(input_csv, "r") as infile:
        reader = csv.DictReader(infile)

        fieldnames = reader.fieldnames + ["cloud_provider_match"]

        with open(output_csv, "w") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)

            writer.writeheader()

            for row in reader:
                ip = row["ip_address"].strip()

                row["cloud_provider_match"] = lookup_ip(
                    ip,
                    lookup_script,
                )

                writer.writerow(row)


if __name__ == "__main__":
    process_csv(INPUT_DIR, OUTPUT_DIR, CLOUD_SEARCH_SCRIPT)