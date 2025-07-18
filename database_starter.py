import sqlite3
import os

# Define the iCloud Drive path
icloud_path = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")

# Ensure it exists
if not os.path.isdir(icloud_path):
    raise Exception("iCloud Drive folder not found. Make sure iCloud Drive is enabled.")

# Define full database path
db_path = os.path.join(icloud_path, "Medicine.db")

# Create and connect to the database
conn = sqlite3.connect(db_path)

# Optional: create a sample table
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brand TEXT,
    detail_url TEXT
)
''')

# Save and close
conn.commit()
conn.close()

print(f"✅ Medicine.db created at {db_path}")
