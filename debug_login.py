from app.core.database import SessionLocal
from app.models.user import User
from app.core.security import verify_password, get_password_hash
import sys

def debug_admin_login():
    db = SessionLocal()
    email = "admin@backupcenter.com"
    raw_password = "123456"
    
    print(f"--- DEBUGGING LOGIN FOR {email} ---")
    
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        print("ERROR: User not found in database!")
        return

    print(f"User ID: {user.id}")
    print(f"Is Active: {user.is_active}")
    print(f"Is Superuser: {user.is_superuser}")
    print(f"Stored Hash: {user.hashed_password}")
    
    # Test verify
    try:
        is_valid = verify_password(raw_password, user.hashed_password)
        print(f"RESULT: verify_password('{raw_password}', stored_hash) = {is_valid}")
        
        if is_valid:
            print("SUCCESS: The password in DB matches '123456'. Problem is likely in Route/Form or Browser.")
        else:
            print("FAILURE: The password in DB does NOT match '123456'.")
            print("Attempting to generate new hash and compare...")
            new_hash = get_password_hash(raw_password)
            print(f"New Hash Generated: {new_hash}")
            print(f"Verify New Hash: {verify_password(raw_password, new_hash)}")
            
            # Auto-Fix attempt?
            # print("Applying FIX...")
            # user.hashed_password = new_hash
            # db.commit()
            
    except Exception as e:
        print(f"EXCEPTION during verification: {e}")

if __name__ == "__main__":
    debug_admin_login()
