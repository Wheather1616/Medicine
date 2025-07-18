#Pills.py
import sqlite3
import os
import sys

# Use a persistent file in the user's home directory
db_path = os.path.expanduser("~/Drugs.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Create table if it doesn't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

# Add a task passed via command-line argument (or show list if none)
if len(sys.argv) > 1:
    task_name = " ".join(sys.argv[1:])
    cursor.execute('INSERT INTO tasks (name) VALUES (?)', (task_name,))
    conn.commit()
    print(f"✅ Added task: {task_name}")

# Print all tasks
cursor.execute('SELECT id, name, created FROM tasks ORDER BY id')
rows = cursor.fetchall()

print("\n📝 Task List:")
for row in rows:
    print(f"{row[0]}. {row[1]} (added {row[2]})")

conn.close()
