import uuid

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.activity_log import ActivityLog
from app.models.api_token import ApiToken
from app.models.backup import Backup
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.web.auth.decorators import login_required

bp = Blueprint("superadmin_users", __name__, url_prefix="/admin/users")


ROLE_LABELS = {
    UserRole.SUPER_ADMIN: "Super Admin",
    UserRole.TENANT_OWNER: "Administrador Master",
    UserRole.TENANT_ADMIN: "Administrador",
    UserRole.TENANT_TECHNICIAN: "Tecnico",
    UserRole.TENANT_VIEWER: "Visualizador",
}

TENANT_ALLOWED_ROLES = [
    UserRole.TENANT_OWNER,
    UserRole.TENANT_ADMIN,
    UserRole.TENANT_TECHNICIAN,
    UserRole.TENANT_VIEWER,
]


@bp.before_request
def check_superadmin():
    if session.get("user_role") != UserRole.SUPER_ADMIN.value:
        return redirect(url_for("auth.login"))


def _parse_role(raw):
    value = (raw or "").strip()
    if not value:
        return None
    for role in UserRole:
        if value == role.value or value == role.name:
            return role
    return None


def _parse_uuid(raw):
    try:
        return uuid.UUID(str(raw))
    except Exception:
        return None


def _tenant_options(db):
    tenants = db.query(Tenant).order_by(Tenant.name.asc()).all()
    return [{"id": str(t.id), "name": t.name, "slug": t.slug} for t in tenants]


