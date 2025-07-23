from flask import Flask, request, jsonify, send_from_directory, abort
import requests
from bs4 import BeautifulSoup
import sqlite3
import logging
import re
import os
import traceback
from urllib.parse import urljoin, urlparse

# Configure logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Paths (override with env vars)
DB_PATH = os.getenv("MEDICINE_DB_PATH", "/home/ubuntu/med-api/Medicine.db")
PDF_DIR = os.getenv("MEDICINE_PDF_DIR", "/home/ubuntu/med-api/pdfs")
os.makedirs(PDF_DIR, exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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
                common_side_effects TEXT,
                serious_side_effects TEXT,
                overdose_info TEXT,
                pdf_url TEXT,
                pdf_filename TEXT
            )
        ''')
        # Add new columns if missing (for upgrades)
        cur = conn.execute("PRAGMA table_info(medicines)")
        columns = [col[1] for col in cur.fetchall()]
        if "common_side_effects" not in columns:
            conn.execute("ALTER TABLE medicines ADD COLUMN common_side_effects TEXT")
        if "serious_side_effects" not in columns:
            conn.execute("ALTER TABLE medicines ADD COLUMN serious_side_effects TEXT")

def extract_section(soup, heading_variants):
    header = None
    for variant in heading_variants:
        header = soup.find(lambda tag: tag.name in ('h2', 'h3') and variant.lower() in tag.get_text(strip=True).lower())
        if header:
            break
    if not header:
        for variant in heading_variants:
            for p in soup.find_all('p'):
                strong = p.find('strong')
                if strong and variant.lower() in p.get_text(strip=True).lower():
                    header = p
                    break
            if header:
                break
    if not header:
        return None
    parts = []
    for sib in header.find_next_siblings():
        if sib.name in ('h2', 'h3') or (sib.name == 'p' and sib.find('strong')):
            break
        txt = sib.get_text(separator=' ', strip=True)
        if txt:
            parts.append(txt)
    return "\n".join(parts) if parts else None

def download_pdf(pdf_url, name):
    try:
        resp = requests.get(pdf_url, stream=True, timeout=10)
        resp.raise_for_status()
        ext = os.path.splitext(urlparse(pdf_url).path)[1] or '.pdf'
        safe = re.sub(r'[^0-9A-Za-z_-]', '_', name) + ext
        path = os.path.join(PDF_DIR, safe)
        with open(path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return safe
    except Exception:
        app.logger.error("PDF download failed:\n" + traceback.format_exc())
        return None

def _fallback_regex(soup, pattern):
    m = re.search(pattern, soup.get_text(separator='\n'), re.IGNORECASE)
    return m.group(1).strip() if m else None

def extract_warnings(soup):
    warnings = []
    count = 0

    # Step 1: Find all relevant sections
    sections = soup.find_all('section', id=re.compile(r'.*-body$'))

    for section in sections:
        h3 = section.find('h3')
        if h3 and 'what should i know before i' in h3.get_text(strip=True).lower():
            count += 1
            if count == 2:
                # Step 2: Extract text from h4, p, ul inside this section
                for tag in section.find_all(['h4', 'p', 'ul']):
                    text = tag.get_text(separator=' ', strip=True)
                    if text:
                        warnings.append(text)
                break

    return warnings

def extract_side_effects_from_soup(soup):
    # Find all sections with id ending in -body
    sections = soup.find_all('section', id=re.compile(r'.*-body$'))

    for section in sections:
        h3 = section.find('h3')
        if h3 and 'are there any side effects' in h3.get_text(strip=True).lower():
            return extract_effects_from_section(section)

    return {'common': [], 'serious': []}

def extract_effects_from_section(section):
    common = []
    serious = []

    # First, check if there are tables
    tables = section.find_all('table')
    if len(tables) >= 2:
        common = extract_side_effects_from_table(tables[0])
        serious = extract_side_effects_from_table(tables[1])
    else:
        # Fallback to strong-tag method
        common, serious = extract_effects_by_strong_order(section)

    return {'common': common, 'serious': serious}

def extract_side_effects_from_table(table):
    effects = []
    for row in table.find_all('tr'):
        for cell in row.find_all(['td', 'th']):
            text = cell.get_text(separator=' ', strip=True)
            if text:
                effects.append(text)
    return effects

def extract_effects_by_strong_order(section):
    common = []
    serious = []

    strong_tags = section.find_all('strong')

    for i, tag in enumerate(strong_tags[:2]):  # Use only first two
        next_text = ''
        for sibling in tag.next_siblings:
            if isinstance(sibling, str):
                next_text = sibling.strip()
                if next_text:
                    break
            elif sibling.name is None and sibling.string:
                next_text = sibling.string.strip()
                if next_text:
                    break

        effects = [s.strip().strip('.') for s in next_text.split(',') if s.strip()]
        if i == 0:
            common.extend(effects)
        elif i == 1:
            serious.extend(effects)

    return common, serious

def get_medicine(drug=None, ing_query=None):
    """
    Fetch medicine info by name or active ingredient from medsinfo.com.au.
    Either `drug` or `ing_query` must be provided.
    """
    base = "https://medsinfo.com.au"
    drug_query = drug.strip().lower() if drug else None
    ing_query_lc = ing_query.strip().lower() if ing_query else None

    if not drug_query and not ing_query:
        return None

    first_char = (drug_query or ing_query)[0].upper()
    headers = {"User-Agent": "Mozilla/5.0"}
    page = 1

    while True:
        idx_url = f"{base}/consumer-information/A-To-Z-Index/{first_char}?page={page}"
        app.logger.info(f"[GET_MEDICINE] Fetching {idx_url}")
        try:
            resp = requests.get(idx_url, headers=headers, timeout=10)
            resp.raise_for_status()
        except Exception:
            app.logger.error(traceback.format_exc())
            break

        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('table tbody tr.desktop-only')
        if not rows:
            break

        for row in rows:
            name_tag = row.select_one('td.col2 a')
            ingredient_tag = row.select_one('td.col2 small.ingredient')
            if not name_tag or not ingredient_tag:
                continue

            name_text = name_tag.get_text(strip=True)
            ingredient_text = ingredient_tag.get_text(strip=True)
            name_lc = name_text.lower()
            ingredient_lc = ingredient_text.lower()
            href = name_tag.get('href')
            detail_url = urljoin(base, href)

            match_name = drug_query and (drug_query in name_lc)
            match_ing = ing_query_lc and (ing_query_lc in ingredient_lc)

            if not (match_name or match_ing):
                continue

            app.logger.info(f"[GET_MEDICINE] Matched index: {name_text} ({ingredient_text}) -> {detail_url}")
            
            # Fetch details from document page
            try:
                r2 = requests.get(detail_url, headers=headers, timeout=10)
                r2.raise_for_status()
            except Exception:
                app.logger.error(traceback.format_exc())
                continue

            doc_soup = BeautifulSoup(r2.text, 'html.parser')
            info_div = doc_soup.find('div', class_='drug-info') \
                        or doc_soup.find('article', class_='document-page') \
                        or doc_soup
            title_tag = info_div.find('h1')
            title_text = title_tag.get_text(strip=True) if title_tag else name_text

            # Dosage extraction
            dose_list = []
            dose_headers = doc_soup.find_all(
                lambda tag: tag.name in ('h2', 'h3') and any(
                    kw in tag.get_text(strip=True).lower() for kw in ['how do i use', 'how do i take', 'how to take']
                )
            )
            if dose_headers:
                sib = dose_headers[0]
                while sib := sib.find_next_sibling():
                    if sib.name == 'ul':
                        for li in sib.find_all('li'):
                            txt = li.get_text(separator=' ', strip=True)
                            if txt:
                                dose_list.append(txt)
                        break
            dose = dose_list or extract_section(doc_soup, ['How to take', 'How do I use', 'How do I take'])

            cont = extract_section(doc_soup, ['Do not use if'])
            warn = extract_warnings(doc_soup)
            effects = extract_side_effects_from_soup(doc_soup)
            common_side_effects = ', '.join(effects['common']) if effects['common'] else None
            serious_side_effects = ', '.join(effects['serious']) if effects['serious'] else None
            over = extract_section(doc_soup, ['If you take too much', 'Overdose'])

            inter_list = []
            hdrs = doc_soup.find_all(
                lambda tag: tag.name in ('h2', 'h3') and 'other medicines' in tag.get_text(strip=True).lower()
            )
            target = hdrs[1] if len(hdrs) > 1 else (hdrs[0] if hdrs else None)
            if target:
                sib = target
                while sib := sib.find_next_sibling():
                    if sib.name == 'ul':
                        for li in sib.find_all('li'):
                            txt = li.get_text(separator=' ', strip=True)
                            if txt:
                                inter_list.append(txt)
                        break
            interactions = '\n'.join(f'- {item}' for item in inter_list) \
                if inter_list else extract_section(doc_soup, ['What if I am taking other medicines'])

            # PDF
            pdf_href = None
            a_pdf = doc_soup.find('a', href=re.compile(r'format=pdf', re.I)) \
                    or doc_soup.find('a', string=re.compile(r'Download PDF', re.I))
            if a_pdf and a_pdf.get('href'):
                pdf_href = urljoin(base, a_pdf['href'])
            pdf_file = download_pdf(pdf_href, title_text) if pdf_href else None

            return {
                'url': detail_url,
                'name': title_text,
                'ingredient': ingredient_text,
                'dosage': dose,
                'contraindications': cont,
                'warnings': warn,
                'interactions': interactions,
                'common_side_effects': common_side_effects,
                'serious_side_effects': serious_side_effects,
                'overdose_info': over,
                'pdf_url': pdf_href,
                'pdf_filename': pdf_file
            }

        page += 1

    return None

def clean_value(val):
    if isinstance(val, list):
        return ' '.join(
            re.sub(r'\s+', ' ', v.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')).strip()
            for v in val
        )
    elif isinstance(val, dict):
        return str(val)
    elif isinstance(val, str):
        return re.sub(r'\s+', ' ', val.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')).strip()
    return val

@app.route('/add', methods=['GET'])
def add():
    name_query = request.args.get('q', '').strip() or None
    ing_query = request.args.get('active_ingredient', '').strip() or None

    if not name_query and not ing_query:
        return jsonify(error='Missing name or active_ingredient'), 400

    search_desc = name_query or ing_query
    app.logger.info(f"[ADD] Lookup for “{search_desc}” (name={name_query}, ingredient={ing_query})")

    with sqlite3.connect(DB_PATH) as conn:
        try:
            # pass both parameters to get_medicine()
            res = get_medicine(drug=name_query, ing_query=ing_query)
        except Exception as e:
            app.logger.exception("Error fetching medicine %s / ingredient %s", name_query, ing_query)
            return jsonify(error='fetch error', detail=str(e)), 500

        if not res:
            return jsonify(error='Not found'), 404

        # check cache by scraped name
        cur = conn.execute(
            "SELECT * FROM medicines WHERE lower(name)=lower(?)",
            (res['name'],)
        )
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            m = dict(zip(cols, row))
            m['source'] = 'cache'
            return jsonify(m)

        # insert fresh scrape
        ins = '''INSERT INTO medicines
                 (url, name, ingredient, dosage, contraindications, warnings,
                  interactions, common_side_effects, serious_side_effects,
                  overdose_info, pdf_url, pdf_filename)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
        conn.execute(ins, tuple(clean_value(res[k]) for k in [
            'url', 'name', 'ingredient', 'dosage', 'contraindications',
            'warnings', 'interactions', 'common_side_effects', 'serious_side_effects',
            'overdose_info', 'pdf_url', 'pdf_filename'
        ]))
        conn.commit()

        # return the newly inserted row
        cur2 = conn.execute("SELECT * FROM medicines WHERE name=?", (res['name'],))
        row2 = cur2.fetchone()
        cols2 = [c[0] for c in cur2.description]
        m2 = dict(zip(cols2, row2))
        m2['source'] = 'remote'
        return jsonify(m2)

@app.route('/pdf/<fn>', methods=['GET'])
def pdf(fn):
    try:
        return send_from_directory(PDF_DIR, fn)
    except FileNotFoundError:
        abort(404)

@app.route('/list', methods=['GET'])
def lst():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT * FROM medicines")
        cols = [c[0] for c in cur.description]
        data = [dict(zip(cols, r)) for r in cur.fetchall()]
    return jsonify(medicines=data)

@app.route('/delete', methods=['GET'])
def delete():
    u = request.args.get('q', '').strip()
    if not u:
        return jsonify(error='Missing q'), 400
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM medicines WHERE url=?", (u,))
        conn.commit()
        if cur.rowcount == 0:
            return jsonify(error='No match'), 404
    return jsonify(deleted=u)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=80, debug=True)
