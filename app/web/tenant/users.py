from flask import Blueprint, render_template, redirect, url_for, request, flash, session, abort
from app.web.auth.decorators import login_required, tenant_admin_required
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from sqlalchemy.exc import IntegrityError
import uuid

bp = Blueprint('tenant_users', __name__, url_prefix='/tenant/<tenant_slug>/users')


def get_db_and_tenant(tenant_slug):
    # Cross-tenant check
    if session.get('user_role') != UserRole.SUPER_ADMIN.value and session.get('tenant_slug') != tenant_slug:
        abort(403)
        
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    return db, tenant


@bp.route('/')
@login_required
def list_users(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404
    
    status_filter = (request.args.get('status') or 'active').strip().lower()
    users_query = db.query(User).filter(User.tenant_id == tenant.id)
    if status_filter == 'inactive':
        users_query = users_query.filter(User.is_active == False)
    elif status_filter != 'all':
        status_filter = 'active'
        users_query = users_query.filter(User.is_active == True)
    users = users_query.order_by(User.full_name).all()

    users_all = db.query(User).filter(User.tenant_id == tenant.id).all()
    
    # EstatÃ­sticas
    stats = {
        'total': len(users_all),
        'admins': sum(1 for u in users_all if u.role in [UserRole.TENANT_OWNER, UserRole.TENANT_ADMIN]),
        'technicians': sum(1 for u in users_all if u.role == UserRole.TENANT_TECHNICIAN),
        'active': sum(1 for u in users_all if u.is_active),
        'inactive': sum(1 for u in users_all if not u.is_active),
        'filtered_total': len(users),
    }
    
    db.close()
    return render_template(
        'tenant/users/list.html',
        tenant=tenant,
        users=users,
        stats=stats,
        UserRole=UserRole,
        status_filter=status_filter,
    )


@bp.route('/add', methods=['GET', 'POST'])
@login_required
@tenant_admin_required
def add_user(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404
    
    if request.method == 'POST':
        try:
            email = request.form.get('email')
            
            # Verifica se email jÃ¡ existe
            existing = db.query(User).filter_by(email=email).first()
            if existing:
                flash('Este email jÃ¡ estÃ¡ em uso.', 'error')
                return redirect(url_for('tenant_users.add_user', tenant_slug=tenant_slug))
            
            role_str = request.form.get('role', 'TENANT_TECHNICIAN')
            role = UserRole[role_str]
            
            user = User(
                tenant_id=tenant.id,
                email=email,
                full_name=request.form.get('full_name'),
                password_hash=get_password_hash(request.form.get('password')),
                role=role,
                is_active=True
            )
            db.add(user)
            db.commit()
            flash('UsuÃ¡rio criado com sucesso!', 'success')
            return redirect(url_for('tenant_users.list_users', tenant_slug=tenant_slug))
        except Exception as e:
            flash(f'Erro ao criar usuÃ¡rio: {str(e)}', 'error')
            db.rollback()
        finally:
            db.close()
    
    db.close()
    return render_template(
        'tenant/users/add.html',
        tenant=tenant,
        UserRole=UserRole
    )


@bp.route('/<user_id>/edit', methods=['GET', 'POST'])
@login_required
@tenant_admin_required
def edit_user(tenant_slug, user_id):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404
    
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        db.close()
        return "Invalid user ID", 400
    
    user = db.query(User).filter_by(id=user_uuid).first()
    if not user or str(user.tenant_id) != str(tenant.id):
        db.close()
        return "User not found", 404
    
    if request.method == 'POST':
        try:
            user.full_name = request.form.get('full_name')
            user.email = request.form.get('email')
            user.is_active = request.form.get('is_active') == 'on'
            
            role_str = request.form.get('role')
            if role_str:
                user.role = UserRole[role_str]
            
            password = request.form.get('password')
            if password:
                user.password_hash = get_password_hash(password)
            
            db.commit()
            flash('UsuÃ¡rio atualizado com sucesso!', 'success')
            return redirect(url_for('tenant_users.list_users', tenant_slug=tenant_slug))
        except Exception as e:
            flash(f'Erro ao atualizar: {str(e)}', 'error')
            db.rollback()
    
    db.close()
    return render_template(
        'tenant/users/edit.html',
        tenant=tenant,
        user=user,
        UserRole=UserRole
    )


@bp.route('/<user_id>/delete', methods=['POST'])
@login_required
@tenant_admin_required
def delete_user(tenant_slug, user_id):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404
    
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        flash('ID de usuÃ¡rio invÃ¡lido', 'error')
        return redirect(url_for('tenant_users.list_users', tenant_slug=tenant_slug))
    
    user = db.query(User).filter_by(id=user_uuid).first()
    if not user or str(user.tenant_id) != str(tenant.id):
        flash('UsuÃ¡rio nÃ£o encontrado', 'error')
        db.close()
        return redirect(url_for('tenant_users.list_users', tenant_slug=tenant_slug))

    current_user_id = session.get('user_id')
    if current_user_id and str(user.id) == str(current_user_id):
        flash('NÃ£o Ã© permitido remover o usuÃ¡rio logado.', 'error')
        db.close()
        return redirect(url_for('tenant_users.list_users', tenant_slug=tenant_slug))

    try:
        db.delete(user)
        db.commit()
        flash('UsuÃ¡rio removido com sucesso!', 'success')
    except IntegrityError:
        db.rollback()
        user.is_active = False
        db.commit()
        flash('UsuÃ¡rio possui histÃ³rico vinculado. Foi desativado em vez de removido.', 'warning')
    
    db.close()
    return redirect(url_for('tenant_users.list_users', tenant_slug=tenant_slug))

