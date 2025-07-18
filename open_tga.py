#open_tga.py
#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import sqlite3
import os
from urllib.parse import urljoin, quote_plus

def get_medicine_name(drug_name: str) -> str:
    import re
    base = "https://medsinfo.com.au"
    first_letter = drug_name.strip()[0].upper()
    id_target = f"row_{drug_name.strip().replace(' ', '_')}_CMI"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MedsInfoRowSearch/1.0)"
    }

    page = 1
    while True:
        url = f"{base}/consumer-information/A-To-Z-Index/{first_letter}?page={page}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[ERROR] Failed to load: {e}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.find("tr", id=id_target):
            return drug_name.strip()

        if not soup.find("tr"):
            break

        page += 1

    print(f"No match found for “{drug_name}”.")
    return None



def insert_into_database(medicine_name: str):
    icloud_path = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
    db_path = os.path.join(icloud_path, "Medicine.db")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    ''')

    cursor.execute('INSERT INTO medicines (name) VALUES (?)', (medicine_name,))
    conn.commit()
    conn.close()

    print(f"✅ Added: {medicine_name}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Search HealthDirect and save medicine name to iCloud DB.")
    parser.add_argument("medicine", help="Medicine name to search for (e.g. tenofovir)")
    args = parser.parse_args()

    name = get_medicine_name(args.medicine)
    if name:
        insert_into_database(name)
