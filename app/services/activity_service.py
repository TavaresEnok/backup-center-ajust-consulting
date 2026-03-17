from sqlalchemy.orm import Session
from app.models.activity_log import ActivityLog
from typing import Optional, Dict, Any
import json

class ActivityService:
    @staticmethod
    def log_action(db: Session, tenant_id: str, user_id: str, action: str, details: str = None, ip_address: str = None):
        """
        Registra uma ação no log de atividades.
        """
        import uuid
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)
            
        log = ActivityLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            details=details,
            ip_address=ip_address
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def get_tenant_logs(db: Session, tenant_id: str, limit: int = 100):
        """
        Retorna os últimos logs do tenant.
        """
        return db.query(ActivityLog).filter(
            ActivityLog.tenant_id == tenant_id
        ).order_by(ActivityLog.created_at.desc()).limit(limit).all()
