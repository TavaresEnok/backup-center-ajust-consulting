from flask import Blueprint, render_template, request, abort, session
from app.web.auth.decorators import login_required
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.services.activity_service import ActivityService
from app.models.activity_log import ActivityLog
from app.models.user import UserRole
from sqlalchemy.orm import joinedload
from sqlalchemy import or_

bp = Blueprint('tenant_activity', __name__, url_prefix='/tenant/<tenant_slug>/activity')

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
    if not tenant:
        db.close()
        return "Tenant not found", 404
    
    # Filtros opcionais
    action_filter = request.args.get('action')
    view_mode = (request.args.get('view') or 'logs').strip().lower()
    live_mode = request.args.get('live') == '1'
    if view_mode not in {'logs', 'alerts'}:
        view_mode = 'logs'
    
    query = db.query(ActivityLog).options(joinedload(ActivityLog.user)).filter(ActivityLog.tenant_id == tenant.id)
    
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
        
    # Paginação simples
    logs = query.order_by(ActivityLog.created_at.desc()).limit(100).all()
    
    db.close()
    return render_template(
        'tenant/activity/list.html',
        tenant=tenant,
        logs=logs,
        view_mode=view_mode,
        live_mode=live_mode,
    )
