from app.core.database import SessionLocal, Base, engine
from app.models.user import User, UserRole
from app.core.security import get_password_hash

# Criar tabelas
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Verificar se já existe
existing = db.query(User).filter(User.email == 'admin@system.com').first()
if existing:
    print(f"Superadmin já existe: {existing.email}")
else:
    # Criar superadmin
    admin = User(
        email='admin@system.com',
        password_hash=get_password_hash('admin123'),
        full_name='Super Administrator',
        role=UserRole.SUPER_ADMIN,
        is_active=True,
        email_verified=True
    )
    db.add(admin)
    db.commit()
    print(f"✅ Superadmin criado: admin@system.com / admin123")

db.close()
