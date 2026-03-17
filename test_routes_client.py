import sys
import os

# Adicionar CWD ao path
sys.path.append(os.getcwd())

from flask import Flask, session
from app import create_flask_app
from app.models.tenant import Tenant
from app.core.database import SessionLocal
from app.models.user import User

def test_routes_client():
    app = create_flask_app()
    app.config['TESTING'] = True
    app.config['DEBUG'] = True
    
    client = app.test_client()
    
    db = SessionLocal()
    tenant = db.query(Tenant).filter_by(slug='ajust-consulting').first()
    user = db.query(User).filter_by(email='audemario@ajustconsulting.com.br').first()
    db.close()
    
    if not tenant or not user:
        print("Tenant ou User não encontrados!")
        return

    print(f"Testando via Client GET para Tenant: {tenant.slug}")

    with client.session_transaction() as sess:
        sess['user_id'] = str(user.id)
        sess['user_role'] = user.role.value if hasattr(user.role, 'value') else user.role
        sess['tenant_slug'] = tenant.slug
        sess['_fresh'] = True
    
    print("MAPA DE ROTAS:")
    for rule in app.url_map.iter_rules():
        if 'compare' in str(rule):
            print(rule)
    
    try:
        url = f'/tenant/{tenant.slug}/backups/'
        print(f"GET {url}")
        resp = client.get(url)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 500:
             print(resp.get_data(as_text=True))
    except Exception as e:
        print(f"EXCEPTION: {e}")
        import traceback
        with open('last_error.log', 'w') as f:
            traceback.print_exc(file=f)

    try:
        # Tenta também compare pra ver se gera erro
        url_compare = f'/tenant/{tenant.slug}/compare/'
        # ... mas aqui vou deixar quieto por enquanto, focar backup.
    except:
        pass

if __name__ == "__main__":
    test_routes_client()
