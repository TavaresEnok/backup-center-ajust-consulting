import psycopg2
import sys

def test_conn():
    try:
        conn = psycopg2.connect(
            dbname="backup_center",
            user="user",
            password="password",
            host="127.0.0.1",
            port="5432",
            connect_timeout=3
        )
        print("SUCCESS: Connection to PostgreSQL successful!")
        conn.close()
    except Exception as e:
        print(f"FAILED: Connection to PostgreSQL failed: {e}")

if __name__ == "__main__":
    test_conn()
