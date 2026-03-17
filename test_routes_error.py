import sys
import os

# Adicionar CWD ao path
sys.path.append(os.getcwd())

from flask import Flask, session
from app import create_flask_app
from app.models.tenant import Tenant
from app.core.database import SessionLocal

def test_routes():
    app = create_flask_app()
    app.config['TESTING'] = True
    
    # Precisamos de um tenant válido
    db = SessionLocal()
    tenant = db.query(Tenant).filter_by(slug='ajust-consulting').first()
    db.close()
    
    if not tenant:
        print("Tenant ajust-consulting não encontrado!")
        return

    print(f"Testando rotas par ao Tenant: {tenant.name} ({tenant.id})")
    
    with app.test_request_context(f'/tenant/{tenant.slug}/backups'):
        # Simula login
        session['user_role'] = 'SUPER_ADMIN'
        session['tenant_slug'] = tenant.slug
        
        try:
            from app.web.tenant.backups import list_backups
            print("Executando list_backups...")
            list_backups(tenant.slug)
            print("list_backups: SUCESSO")
        except Exception as e:
            print(f"list_backups: ERRO: {e}")
            import traceback
            traceback.print_exc()

    with app.test_request_context(f'/tenant/{tenant.slug}/compare/'):
        session['user_role'] = 'SUPER_ADMIN'
        session['tenant_slug'] = tenant.slug
        
        try:
            # O problema do compare é que a rota exige device_id?
            # Se for acessado sem device_id, deve dar 404, mas user reportou 500.
            # Vou tentar acessar a rota se ela existir.
            # Se não, vou tentar descobrir qual rota o browser chamou.
            # O browser chamou: /tenant/ajust-consulting/compare/
            # Vou ver se tenant_compare.bp tem rota /
            from app.web.tenant import compare
            # Se compare.bp tiver url_prefix com device_id, então rota / não deve ser essa.
            print(f"Compare Blueprint Prefix: {compare.bp.url_prefix}")
            
        except Exception as e:
            print(f"Compare Check: ERRO: {e}")

if __name__ == "__main__":
    test_routes()