@bp.route("/")
@login_required
def list_users():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    role_filter = (request.args.get("role") or "all").strip()
    tenant_filter = (request.args.get("tenant") or "").strip()

    db = SessionLocal()
    try:
        query = db.query(User).options(joinedload(User.tenant))

        if tenant_filter == "__platform__":
            query = query.filter(User.tenant_id.is_(None))
        else:
            tenant_uuid = _parse_uuid(tenant_filter)
            if tenant_filter and tenant_uuid:
                query = query.filter(User.tenant_id == tenant_uuid)
            elif tenant_filter:
                tenant_filter = ""

        if status == "active":
            query = query.filter(User.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(User.is_active.is_(False))
        else:
            status = "all"

        parsed_role = _parse_role(role_filter)
        if parsed_role:
            query = query.filter(User.role == parsed_role)
            role_filter = parsed_role.value
        else:
            role_filter = "all"

        if q:
            term = f"%{q}%"
            query = query.outerjoin(Tenant, User.tenant_id == Tenant.id).filter(
                or_(
                    User.full_name.ilike(term),
                    User.email.ilike(term),
                    Tenant.name.ilike(term),
                    Tenant.slug.ilike(term),
                )
            )

        users = query.order_by(User.created_at.desc()).all()

        rows = []
        for user in users:
            rows.append(
                {
                    "user": user,
                    "role_label": ROLE_LABELS.get(user.role, user.role.value),
                    "tenant_name": user.tenant.name if user.tenant else "ADMIN",
                    "tenant_slug": user.tenant.slug if user.tenant else "admin",
                }
            )

        stats = {
            "total": db.query(func.count(User.id)).scalar() or 0,
            "active": db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0,
            "inactive": db.query(func.count(User.id)).filter(User.is_active.is_(False)).scalar() or 0,
            "admin_users": db.query(func.count(User.id)).filter(User.role == UserRole.SUPER_ADMIN).scalar() or 0,
            "tenant_users": db.query(func.count(User.id)).filter(User.tenant_id.isnot(None)).scalar() or 0,
        }

        role_options = [{"value": "all", "label": "Todas funcoes"}] + [
            {"value": role.value, "label": ROLE_LABELS.get(role, role.value)} for role in UserRole
        ]

        return render_template(
            "superadmin/users/list.html",
            rows=rows,
            stats=stats,
            role_options=role_options,
            tenant_options=_tenant_options(db),
            q=q,
            status=status,
            role_filter=role_filter,
            tenant_filter=tenant_filter,
        )
    finally:
        db.close()


@bp.route("/add", methods=["GET", "POST"])
@login_required
def add_user():
    db = SessionLocal()
    try:
        if request.method == "POST":
            user_scope = (request.form.get("user_scope") or "tenant").strip().lower()
            full_name = (request.form.get("full_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = (request.form.get("password") or "").strip()
            role = _parse_role(request.form.get("role"))
            tenant_id = _parse_uuid(request.form.get("tenant_id"))

            if not full_name or not email or not password:
                flash("Nome, e-mail e senha sao obrigatorios.", "error")
                return render_template(
                    "superadmin/users/add.html",
                    tenant_options=_tenant_options(db),
                    tenant_roles=TENANT_ALLOWED_ROLES,
                    role_labels=ROLE_LABELS,
                )
            if len(password) < 6:
                flash("A senha deve ter no minimo 6 caracteres.", "error")
                return render_template(
                    "superadmin/users/add.html",
                    tenant_options=_tenant_options(db),
                    tenant_roles=TENANT_ALLOWED_ROLES,
                    role_labels=ROLE_LABELS,
                )
            if db.query(User).filter(func.lower(User.email) == email).first():
                flash("Ja existe um usuario com esse e-mail.", "error")
                return render_template(
                    "superadmin/users/add.html",
                    tenant_options=_tenant_options(db),
                    tenant_roles=TENANT_ALLOWED_ROLES,
                    role_labels=ROLE_LABELS,
                )

            if user_scope == "platform":
                role = UserRole.SUPER_ADMIN
                tenant_id = None
            else:
                if role not in TENANT_ALLOWED_ROLES:
                    role = UserRole.TENANT_TECHNICIAN
                if not tenant_id:
                    flash("Selecione um tenant para usuario de cliente.", "error")
                    return render_template(
                        "superadmin/users/add.html",
                        tenant_options=_tenant_options(db),
                        tenant_roles=TENANT_ALLOWED_ROLES,
                        role_labels=ROLE_LABELS,
                    )
                tenant_exists = db.query(Tenant).filter(Tenant.id == tenant_id).first()
                if not tenant_exists:
                    flash("Tenant informado nao existe.", "error")
                    return render_template(
                        "superadmin/users/add.html",
                        tenant_options=_tenant_options(db),
                        tenant_roles=TENANT_ALLOWED_ROLES,
                        role_labels=ROLE_LABELS,
                    )

            user = User(
                tenant_id=tenant_id,
                email=email,
                full_name=full_name,
                password_hash=get_password_hash(password),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.commit()
            flash("Usuario criado com sucesso.", "success")
            return redirect(url_for("superadmin_users.list_users"))

        return render_template(
            "superadmin/users/add.html",
            tenant_options=_tenant_options(db),
            tenant_roles=TENANT_ALLOWED_ROLES,
            role_labels=ROLE_LABELS,
        )
    except Exception as exc:
        db.rollback()
        flash(f"Erro ao criar usuario: {str(exc)}", "error")
        return redirect(url_for("superadmin_users.list_users"))
    finally:
        db.close()


@bp.route("/<user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id):
    def _render_edit(db, user):
        return render_template(
            "superadmin/users/edit.html",
            user=user,
            selected_tenant_id=str(user.tenant_id) if user.tenant_id else "",
            tenant_options=_tenant_options(db),
            tenant_roles=TENANT_ALLOWED_ROLES,
            role_labels=ROLE_LABELS,
        )

    db = SessionLocal()
    try:
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            flash("Usuario invalido.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        user = db.query(User).options(joinedload(User.tenant)).filter(User.id == user_uuid).first()
        if not user:
            flash("Usuario nao encontrado.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        if request.method == "POST":
            full_name = (request.form.get("full_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = (request.form.get("password") or "").strip()
            user_scope = (request.form.get("user_scope") or "tenant").strip().lower()
            role = _parse_role(request.form.get("role"))
            tenant_id = _parse_uuid(request.form.get("tenant_id"))
            is_active = request.form.get("is_active") == "on"

            if not full_name or not email:
                flash("Nome e e-mail sao obrigatorios.", "error")
                return _render_edit(db, user)

            email_exists = (
                db.query(User)
                .filter(func.lower(User.email) == email, User.id != user.id)
                .first()
            )
            if email_exists:
                flash("Ja existe outro usuario com esse e-mail.", "error")
                return _render_edit(db, user)

            current_user_id = session.get("user_id")
            if current_user_id and str(user.id) == str(current_user_id) and not is_active:
                flash("Nao e permitido desativar o usuario logado.", "error")
                return _render_edit(db, user)

            user.full_name = full_name
            user.email = email
            user.is_active = is_active

            if user_scope == "platform":
                user.tenant_id = None
                user.role = UserRole.SUPER_ADMIN
            else:
                if current_user_id and str(user.id) == str(current_user_id):
                    flash("Nao e permitido remover seu proprio perfil da plataforma.", "error")
                    return _render_edit(db, user)
                if role not in TENANT_ALLOWED_ROLES:
                    role = UserRole.TENANT_TECHNICIAN
                if not tenant_id:
                    flash("Selecione um tenant para usuario de cliente.", "error")
                    return _render_edit(db, user)
                tenant_exists = db.query(Tenant).filter(Tenant.id == tenant_id).first()
                if not tenant_exists:
                    flash("Tenant informado nao existe.", "error")
                    return _render_edit(db, user)
                user.tenant_id = tenant_id
                user.role = role

            if password:
                if len(password) < 6:
                    flash("A senha deve ter no minimo 6 caracteres.", "error")
                    return _render_edit(db, user)
                user.password_hash = get_password_hash(password)

            db.commit()
            flash("Usuario atualizado com sucesso.", "success")
            return redirect(url_for("superadmin_users.list_users"))

        return _render_edit(db, user)
    except Exception as exc:
        db.rollback()
        flash(f"Erro ao atualizar usuario: {str(exc)}", "error")
        return redirect(url_for("superadmin_users.list_users"))
    finally:
        db.close()


@bp.route("/<user_id>/toggle-active", methods=["POST"])
@login_required
def toggle_active(user_id):
    db = SessionLocal()
    try:
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            flash("Usuario invalido.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        user = db.query(User).filter(User.id == user_uuid).first()
        if not user:
            flash("Usuario nao encontrado.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        current_user_id = session.get("user_id")
        if current_user_id and str(user.id) == str(current_user_id) and user.is_active:
            flash("Nao e permitido desativar o usuario logado.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        user.is_active = not bool(user.is_active)
        db.commit()
        flash("Status do usuario atualizado com sucesso.", "success")
        return redirect(url_for("superadmin_users.list_users"))
    except Exception as exc:
        db.rollback()
        flash(f"Erro ao atualizar status: {str(exc)}", "error")
        return redirect(url_for("superadmin_users.list_users"))
    finally:
        db.close()


@bp.route("/<user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    db = SessionLocal()
    try:
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            flash("Usuario invalido.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        user = db.query(User).options(joinedload(User.tenant)).filter(User.id == user_uuid).first()
        if not user:
            flash("Usuario nao encontrado.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        current_user_id = session.get("user_id")
        if current_user_id and str(user.id) == str(current_user_id):
            flash("Nao e permitido apagar o usuario logado.", "error")
            return redirect(url_for("superadmin_users.list_users"))

        if user.role == UserRole.SUPER_ADMIN:
            super_admin_count = (
                db.query(func.count(User.id))
                .filter(User.role == UserRole.SUPER_ADMIN)
                .scalar()
                or 0
            )
            if super_admin_count <= 1:
                flash("Nao e permitido apagar o ultimo super admin da plataforma.", "error")
                return redirect(url_for("superadmin_users.list_users"))

        # Preserva historico e remove dependencias que impedem exclusao fisica.
        db.query(ActivityLog).filter(ActivityLog.user_id == user.id).update(
            {ActivityLog.user_id: None},
            synchronize_session=False,
        )
        db.query(Backup).filter(Backup.triggered_by_user_id == user.id).update(
            {Backup.triggered_by_user_id: None},
            synchronize_session=False,
        )
        db.query(ApiToken).filter(ApiToken.user_id == user.id).delete(synchronize_session=False)
        db.query(Notification).filter(Notification.user_id == user.id).delete(synchronize_session=False)
        db.flush()

        user_label = user.full_name or user.email
        db.delete(user)
        db.commit()
        flash(f"Usuario {user_label} removido com sucesso.", "success")
        return redirect(url_for("superadmin_users.list_users"))
    except Exception as exc:
        db.rollback()
        flash(f"Erro ao apagar usuario: {str(exc)}", "error")
        return redirect(url_for("superadmin_users.list_users"))
    finally:
        db.close()
