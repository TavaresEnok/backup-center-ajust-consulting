from app.core.database import SessionLocal
from app.models.user import User
from app.core.security import get_password_hash

def reset_admin():
    db = SessionLocal()
    try:
        # Tenta achar o admin
        admin_email = 'admin@backupcenter.com'
        user = db.query(User).filter(User.email == admin_email).first()
        
        if not user:
            print(f"Usuário {admin_email} não encontrado. Criando...")
            # Se não existir, cria (mas precisaria de mais campos, melhor só avisar)
            # Mas vamos assumir que existe pelo check anterior
            print("Admin não encontrado no banco de dados!")
            return

        print(f"Resetando senha para o usuário: {user.email}")
        new_password = '123456'
        user.hashed_password = get_password_hash(new_password)
        # Se o campo for 'password' no model antigo, ajustar. 
        # Mas pelo padrão usually é hashed_password. 
        # Vou checar atributo:
        if hasattr(user, 'password'):
             user.password = get_password_hash(new_password)
        
        db.commit()
        print(f"SUCESSO: Senha do admin resetada para '{new_password}'")
        
    except Exception as e:
        print(f"Erro ao resetar senha: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    reset_admin()
