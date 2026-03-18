from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.models.plan import Plan
from app.core.security import get_password_hash, verify_password
from app.services.tenant_access_service import TenantAccessService
from typing import Optional

class AuthService:
    @staticmethod
    def register_tenant(db: Session, email: str, password: str, full_name: str, company_name: str) -> User:
        trial_plan = (
            db.query(Plan)
            .filter(Plan.is_active.is_(True))
            .order_by(Plan.price_monthly.asc(), Plan.created_at.asc())
            .first()
        )
        if not trial_plan:
            raise ValueError("Nao existe plano ativo disponivel para novos clientes.")

        # Create Tenant
        slug = company_name.lower().replace(" ", "-")
        tenant = Tenant(
            name=company_name,
            slug=slug,
            email=email,
            company_name=company_name,
        )
        TenantAccessService.seed_trial_plan_fields(tenant, trial_plan)
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
