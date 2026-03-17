from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.core.security import get_password_hash
import uuid

def seed():
    db = SessionLocal()
    try:
        # 1. Create Super Admin
        admin_email = "admin@backupcenter.com"
        admin_pass = "admin123"
        admin_user = db.query(User).filter(User.email == admin_email).first()
        
        if not admin_user:
            admin_user = User(
                email=admin_email,
                password_hash=get_password_hash(admin_pass),
                full_name="Super Admin",
                role=UserRole.SUPER_ADMIN,
                is_active=True
            )
            db.add(admin_user)
            print(f"Super Admin criado: {admin_email} / {admin_pass}")

        # 2. Create a Tenant and a Client User
        tenant_name = "Cliente Exemplo"
        tenant_slug = "cliente-exemplo"
        tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
        
        if not tenant:
            tenant = Tenant(
                name=tenant_name,
                slug=tenant_slug,
                email="contato@exemplo.com",
                plan_id=None
            )
            db.add(tenant)
            db.flush() # Get tenant.id
            print(f"Tenant criado: {tenant_name}")

        client_email = "cliente@exemplo.com"
        client_pass = "cliente123"
        client_user = db.query(User).filter(User.email == client_email).first()
        
        if not client_user:
            client_user = User(
                email=client_email,
                password_hash=get_password_hash(client_pass),
                full_name="Dono do Cliente",
                role=UserRole.TENANT_OWNER,
                tenant_id=tenant.id,
                is_active=True
            )
            db.add(client_user)
            print(f"Usuário Cliente criado: {client_email} / {client_pass}")

        db.commit()
        print("\nSeed finalizado com sucesso!")
        
    except Exception as e:
        print(f"Erro ao popular banco: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
