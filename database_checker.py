import sqlite3
import os

def show_all_medicines():
    icloud_path = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
    db_path = os.path.join(icloud_path, "Medicine.db")

    if not os.path.exists(db_path):
        print("⚠️ Database not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT id, name FROM medicines')
    rows = cursor.fetchall()

    if not rows:
        print("📭 No entries in the database.")
    else:
        print("📋 Medicines in database:")
        for row in rows:
            print(f"{row[0]}. {row[1]}")

    conn.close()


def remove_medicine(name: str):
    icloud_path = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
    db_path = os.path.join(icloud_path, "Medicine.db")

    if not os.path.exists(db_path):
        print("⚠️ Database not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM medicines WHERE name = ?", (name,))
    result = cursor.fetchone()

    if result:
        cursor.execute("DELETE FROM medicines WHERE name = ?", (name,))
        conn.commit()
        print(f"🗑 Deleted medicine: {name}")
    else:
        print(f"❌ Medicine '{name}' not found in the database.")

    conn.close()

show_all_medicines()
