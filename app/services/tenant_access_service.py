from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.database import engine
from app.models.device import Device
from app.models.plan import Plan
from app.models.tenant import Tenant


class TenantAccessService:
    UNLIMITED_DEFAULT_SLUGS = {"ajust-consulting"}
    PROTECTED_DEFAULT_SLUGS = {"ajust-consulting"}

    @classmethod
    def ensure_schema(cls) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE tenants "
                    "ADD COLUMN IF NOT EXISTS access_unlimited BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE tenants "
                    "ADD COLUMN IF NOT EXISTS protected_system_tenant BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )

    @classmethod
    def apply_builtin_overrides(cls) -> None:
        cls.ensure_schema()
        with engine.begin() as conn:
            for slug in cls.UNLIMITED_DEFAULT_SLUGS:
                conn.execute(
                    text(
                        "UPDATE tenants "
                        "SET access_unlimited = TRUE "
                        "WHERE slug = :slug"
                    ),
                    {"slug": slug},
                )
            for slug in cls.PROTECTED_DEFAULT_SLUGS:
                conn.execute(
                    text(
                        "UPDATE tenants "
                        "SET protected_system_tenant = TRUE "
                        "WHERE slug = :slug"
                    ),
                    {"slug": slug},
                )

    @staticmethod
    def get_device_count(db, tenant_id) -> int:
        return int(db.query(Device.id).filter(Device.tenant_id == tenant_id).count() or 0)

    @staticmethod
    def get_plan_display_name(tenant: Tenant) -> str:
        if tenant.plan:
            return tenant.plan.name
        if bool(getattr(tenant, "access_unlimited", False)):
            return "Acesso ilimitado"
        return "Sem plano"

    @staticmethod
    def can_operate_without_plan(tenant: Tenant) -> bool:
        return bool(tenant.plan_id or getattr(tenant, "access_unlimited", False))

    @staticmethod
    def validate_plan_selection(plan: Plan | None, device_count: int) -> None:
        if not plan:
            raise ValueError("Selecione um plano valido.")
        if device_count > int(plan.max_devices or 0):
            raise ValueError(
                f"Esse plano suporta ate {int(plan.max_devices or 0)} dispositivos, mas este cliente possui {device_count}."
            )

    @staticmethod
    def seed_trial_plan_fields(tenant: Tenant, plan: Plan | None) -> None:
        if not plan:
            return
        tenant.plan_id = plan.id
        tenant.subscription_status = "trial"
        trial_days = int(plan.trial_days or 0)
        tenant.trial_ends_at = datetime.utcnow() + timedelta(days=trial_days) if trial_days > 0 else None
