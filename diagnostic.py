import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def diagnostic():
    db_url = os.getenv("DATABASE_URL")
    print(f"Tentando conectar ao banco: {db_url}")
    try:
        # Extrair parâmetros manualmente para evitar problemas de parsing na URL se necessário
        # postgresql://user:password@127.0.0.1:5432/backup_center
        conn = psycopg2.connect(
            dbname="backup_center",
            user="user",
            password="password",
            host="127.0.0.1",
            port="5432",
            connect_timeout=5
        )
        print("CONEXÃO PSYCOPG2: OK")
        cur = conn.cursor()
        cur.execute("SELECT version();")
        print(f"Versão do Postgres: {cur.fetchone()}")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"ERRO DE CONEXÃO: {type(e).__name__}")
        try:
            # Tentar capturar a mensagem bruta se possível
            print(f"Mensagem: {str(e)}")
        except UnicodeDecodeError:
            print("Mensagem contém caracteres que falharam no decode utf-8.")
            # Tentativa de binário ou repr
            print(f"Mensagem (repr): {repr(e)}")

if __name__ == "__main__":
    diagnostic()
