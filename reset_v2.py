from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.core.security import get_password_hash

def reset_admin_v2():
    db = SessionLocal()
    try:
        email = 'admin@backupcenter.com'
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            print("Admin User Not Found. Creating...")
            # Optional: Create if missing
            # user = User(email=email, full_name="Super Admin", role=UserRole.SUPER_ADMIN)
            # db.add(user)
            return

        print(f"User Found: {user.email}")
        print(f"Current Role: {user.role}")
        
        # CORRECT FIELD: password_hash
        new_password = '123456'
        user.password_hash = get_password_hash(new_password)
        
        # Ensure correct role
        user.role = UserRole.SUPER_ADMIN
        user.is_active = True
        
        db.commit()
        print(f"SUCCESS: 'password_hash' updated for {user.email}")
        
    except Exception as e:
        print(f"Error resetting password: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    reset_admin_v2()
