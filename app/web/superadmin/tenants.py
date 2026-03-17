import re
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import case, func, select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.backup import Backup, BackupStatus
from app.models.device import Device
from app.models.tenant import Tenant
from app.models.user import User, UserRole

bp = Blueprint("superadmin_tenants", __name__, url_prefix="/admin/tenants")


@bp.before_request
def check_superadmin():
    if session.get("user_role") != UserRole.SUPER_ADMIN.value:
        return redirect(url_for("auth.login"))


def _normalize_slug(raw: str) -> str:
    value = (raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


@bp.route("/")
def list_tenants():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    subscription = (request.args.get("subscription") or "all").strip().lower()
    ops = (request.args.get("ops") or "all").strip().lower()
    sort = (request.args.get("sort") or "created_desc").strip().lower()
    per_page = request.args.get("per_page", type=int) or 20
    page = request.args.get("page", type=int) or 1
    per_page = per_page if per_page in {20, 50, 100} else 20

    db = SessionLocal()
    try:
        query = db.query(Tenant).options(joinedload(Tenant.plan))
        if q:
            term = f"%{q}%"
            query = query.filter(
                Tenant.name.ilike(term) | Tenant.slug.ilike(term) | Tenant.email.ilike(term)
            )
        if status == "active":
            query = query.filter(Tenant.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(Tenant.is_active.is_(False))
        if subscription != "all":
            query = query.filter(Tenant.subscription_status == subscription)
        if ops == "no_plan":
            query = query.filter(Tenant.plan_id.is_(None))
        elif ops == "payment_risk":
            query = query.filter(Tenant.subscription_status.in_(["trial", "past_due", "canceled"]))
        elif ops == "payment_pending":
            query = query.filter(Tenant.subscription_status.in_(["trial", "past_due"]))
        elif ops == "past_due":
            query = query.filter(Tenant.subscription_status == "past_due")
        elif ops == "canceled":
            query = query.filter(Tenant.subscription_status == "canceled")
        elif ops == "no_devices":
            subq = (
                db.query(Device.tenant_id)
                .group_by(Device.tenant_id)
                .subquery()
            )
            query = query.filter(Tenant.id.notin_(select(subq.c.tenant_id)))
        elif ops == "no_admin":
            subq = (
                db.query(User.tenant_id)
                .filter(User.role.in_([UserRole.TENANT_OWNER, UserRole.TENANT_ADMIN]))
                .group_by(User.tenant_id)
                .subquery()
            )
            query = query.filter(Tenant.id.notin_(select(subq.c.tenant_id)))
        else:
            ops = "all"

        total = query.count()
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        if sort == "name_asc":
            query = query.order_by(Tenant.name.asc())
        elif sort == "name_desc":
            query = query.order_by(Tenant.name.desc())
        elif sort == "created_asc":
            query = query.order_by(Tenant.created_at.asc())
        else:
            sort = "created_desc"
            query = query.order_by(Tenant.created_at.desc())

        tenants = query.offset((page - 1) * per_page).limit(per_page).all()

        tenant_ids = [t.id for t in tenants]
        device_counts = {}
        user_counts = {}
        backup_stats = {}
        if tenant_ids:
            device_counts = {
                str(tid): int(count or 0)
                for tid, count in db.query(Device.tenant_id, func.count(Device.id))
                .filter(Device.tenant_id.in_(tenant_ids))
                .group_by(Device.tenant_id)
                .all()
            }
            user_counts = {
                str(tid): int(count or 0)
                for tid, count in db.query(User.tenant_id, func.count(User.id))
                .filter(User.tenant_id.in_(tenant_ids))
                .group_by(User.tenant_id)
                .all()
            }

            window_24h = datetime.utcnow() - timedelta(hours=24)
            backup_rows = (
                db.query(
                    Device.tenant_id,
                    func.sum(
                        case((Backup.status == BackupStatus.SUCCESS, 1), else_=0)
                    ).label("success_24h"),
                    func.sum(
                        case((Backup.status == BackupStatus.FAILED, 1), else_=0)
                    ).label("failed_24h"),
                )
                .join(Backup, Backup.device_id == Device.id)
                .filter(Device.tenant_id.in_(tenant_ids), Backup.created_at >= window_24h)
                .group_by(Device.tenant_id)
                .all()
            )
            backup_stats = {
                str(tid): {
                    "success_24h": int(success_24h or 0),
                    "failed_24h": int(failed_24h or 0),
                }
                for tid, success_24h, failed_24h in backup_rows
            }

        rows = []
        for tenant in tenants:
            tid = str(tenant.id)
            backup = backup_stats.get(tid, {"success_24h": 0, "failed_24h": 0})
            rows.append(
                {
                    "tenant": tenant,
                    "devices_count": device_counts.get(tid, 0),
                    "users_count": user_counts.get(tid, 0),
                    "success_24h": backup["success_24h"],
                    "failed_24h": backup["failed_24h"],
                }
            )

        stats = {
            "total": db.query(func.count(Tenant.id)).scalar() or 0,
            "active": db.query(func.count(Tenant.id)).filter(Tenant.is_active.is_(True)).scalar() or 0,
            "inactive": db.query(func.count(Tenant.id)).filter(Tenant.is_active.is_(False)).scalar() or 0,
            "subscription_active": (
                db.query(func.count(Tenant.id)).filter(Tenant.subscription_status == "active").scalar() or 0
            ),
            "subscription_trial": (
                db.query(func.count(Tenant.id)).filter(Tenant.subscription_status == "trial").scalar() or 0
            ),
            "subscription_past_due": (
                db.query(func.count(Tenant.id)).filter(Tenant.subscription_status == "past_due").scalar() or 0
            ),
            "subscription_canceled": (
                db.query(func.count(Tenant.id)).filter(Tenant.subscription_status == "canceled").scalar() or 0
            ),
            "no_plan": db.query(func.count(Tenant.id)).filter(Tenant.plan_id.is_(None)).scalar() or 0,
        }

        return render_template(
            "superadmin/tenants/list.html",
            rows=rows,
            stats=stats,
            q=q,
            status=status,
            subscription=subscription,
            ops=ops,
            sort=sort,
            per_page=per_page,
            page=page,
            total=total,
            total_pages=total_pages,
        )
    finally:
        db.close()


@bp.route("/add", methods=["GET", "POST"])
def add_tenant():
    if request.method == "POST":
        db = SessionLocal()
        try:
            name = (request.form.get("name") or "").strip()
            slug = _normalize_slug(request.form.get("slug"))
            owner_email = (request.form.get("owner_email") or "").strip().lower()
            owner_password = (request.form.get("owner_password") or "").strip()
            owner_full_name = (request.form.get("owner_full_name") or "").strip() or f"Admin {name}"
            company_name = (request.form.get("company_name") or "").strip() or name

            if not name or not slug or not owner_email or not owner_password:
                flash("Todos os campos obrigatórios devem ser preenchidos.", "error")
                return render_template("superadmin/tenants/add.html")

            if db.query(Tenant).filter(Tenant.slug == slug).first():
                flash("Esse slug já está em uso por outro tenant.", "error")
                return render_template("superadmin/tenants/add.html")

            if db.query(User).filter(User.email == owner_email).first():
                flash("Esse e-mail já está em uso por outro usuário.", "error")
                return render_template("superadmin/tenants/add.html")

            tenant = Tenant(
                name=name,
                slug=slug,
                email=owner_email,
                company_name=company_name,
                subscription_status="trial",
                is_active=True,
            )
            db.add(tenant)
            db.flush()

            owner = User(
                email=owner_email,
                password_hash=get_password_hash(owner_password),
                full_name=owner_full_name,
                tenant_id=tenant.id,
                role=UserRole.TENANT_OWNER,
                is_active=True,
                email_verified=False,
            )
            db.add(owner)
            db.commit()

            flash("Tenant criado com sucesso.", "success")
            return redirect(url_for("superadmin_tenants.list_tenants"))
        except Exception as exc:
            db.rollback()
            flash(f"Erro ao criar tenant: {str(exc)}", "error")
        finally:
            db.close()

    return render_template("superadmin/tenants/add.html")


@bp.route("/<tenant_id>/edit", methods=["GET", "POST"])
def edit_tenant(tenant_id):
    db = SessionLocal()
    try:
        try:
            tenant_uuid = uuid.UUID(str(tenant_id))
        except Exception:
            flash("Tenant inválido.", "error")
            return redirect(url_for("superadmin_tenants.list_tenants"))

        tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
        if not tenant:
            flash("Tenant não encontrado.", "error")
            return redirect(url_for("superadmin_tenants.list_tenants"))

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            slug = _normalize_slug(request.form.get("slug"))
            company_name = (request.form.get("company_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            subscription_status = (request.form.get("subscription_status") or tenant.subscription_status or "trial").strip().lower()
            allowed_sub_status = {"trial", "active", "past_due", "canceled"}
            if subscription_status not in allowed_sub_status:
                subscription_status = "trial"

            if not name or not slug or not email:
                flash("Nome, slug e e-mail são obrigatórios.", "error")
                return render_template("superadmin/tenants/edit.html", tenant=tenant)

            exists_slug = (
                db.query(Tenant)
                .filter(Tenant.slug == slug, Tenant.id != tenant.id)
                .first()
            )
            if exists_slug:
                flash("Esse slug já está em uso por outro tenant.", "error")
                return render_template("superadmin/tenants/edit.html", tenant=tenant)

            tenant.name = name
            tenant.slug = slug
            tenant.company_name = company_name or None
            tenant.email = email
            tenant.is_active = request.form.get("is_active") == "on"
            tenant.subscription_status = subscription_status

            db.commit()
            flash("Tenant atualizado com sucesso.", "success")
            return redirect(url_for("superadmin_tenants.list_tenants"))

        return render_template("superadmin/tenants/edit.html", tenant=tenant)
    except Exception as exc:
        db.rollback()
        flash(f"Erro ao editar tenant: {str(exc)}", "error")
        return redirect(url_for("superadmin_tenants.list_tenants"))
    finally:
        db.close()
