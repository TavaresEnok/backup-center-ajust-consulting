import subprocess
import sys
import os
import time

def run_command(command):
    print(f"Running: {command}")
    process = subprocess.Popen(command, shell=True)
    return process

def main():
    print("=== Backup Center Auto-Configurator ===")
    
    # 1. Install dependencies
    print("\n1. Instalando dependências...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements/base.txt"])
    
    # 2. Setup Environment
    if not os.path.exists(".env"):
        print("\n2. Criando arquivo .env...")
        with open(".env", "w") as f:
            f.write("DATABASE_URL=postgresql://user:password@127.0.0.1:5432/backup_center\n")
            f.write("SECRET_KEY=dev-secret-key-123\n")
            f.write("ENCRYPTION_KEY=AYCJU4fRMZE61g04GsT653mApiwswOwvwlrpUK1lmgk=\n") # Fernet key placeholder
    
    # 3. Create directories
    print("\n3. Preparando diretórios...")
    os.makedirs("storage/backups", exist_ok=True)
    
    # 4. Start Server
    print("\n4. Iniciando servidor...")
    print("\nO sistema estará disponível em: http://localhost:8000")
    print("Pressione Ctrl+C para encerrar.\n")
    
    try:
        subprocess.check_call([sys.executable, "main.py"])
    except KeyboardInterrupt:
        print("\nServidor encerrado.")

if __name__ == "__main__":
    main()
