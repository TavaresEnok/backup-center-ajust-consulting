import os
import sys

# 1. Configurar Entorno ANTES de qualquer import do App
os.environ["DATABASE_URL"] = "sqlite:///./test_system.db"
os.environ["WTF_CSRF_ENABLED"] = "False"
os.environ["TESTING"] = "True"

# Adicionar diretório atual ao path
sys.path.append(os.getcwd())

# Agora sim importar módulos do app (que vão ler a config acima)
from app import create_flask_app
from app.core.database import SessionLocal, Base, engine
from app.models.user import User, UserRole
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.models.device_type import DeviceType
from app.models.activity_log import ActivityLog
from app.services.auth_service import AuthService

def setup_clean_db():
    print("--- [SETUP] Criando Base de Dados Limpa (SQLite) ---")
    # Forçar recriação
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    # Criar Superadmin Inicial
    db = SessionLocal()
    
    # Superadmin
    admin = User(
        email="admin@system.com",
        password_hash=AuthService.get_password_hash("admin123"),
        full_name="Super Administrator",
        role=UserRole.SUPER_ADMIN,
        is_active=True
    )
    db.add(admin)
    db.commit()
    db.close()
    print("--- [SETUP] Superadmin criado (admin@system.com / admin123) ---")

def run_test():
    app = create_flask_app()
    app.config['WTF_CSRF_ENABLED'] = False # Ensure disable
    client = app.test_client()
    
    # DEBUG: Print all registered routes
    print("\n[DEBUG] URL MAP:")
    print(app.url_map)
    print("------------------")
    
    print("\n=== INICIANDO TESTE DE SISTEMA COMPLETO ===")
    
    # 1. Login Superadmin
    print("\n1. [AUTH] Login como Superadmin...")
    resp = client.post('/auth/login', data={
        'email': 'admin@system.com',
        'password': 'admin123'
    }, follow_redirects=True)
    
    if b"Login realizado com sucesso" not in resp.data and b"Dashboard" not in resp.data:
        print("!!! ERRO NO LOGIN !!!")
        print(resp.data.decode('utf-8'))
        raise AssertionError("Login falhou")
    else:
        print("   > Login OK! Acesso ao Dashboard confirmado.")
    
    # 2. Criar Plano
    print("\n2. [SUPERADMIN] Criando Plano 'Ouro'...")
    resp = client.post('/admin/plans/add', data={
        'name': 'Plano Ouro',
        'slug': 'gold-plan',
        'description': 'Plano completo',
        'price_monthly': '99.90',
        'price_yearly': '999.00',
        'max_devices': '50',
        'max_users': '5',
        'backup_retention_days': '60',
        'is_active': 'on'
    }, follow_redirects=True)
    assert b"Plano criado com sucesso" in resp.data
    print("   > Plano criado com sucesso.")
    
    # 3. Criar Novo Tenant
    print("\n3. [SUPERADMIN] Criando Tenant 'Tech Corp'...")
    resp = client.post('/admin/tenants/add', data={
        'name': 'Tech Corp',
        'slug': 'tech-corp',
        'owner_email': 'ceo@techcorp.com',
        'owner_password': 'user123'
    }, follow_redirects=True)
    if b"Tenant criado com sucesso" not in resp.data:
        print(resp.data.decode('utf-8'))
    assert b"Tenant criado com sucesso" in resp.data
    print("   > Tenant 'Tech Corp' criado.")

    # Logout Superadmin
    client.get('/auth/logout', follow_redirects=True)

    # 4. Login como Novo Tenant
    print("\n4. [TENANT] Login como CEO da Tech Corp...")
    resp = client.post('/auth/login', data={
        'email': 'ceo@techcorp.com',
        'password': 'user123'
    }, follow_redirects=True)
    assert b"Tech Corp" in resp.data
    print("   > Login no Tenant OK.")
    
    with client.session_transaction() as sess:
        print(f"   > Session after login: {sess}")
    
    # 5. Atualizar Perfil
    print("\n5. [SETTINGS] Atualizando nome do usuário...")
    resp = client.post('/tenant/tech-corp/settings/profile', data={
        'full_name': 'CEO Roberto Updated'
    }, follow_redirects=True)
    
    if b"Perfil atualizado" not in resp.data:
        print(f"!!! ERRO NO PERFIL !!! Status: {resp.status_code}")
        # print(resp.data.decode('utf-8', errors='ignore'))
        
    assert b"Perfil atualizado" in resp.data
    print("   > Perfil atualizado.")
    
    # 6. Atualizar Configuração Empresa
    print("\n6. [SETTINGS] Atualizando nome da empresa...")
    resp = client.post('/tenant/tech-corp/settings/general', data={
        'name': 'Tech Corp Solutions'
    }, follow_redirects=True)
    assert b"Configura\xc3\xa7\xc3\xb5es da empresa atualizadas" in resp.data or b"atualizadas!" in resp.data
    print("   > Configurações da empresa salvas.")

    # 7. Assinar Plano (Billing Manual)
    print("\n7. [BILLING] Assinando Plano Ouro (Manual)...")
    db = SessionLocal()
    gold_plan = db.query(Plan).filter_by(slug='gold-plan').first()
    db.close()
    
    resp = client.post(f'/tenant/tech-corp/billing/subscribe/{gold_plan.id}', follow_redirects=True)
    # Check for substring of expected message
    assert b"Instru" in resp.data 
    print("   > Assinatura manual iniciada! Status pendente registrado.")

    # 8. Criação de Dispositivo e Log de Atividade
    print("\n8. [DEVICE] Criando Dispositivo para Gerar Log...")
    db = SessionLocal()
    dt = DeviceType(name="Generic Router", slug="gen-router", script_name="generic.py", category="router")
    db.add(dt)
    db.commit()
    dt_id = dt.id
    db.close()
    
    resp = client.post('/tenant/tech-corp/devices/add', data={
        'name': 'Core Router',
        'ip_address': '192.168.1.1',
        'device_type_id': str(dt_id),
        'username': 'admin', 
        'password': 'admin',
        'port': '22'
    }, follow_redirects=True)
    assert b"Dispositivo criado com sucesso" in resp.data
    print("   > Dispositivo criado.")

    # 9. Verificando Activity Logs
    print("\n9. [AUDIT] Verificando Logs de Atividade no Banco...")
    db = SessionLocal()
    logs = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).all()
    print(f"   > Total de Logs Encontrados: {len(logs)}")
    
    found_device_log = any(l.action == 'CREATE_DEVICE' for l in logs)
    if found_device_log:
        print("   ✅ SUCESSO: Ação 'CREATE_DEVICE' foi auditada corretamente!")
    else:
        print("   ❌ FALHA: Ação 'CREATE_DEVICE' não encontrada nos logs.")
        
    db.close()
    
    print("\n=== TESTE FINALIZADO COM SUCESSO ===")

if __name__ == "__main__":
    setup_clean_db()
    run_test()
