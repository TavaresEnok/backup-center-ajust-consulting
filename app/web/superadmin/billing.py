from flask import Blueprint, redirect, render_template, request, session, url_for
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models.invoice import Invoice, InvoiceStatus
from app.models.tenant import Tenant
from app.models.user import UserRole

bp = Blueprint("superadmin_billing", __name__, url_prefix="/admin/billing")


@bp.before_request
def check_superadmin():
    if session.get("user_role") != UserRole.SUPER_ADMIN.value:
        return redirect(url_for("auth.login"))


@bp.route("/")
def list_billing():
    q = (request.args.get("q") or "").strip()
    subscription = (request.args.get("subscription") or "all").strip().lower()
    focus = (request.args.get("focus") or "all").strip().lower()

    db = SessionLocal()
    try:
        tenants_query = db.query(Tenant).options(joinedload(Tenant.plan))
        if q:
            term = f"%{q}%"
            tenants_query = tenants_query.filter(
                or_(
                    Tenant.name.ilike(term),
                    Tenant.slug.ilike(term),
                    Tenant.company_name.ilike(term),
                    Tenant.email.ilike(term),
                )
            )
        if subscription != "all":
            tenants_query = tenants_query.filter(Tenant.subscription_status == subscription)

        if focus == "pending":
            pending_subq = db.query(Invoice.tenant_id).filter(Invoice.status == InvoiceStatus.PENDING).subquery()
            tenants_query = tenants_query.filter(Tenant.id.in_(select(pending_subq.c.tenant_id)))
        elif focus == "failed":
            failed_subq = db.query(Invoice.tenant_id).filter(Invoice.status == InvoiceStatus.FAILED).subquery()
            tenants_query = tenants_query.filter(Tenant.id.in_(select(failed_subq.c.tenant_id)))
        elif focus == "pending_or_failed":
            pending_or_failed_subq = (
                db.query(Invoice.tenant_id)
                .filter(Invoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.FAILED]))
                .subquery()
            )
            tenants_query = tenants_query.filter(Tenant.id.in_(select(pending_or_failed_subq.c.tenant_id)))
        elif focus == "at_risk":
            at_risk_subq = (
                db.query(Invoice.tenant_id)
                .filter(Invoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.FAILED]))
                .subquery()
            )
            tenants_query = tenants_query.filter(
                Tenant.subscription_status.in_(["trial", "past_due", "canceled"]) | Tenant.id.in_(select(at_risk_subq.c.tenant_id))
            )
        elif focus == "active":
            tenants_query = tenants_query.filter(Tenant.subscription_status == "active")
        else:
            focus = "all"
        tenants = tenants_query.order_by(Tenant.name.asc()).all()

        tenant_ids = [t.id for t in tenants]
        invoice_stats = {}
        if tenant_ids:
            rows = (
                db.query(
                    Invoice.tenant_id,
                    func.sum(case((Invoice.status == InvoiceStatus.PAID, 1), else_=0)).label("paid"),
                    func.sum(case((Invoice.status == InvoiceStatus.PENDING, 1), else_=0)).label("pending"),
                    func.sum(case((Invoice.status == InvoiceStatus.FAILED, 1), else_=0)).label("failed"),
                    func.min(
                        case(
                            (
                                Invoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.FAILED]),
                                Invoice.due_date,
                            ),
                            else_=None,
                        )
                    ).label("next_due"),
                )
                .filter(Invoice.tenant_id.in_(tenant_ids))
                .group_by(Invoice.tenant_id)
                .all()
            )
            invoice_stats = {
                str(tenant_id): {
                    "paid": int(paid or 0),
                    "pending": int(pending or 0),
                    "failed": int(failed or 0),
                    "next_due": next_due,
                }
                for tenant_id, paid, pending, failed, next_due in rows
            }

        tenant_rows = []
        for tenant in tenants:
            key = str(tenant.id)
            stat = invoice_stats.get(
                key,
                {"paid": 0, "pending": 0, "failed": 0, "next_due": None},
            )
            tenant_rows.append(
                {
                    "tenant": tenant,
                    "paid": stat["paid"],
                    "pending": stat["pending"],
                    "failed": stat["failed"],
                    "next_due": stat["next_due"],
                }
            )

        recent_invoices = (
            db.query(Invoice)
            .options(joinedload(Invoice.tenant))
            .order_by(Invoice.created_at.desc())
            .limit(80)
            .all()
        )

        stats = {
            "invoices_total": db.query(func.count(Invoice.id)).scalar() or 0,
            "invoices_paid": db.query(func.count(Invoice.id)).filter(Invoice.status == InvoiceStatus.PAID).scalar()
            or 0,
            "invoices_pending": db.query(func.count(Invoice.id))
            .filter(Invoice.status == InvoiceStatus.PENDING)
            .scalar()
            or 0,
            "invoices_failed": db.query(func.count(Invoice.id)).filter(Invoice.status == InvoiceStatus.FAILED).scalar()
            or 0,
            "tenants_active_payment": db.query(func.count(Tenant.id))
            .filter(Tenant.subscription_status == "active")
            .scalar()
            or 0,
            "tenants_pending_payment": db.query(func.count(Tenant.id))
            .filter(Tenant.subscription_status.in_(["trial", "past_due"]))
            .scalar()
            or 0,
        }

        return render_template(
            "superadmin/billing/list.html",
            tenant_rows=tenant_rows,
            recent_invoices=recent_invoices,
            stats=stats,
            q=q,
            subscription=subscription,
            focus=focus,
        )
    finally:
        db.close()
