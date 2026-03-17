from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.core.security import get_password_hash, verify_password
from typing import Optional

class AuthService:
    @staticmethod
    def register_tenant(db: Session, email: str, password: str, full_name: str, company_name: str) -> User:
        # Create Tenant
        slug = company_name.lower().replace(" ", "-")
        tenant = Tenant(
            name=company_name,
            slug=slug,
            email=email,
            company_name=company_name
        )
        db.add(tenant)
        db.flush()  # Get tenant.id
        
        # Create User (Owner)
        user = User(
            email=email,
            password_hash=get_password_hash(password),
            full_name=full_name,
            tenant_id=tenant.id,
            role=UserRole.TENANT_OWNER,
            email_verified=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    @staticmethod
    def get_password_hash(password: str) -> str:
        return get_password_hash(password)
