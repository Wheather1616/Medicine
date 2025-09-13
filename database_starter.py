import sqlite3
import os

# Use a local folder for the database
db_path = os.path.join(os.path.dirname(__file__), "Medicine.db")

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