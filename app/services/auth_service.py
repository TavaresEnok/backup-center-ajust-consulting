from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.models.plan import Plan
from app.core.security import get_password_hash, verify_password
from app.services.tenant_access_service import TenantAccessService
from typing import Optional
import re
import uuid

class AuthService:
    @staticmethod
    def _build_unique_slug(db: Session, company_name: str) -> str:
        base = re.sub(r"[^a-z0-9-]+", "-", (company_name or "").strip().lower())
        base = re.sub(r"-{2,}", "-", base).strip("-") or "cliente"
        slug = base
        suffix = 1
        while db.query(Tenant.id).filter(Tenant.slug == slug).first():
            suffix += 1
            slug = f"{base}-{suffix}"
        return slug

    @staticmethod
    def register_tenant(
        db: Session,
        email: str,
        password: str,
        full_name: str,
        company_name: str,
        plan_id: str | None = None,
        activate_trial: bool = True,
    ) -> User:
        selected_plan = None
        if plan_id:
            try:
                plan_uuid = uuid.UUID(str(plan_id))
            except Exception as exc:
                raise ValueError("Plano invalido.") from exc
            selected_plan = (
                db.query(Plan)
                .filter(Plan.id == plan_uuid, Plan.is_active.is_(True))
                .first()
            )
        if not selected_plan:
            selected_plan = (
                db.query(Plan)
                .filter(Plan.is_active.is_(True))
                .order_by(Plan.price_monthly.asc(), Plan.created_at.asc())
                .first()
            )
        if not selected_plan:
            raise ValueError("Nao existe plano ativo disponivel para novos clientes.")

        # Create Tenant
        slug = AuthService._build_unique_slug(db, company_name)
        tenant = Tenant(
            name=company_name,
            slug=slug,
            email=email,
            company_name=company_name,
            is_active=bool(activate_trial),
        )
        if activate_trial:
            TenantAccessService.seed_trial_plan_fields(tenant, selected_plan)
        else:
            TenantAccessService.seed_pending_payment_plan_fields(tenant, selected_plan)
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
