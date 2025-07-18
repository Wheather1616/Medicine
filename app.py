from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import sqlite3
import logging
import re

# Configure logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Full path to the SQLite database
db_path = "/home/ubuntu/med-api/Medicine.db"


def init_db():
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            name TEXT UNIQUE COLLATE NOCASE,
            ingredient TEXT,
            dosage TEXT,
            contraindications TEXT,
            warnings TEXT,
            interactions TEXT,
            key_side_effects TEXT,
            overdose_info TEXT
        )
    ''')
    conn.close()


def extract_section(soup, heading_variants):
    """Find any heading variant and return its following content until the next same-level heading."""
    header = None
    for variant in heading_variants:
        header = soup.find(lambda tag: tag.name in ('h2', 'h3') and variant.lower() in tag.get_text(strip=True).lower())
        if header:
            break
    if not header:
        return None
    parts = []
    for sib in header.find_next_siblings():
        if sib.name in ('h2', 'h3'):
            break
        text = sib.get_text(separator=" ", strip=True)
        if text:
            parts.append(text)
    return "\n".join(parts)


def get_medicine(drug):
    base = "https://medsinfo.com.au"
    first_letter = drug.strip()[0].upper()
    drug_lower = drug.strip().lower()
    headers = {"User-Agent": "Mozilla/5.0"}
    page = 1

    while True:
        index_url = f"{base}/consumer-information/A-To-Z-Index/{first_letter}?page={page}"
        app.logger.info(f"[GET_MEDICINE] Fetch index: {index_url}")
        try:
            response = requests.get(index_url, headers=headers, timeout=10)
            response.raise_for_status()
        except Exception as e:
            app.logger.error(f"Error fetching index page: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a", href=True)

        for a in links:
            if a.text.strip().lower() == drug_lower and "/document/" in a["href"]:
                full_url = base.rstrip("/") + "/" + a["href"].lstrip("/")
                app.logger.info(f"[GET_MEDICINE] Fetch detail: {full_url}")
                try:
                    resp2 = requests.get(full_url, headers=headers, timeout=10)
                    resp2.raise_for_status()
                except Exception as e:
                    app.logger.error(f"Error fetching detail page: {e}")
                    raise

                soup2 = BeautifulSoup(resp2.text, "html.parser")
                info_div = soup2.find("div", class_="drug-info") or soup2
                title = info_div.find("h1").get_text(strip=True) if info_div and info_div.find("h1") else None

                # Ingredient: heading or fallback regex
                ingredient = extract_section(soup2, ["Active ingredient"]) or None
                if not ingredient:
                    match = re.search(r"Active ingredient:?\s*(.+)", soup2.get_text(separator="\n"))
                    ingredient = match.group(1).strip() if match else None

                # Dosage: support variants
                dosage = extract_section(soup2, ["How do I use", "How do I take"]) or None

                # Contraindications
                contraindications = extract_section(soup2, ["Do not use if"]) or None
                # Warnings: include before-taking, while-taking, standalone
                warnings = extract_section(soup2, [
                    "What should I know before", 
                    "What should I know while taking", 
                    "Warnings"
                ]) or None
                # Interactions
                interactions = extract_section(soup2, ["What if I am taking other medicines"]) or None
                # Key side effects
                key_side_effects = extract_section(soup2, ["Are there any side effects"]) or None
                # Overdose info
                overdose_info = extract_section(soup2, ["If you think that you have used too much"]) or None

                return {
                    "url": full_url,
                    "name": title,
                    "ingredient": ingredient,
                    "dosage": dosage,
                    "contraindications": contraindications,
                    "warnings": warnings,
                    "interactions": interactions,
                    "key_side_effects": key_side_effects,
                    "overdose_info": overdose_info
                }
        if not soup.find("table"):
            break
        page += 1
    return None

@app.route("/add", methods=["GET"])
def add_medicine():
    drug = request.args.get("q", "").strip()
    if not drug:
        return jsonify(error="Missing query parameter 'q'."), 400

    conn = sqlite3.connect(db_path)
    # Case-insensitive cache lookup
    cursor = conn.execute("SELECT * FROM medicines WHERE lower(name)=lower(?)", (drug,))
    cached = cursor.fetchone()
    if cached:
        cols = [c[0] for c in cursor.description]
        med = dict(zip(cols, cached))
        med["source"] = "cache"
        conn.close()
        app.logger.info(f"Returning '{drug}' from CACHE")
        return jsonify(med), 200

    # Fetch remotely
    try:
        result = get_medicine(drug)
    except Exception as e:
        app.logger.error(f"Error in get_medicine: {e}")
        conn.close()
        return jsonify(error="Internal error fetching medicine."), 500

    if not result:
        conn.close()
        return jsonify(error=f"No CMI document found for '{drug}'."), 404

    import sqlite3 as _sqlite
    try:
        insert_cursor = conn.execute(
            '''INSERT INTO medicines
               (url,name,ingredient,dosage,contraindications,warnings,interactions,key_side_effects,overdose_info)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (result["url"], result["name"], result["ingredient"], result["dosage"],
             result["contraindications"], result["warnings"], result["interactions"],
             result["key_side_effects"], result["overdose_info"])
        )
        conn.commit()
    except _sqlite.IntegrityError:
        conn.rollback()
        cursor = conn.execute("SELECT * FROM medicines WHERE lower(name)=lower(?)", (drug,))
        row = cursor.fetchone()
        cols = [c[0] for c in cursor.description]
        med = dict(zip(cols, row))
        med["source"] = "cache"
        conn.close()
        return jsonify(med), 200

    new_id = insert_cursor.lastrowid
    row_cursor = conn.execute("SELECT * FROM medicines WHERE id = ?", (new_id,))
    row = row_cursor.fetchone()
    cols = [c[0] for c in row_cursor.description]
    conn.close()

    med = dict(zip(cols, row))
    med["source"] = "remote"
    app.logger.info(f"Fetched '{drug}' from web and saved to DB")
    return jsonify(med), 200

@app.route("/list", methods=["GET"])
def list_medicines():
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT * FROM medicines")
    cols = [c[0] for c in cursor.description]
    meds = [dict(zip(cols, row)) for row in cursor.fetchall()]
    conn.close()
    return jsonify(medicines=meds), 200

@app.route("/delete", methods=["GET"])
def delete_medicine():
    url = request.args.get("q", "").strip()
    if not url:
        return jsonify(error="Missing query parameter 'q'."), 400

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("DELETE FROM medicines WHERE url = ?", (url,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()

    if affected == 0:
        return jsonify(error=f"No entry found matching URL '{url}'."), 404
    return jsonify(deleted=url), 200

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=80)
