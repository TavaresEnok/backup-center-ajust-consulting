import sqlite3
import os

db_path = 'antigos/projeto_antigo03/app/app.db'
print(f"Checking database: {db_path}")
print(f"Exists: {os.path.exists(db_path)}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cursor.fetchall()]
print(f"\nTables: {tables}")

# Get data from each relevant table
for table in ['user', 'provedor', 'dispositivo', 'tipo_equipamento', 'log_backup']:
    if table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"\n{table}: {count} records")
        
        if count > 0 and count < 50:
            cursor.execute(f"SELECT * FROM {table} LIMIT 10")
            rows = cursor.fetchall()
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]
            print(f"  Columns: {columns}")
            for row in rows:
                print(f"  - {dict(zip(columns, row))}")

conn.close()
