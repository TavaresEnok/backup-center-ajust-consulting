import os
import sys

# 1. Configurar Entorno
os.environ["DATABASE_URL"] = "sqlite:///./test_debug.db"
os.environ["WTF_CSRF_ENABLED"] = "False"
os.environ["TESTING"] = "True"

sys.path.append(os.getcwd())

from app import create_flask_app

app = create_flask_app()
print("\n=== URL MAP ===")
for rule in app.url_map.iter_rules():
    print(f"{rule.endpoint} -> {rule}")
print("===============")
