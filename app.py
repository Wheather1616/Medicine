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




def get_medicine(drug):
    base   = "https://medsinfo.com.au"
    lower  = drug.strip().lower()
    headers = {"User-Agent": "Mozilla/5.0"}

    # build an order of letters: query’s first letter, then the rest A–Z
    first = drug.strip()[0].upper()
    all_letters = [first] + [L for L in string.ascii_uppercase if L != first]

    detail = None
    for letter in all_letters:
        page = 1
        while True:
            idx_url = f"{base}/consumer-information/A-To-Z-Index/{letter}?page={page}"
            app.logger.info(f"[GET_MEDICINE] Fetching {idx_url}")
            try:
                r = requests.get(idx_url, headers=headers, timeout=10)
                r.raise_for_status()
            except Exception:
                app.logger.error(traceback.format_exc())
                break

            soup = BeautifulSoup(r.text, 'html.parser')

            # look for either the link text or its <small class="ingredient">
            for a in soup.find_all('a', href=True):
                if '/document/' not in a['href']:
                    continue

                link_text = a.get_text(strip=True).lower()
                ing_tag   = a.find_next_sibling('small', class_='ingredient')
                ing_text  = ing_tag.get_text(strip=True).lower() if ing_tag else ''

                if (lower == link_text or lower in link_text or
                    lower == ing_text  or lower in ing_text):
                    detail = urljoin(base, a['href'])
                    break

            if detail:
                break

            # no match on this page?
            if not soup.find('table'):
                # no results at all for this letter
                break
            page += 1

        if detail:
            # once we found it, stop looping letters
            break

    if not detail:
        return None

        app.logger.info(f"Detail page: {detail}")
        try:
            r2 = requests.get(detail, headers=headers, timeout=10)
            r2.raise_for_status()
        except Exception:
            app.logger.error(traceback.format_exc())
            return None

        s2 = BeautifulSoup(r2.text, 'html.parser')
        info_div = s2.find('div', class_='drug-info') or s2.find('article', class_='document-page') or s2
        title = info_div.find('h1').get_text(strip=True) if info_div.find('h1') else drug.strip()

        ing = None
        ex_headers = s2.find_all(
            lambda tag: tag.name in ('h2', 'h3') and any(
                kw in tag.get_text(strip=True).lower() for kw in ['why am i taking', 'why am i using']
            )
        )
        if ex_headers:
            for tag in ex_headers[0].find_all_next():
                if tag.name in ('h2', 'h3'):
                    break
                if tag.name == 'p':
                    text_p = tag.get_text(strip=True)
                    if 'contains the active ingredient' in text_p.lower():
                        m = re.search(r'contains the active ingredient(?:s)?\s*(.+?)\.', text_p, re.IGNORECASE)
                        if m:
                            parts = re.split(r' and |,\s*', m.group(1))
                            ing = ', '.join(p.strip() for p in parts)
                        break
        if not ing:
            ing = extract_section(s2, ['Active ingredient']) or _fallback_regex(s2, r'Active ingredient:?\s*(.+)')

        dose_list = []
        dose_headers = s2.find_all(lambda tag: tag.name in ('h2', 'h3') and any(
            kw in tag.get_text(strip=True).lower() for kw in ['how do i use', 'how do i take', 'how to take']
        ))
        if dose_headers:
            hdr = dose_headers[0]
            sib = hdr
            while sib:
                sib = sib.find_next_sibling()
                if not sib:
                    break
                if sib.name == 'ul':
                    for li in sib.find_all('li'):
                        txt = li.get_text(separator=' ', strip=True)
                        if txt:
                            dose_list.append(txt)
                    break
        dose = dose_list if dose_list else extract_section(s2, ['How to take', 'How do I use', 'How do I take'])
        cont = extract_section(s2, ['Do not use if'])
        warn = extract_warnings(s2)
        side_effects = extract_side_effects_from_soup(s2)
        common_side_effects = ', '.join(side_effects['common']) if side_effects['common'] else None
        serious_side_effects = ', '.join(side_effects['serious']) if side_effects['serious'] else None

        over = extract_section(s2, ['If you take too much', 'Overdose'])

        inter_list = []
        headers_list = s2.find_all(
            lambda tag: tag.name in ('h2', 'h3') and 'other medicines' in tag.get_text(strip=True).lower()
        )
        target = headers_list[1] if len(headers_list) > 1 else (headers_list[0] if headers_list else None)
        if target:
            sib = target
            while sib:
                sib = sib.find_next_sibling()
                if not sib:
                    break
                if sib.name == 'ul':
                    for li in sib.find_all('li'):
                        txt = li.get_text(separator=' ', strip=True)
                        if txt:
                            inter_list.append(txt)
                    break
        interactions = '\n'.join(f'- {item}' for item in inter_list) if inter_list else extract_section(s2, ['What if I am taking other medicines'])

        pdf_href = None
        a_pdf = s2.find('a', href=re.compile(r'format=pdf', re.I)) or s2.find('a', string=re.compile(r'Download PDF', re.I))
        if a_pdf and a_pdf.get('href'):
            pdf_href = urljoin(base, a_pdf['href'])
        pdf_file = download_pdf(pdf_href, title) if pdf_href else None

        return {
            'url': detail,
            'name': title,
            'ingredient': ing,
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
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(error='Missing q'), 400

    app.logger.info(f"[ADD] Lookup for “{q}”")
    with sqlite3.connect(DB_PATH) as conn:
        try:
            res = get_medicine(q)
        except Exception as e:
            # This logs the full traceback to your console / nohup.out
            app.logger.exception("Error fetching medicine %s", q)
            # Return the exception text so you can see it in your curl response
            return jsonify(error='fetch error', detail=str(e)), 500

        if not res:
            return jsonify(error='Not found'), 404

        # Use the *scraped* name to check the cache, not the raw query:
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