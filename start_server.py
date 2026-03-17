import os
import sys
import time
import subprocess
import psycopg2
from app.core.config import settings

def check_db():
    print("Verificando conexão com o banco de dados...")
    retry = 0
    while retry < 5:
        try:
            conn = psycopg2.connect(settings.DATABASE_URL, connect_timeout=3)
            conn.close()
            print("Conexão com PostgreSQL OK!")
            return True
        except Exception as e:
            try:
                err_msg = str(e)
            except UnicodeDecodeError:
                err_msg = repr(e)
            print(f"Tentativa {retry+1}/5: Banco de dados ainda não está pronto... ({err_msg})")
            time.sleep(3)
            retry += 1
    return False

if __name__ == "__main__":
    if check_db():
        print("Iniciando servidor...")
        subprocess.run([sys.executable, "main.py"])
    else:
        print("ERRO: Não foi possível conectar ao banco de dados após várias tentativas.")
        print("Certifique-se de que o Docker Desktop está aberto e os containers estão rodando.")
