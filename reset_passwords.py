"""
Script para resetar senhas de usuários e garantir acesso ao sistema.
Execute com: python reset_passwords.py
"""

from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.core.security import get_password_hash  # Usa o mesmo algoritmo do sistema

def main():
    db = SessionLocal()
    
    try:
        print("=" * 50)
        print("RESET DE SENHAS - Backup Center")
        print("=" * 50)
        
        # 1. Busca o primeiro usuário tenant_owner para resetar senha
        owner = db.query(User).filter(User.role == UserRole.TENANT_OWNER).first()
        if owner:
            new_password = "admin123"
            owner.password_hash = get_password_hash(new_password)
            db.commit()
            print(f"\n✅ Senha resetada para TENANT OWNER:")
            print(f"   Email: {owner.email}")
            print(f"   Nova senha: {new_password}")
            
            # Busca o tenant deste owner
            if owner.tenant_id:
                tenant = db.query(Tenant).filter(Tenant.id == owner.tenant_id).first()
                if tenant:
                    print(f"   URL: http://localhost:8000/tenant/{tenant.slug}/dashboard")
        
        # 2. Também reseta a senha do primeiro admin
        admin = db.query(User).filter(User.role == UserRole.TENANT_ADMIN).first()
        if admin:
            new_password = "admin123"
            admin.password_hash = get_password_hash(new_password)
            db.commit()
            print(f"\n✅ Senha resetada para TENANT ADMIN:")
            print(f"   Email: {admin.email}")
            print(f"   Nova senha: {new_password}")
        
        # 3. Verifica se existe super_admin, se não cria
        super_admin = db.query(User).filter(User.role == UserRole.SUPER_ADMIN).first()
        if not super_admin:
            super_admin = User(
                email="admin@backupcenter.com",
                full_name="Super Admin",
                password_hash=get_password_hash("admin123"),
                role=UserRole.SUPER_ADMIN,
                is_active=True
            )
            db.add(super_admin)
            db.commit()
            print(f"\n✅ SUPER ADMIN criado:")
            print(f"   Email: admin@backupcenter.com")
            print(f"   Senha: admin123")
        else:
            super_admin.password_hash = get_password_hash("admin123")
            db.commit()
            print(f"\n✅ Senha resetada para SUPER ADMIN:")
            print(f"   Email: {super_admin.email}")
            print(f"   Nova senha: admin123")
        
        print("\n" + "=" * 50)
        print("PRONTO! Use as credenciais acima para acessar.")
        print("=" * 50)
        
    except Exception as e:
        print(f"ERRO: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
