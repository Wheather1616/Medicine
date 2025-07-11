#!/usr/bin/env python3
import argparse
import requests
from bs4 import BeautifulSoup
import webbrowser
from urllib.parse import urljoin

def open_first_tga_result(drug_name: str):
    # 1. Fetch the search results page
    base = "https://www.tga.gov.au"
    resp = requests.get(
        urljoin(base, "/search"),
        params={"keywords": drug_name, "submit": "Search"},
        timeout=10,
    )
    resp.raise_for_status()

    # 2. Parse the HTML and find the first link under <ul class="health-listing">
    soup = BeautifulSoup(resp.text, "html.parser")
    first_link = soup.select_one("ul.health-listing li a")
    if not first_link or not first_link.get("href"):
        print(f"No results found for “{drug_name}”.")
        return

    # 3. Build the full URL and open it
    href = first_link["href"]
    full_url = urljoin(base, href)
    print(f"Opening: {full_url}")
    webbrowser.open(full_url)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search TGA.gov.au and open the first result for a given medicine"
    )
    parser.add_argument("medicine", help="Medicine name to search for (e.g. tenofovir)")
    args = parser.parse_args()

    open_first_tga_result(args.medicine)
