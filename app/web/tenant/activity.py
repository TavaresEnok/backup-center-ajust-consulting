import uuid

from flask import Blueprint, render_template, request, abort, session
from app.web.auth.decorators import login_required
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.tenant import Tenant
from app.models.activity_log import ActivityLog
from app.models.user import UserRole
from sqlalchemy.orm import joinedload
from sqlalchemy import or_

bp = Blueprint('tenant_activity', __name__, url_prefix='/tenant/<tenant_slug>/activity')


def _parse_uuid(raw):
    try:
        return uuid.UUID(str(raw))
    except Exception:
        return None


def _can_view_other_users_logs() -> bool:
    role = session.get('user_role')
    return role in {UserRole.SUPER_ADMIN.value, UserRole.TENANT_OWNER.value}


def _parse_positive_int(raw, default: int) -> int:
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value > 0 else default

def get_db_and_tenant(tenant_slug):
    # Cross-tenant check
    if session.get('user_role') != UserRole.SUPER_ADMIN.value and session.get('tenant_slug') != tenant_slug:
        # Se for super admin, pode acessar qualquer tenant. Se não, só o seu.
        # Pequena correção na lógica para garantir segurança
        abort(403)
        
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    return db, tenant

@bp.route('/')
@login_required
def list_activity(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    try:
        if not tenant:
            return "Tenant not found", 404

        # Filtros opcionais
        action_filter = request.args.get('action')
        view_mode = (request.args.get('view') or 'logs').strip().lower()
        live_mode = request.args.get('live') == '1'
        page = _parse_positive_int(request.args.get("page"), 1)
        per_page = min(_parse_positive_int(request.args.get("per_page"), 50), 100)
        if view_mode not in {'logs', 'alerts'}:
            view_mode = 'logs'

        query = db.query(ActivityLog).options(joinedload(ActivityLog.user)).filter(ActivityLog.tenant_id == tenant.id)

        selected_user_id = None
        if settings.AUDIT_USER_SCOPING_ENABLED:
            current_user_id = _parse_uuid(session.get("user_id"))
            if not current_user_id:
                abort(403)

            if _can_view_other_users_logs():
                requested_user_id = _parse_uuid(request.args.get("user_id"))
                selected_user_id = requested_user_id
            else:
                selected_user_id = current_user_id

            if selected_user_id:
                query = query.filter(ActivityLog.user_id == selected_user_id)

        if action_filter:
            query = query.filter(ActivityLog.action == action_filter)

        if view_mode == 'alerts':
            query = query.filter(
                or_(
                    ActivityLog.action.ilike('%FAIL%'),
                    ActivityLog.action.ilike('%ERROR%'),
                    ActivityLog.action.ilike('%ALERT%'),
                    ActivityLog.action.ilike('%WARN%'),
                )
            )

        total_logs = query.count()
        total_pages = max((total_logs + per_page - 1) // per_page, 1)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * per_page
        logs = query.order_by(ActivityLog.created_at.desc()).offset(offset).limit(per_page).all()

        return render_template(
            'tenant/activity/list.html',
            tenant=tenant,
            logs=logs,
            view_mode=view_mode,
            live_mode=live_mode,
            page=page,
            per_page=per_page,
            total_logs=total_logs,
            total_pages=total_pages,
            has_prev=page > 1,
            has_next=page < total_pages,
            prev_page=page - 1 if page > 1 else 1,
            next_page=page + 1 if page < total_pages else total_pages,
        )
    finally:
        db.close()
