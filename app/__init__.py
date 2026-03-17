from flask import Flask, session, request, abort, jsonify, flash, redirect, url_for
from datetime import timedelta, datetime
from fastapi import FastAPI
from zoneinfo import ZoneInfo
from fastapi.middleware.wsgi import WSGIMiddleware
from app.core.config import settings, validate_settings
from app.core.logging_config import setup_logging
import logging
from app.core.database import SessionLocal
from app.models.notification import Notification
from app.models.tenant import Tenant
import uuid
import secrets
from sqlalchemy import text
from redis import Redis
from werkzeug.exceptions import HTTPException
from urllib.parse import urlsplit, urlunsplit

def create_flask_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = settings.SECRET_KEY
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = settings.SESSION_COOKIE_SAMESITE
    app.config['SESSION_COOKIE_SECURE'] = settings.SESSION_COOKIE_SECURE
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=settings.SESSION_MAX_AGE_MINUTES)
    setup_logging()
    validate_settings()

    def _generate_csrf_token():
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    def _sanitize_admin_return_target(raw_value: str | None) -> str | None:
        value = (raw_value or "").strip()
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return None
        if not parsed.path.startswith("/admin/"):
            return None
        return urlunsplit(("", "", parsed.path, parsed.query, ""))

    @app.before_request
    def _csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.path.startswith("/webhooks/billing/mercadopago"):
                return
            session_token = session.get("_csrf_token")
            request_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            if not session_token or not request_token or session_token != request_token:
                abort(400)

    @app.before_request
    def _track_superadmin_tenant_origin():
        if session.get("user_role") != "super_admin":
            session.pop("superadmin_return_url", None)
            session.pop("superadmin_return_label", None)
            session.pop("superadmin_return_tenant_slug", None)
            return

        if not request.path.startswith("/tenant/"):
            return

        admin_return = _sanitize_admin_return_target(request.args.get("admin_return"))
        if not admin_return:
            return

        session["superadmin_return_url"] = admin_return
        session["superadmin_return_label"] = (
            (request.args.get("admin_return_label") or "").strip()[:80] or "Voltar ao Cliente 360"
        )
        current_tenant_slug = (request.view_args or {}).get("tenant_slug")
        if current_tenant_slug:
            session["superadmin_return_tenant_slug"] = current_tenant_slug

    @app.before_request
    def _guard_inactive_tenant_access():
        if session.get("user_role") == "super_admin":
            return
        if not request.path.startswith("/tenant/"):
            return

        tenant_slug = (request.view_args or {}).get("tenant_slug")
        if not tenant_slug:
            return

        db = SessionLocal()
        try:
            tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
            if tenant and not tenant.is_active:
                session.clear()
                flash("Este cliente esta desativado. Entre em contato com o suporte.", "error")
                return redirect(url_for("auth.login"))
        finally:
            db.close()

    app.jinja_env.globals["csrf_token"] = _generate_csrf_token

    app_tz = ZoneInfo(settings.APP_TIMEZONE)

    def _localtime(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo("UTC"))
            return value.astimezone(app_tz)
        return value

    def _format_datetime(value, fmt="%d/%m/%Y %H:%M", default="-"):
        dt = _localtime(value)
        if isinstance(dt, datetime):
            return dt.strftime(fmt)
        return default

    app.jinja_env.filters["localtime"] = _localtime
    app.jinja_env.globals["localtime"] = _localtime
    app.jinja_env.filters["format_datetime"] = _format_datetime
    app.jinja_env.globals["format_datetime"] = _format_datetime

    @app.context_processor
    def inject_notifications():
        user_id = session.get('user_id')
        if not user_id:
            return {}
        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            return {}
        db = SessionLocal()
        try:
            notifications = db.query(Notification).filter(
                Notification.user_id == user_uuid
            ).order_by(Notification.created_at.desc()).limit(5).all()
            unread_count = db.query(Notification).filter(
                Notification.user_id == user_uuid,
                Notification.is_read == False
            ).count()
            return {
                "notifications_list": notifications,
                "notifications_count": unread_count,
            }
        finally:
            db.close()

    @app.context_processor
    def inject_superadmin_tenant_return():
        if session.get("user_role") != "super_admin":
            return {}

        current_tenant_slug = (request.view_args or {}).get("tenant_slug")
        return_url = session.get("superadmin_return_url")
        return_label = session.get("superadmin_return_label") or "Voltar ao Cliente 360"
        return_tenant_slug = session.get("superadmin_return_tenant_slug")
        is_tenant_area = request.path.startswith("/tenant/")
        is_active = bool(
            is_tenant_area
            and return_url
            and current_tenant_slug
            and (not return_tenant_slug or return_tenant_slug == current_tenant_slug)
        )

        return {
            "superadmin_return_url": return_url if is_active else None,
            "superadmin_return_label": return_label,
            "is_superadmin_tenant_view": is_active,
        }
    
    logger = logging.getLogger(__name__)

    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        return e

    @app.errorhandler(Exception)
    def handle_exception(e):
        logger.exception("flask error")
        return "Internal Server Error", 500

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"}), 200

    @app.get("/readyz")
    def readyz():
        db_ok = False
        redis_ok = False

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            logger.exception("readyz db check failed")
        finally:
            db.close()

        try:
            redis_ok = bool(Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1).ping())
        except Exception:
            logger.exception("readyz redis check failed")

        status_code = 200 if db_ok and redis_ok else 503
        return jsonify({
            "status": "ready" if status_code == 200 else "degraded",
            "checks": {
                "database": db_ok,
                "redis": redis_ok,
            }
        }), status_code
    
    # Register Blueprints
    from app.web.superadmin.dashboard import bp as superadmin_dashboard_bp
    app.register_blueprint(superadmin_dashboard_bp)

    from app.web.public.routes import bp as public_bp
    app.register_blueprint(public_bp)
    
    from app.web.auth.routes import bp as auth_bp
    app.register_blueprint(auth_bp)
    
    from app.web.tenant.dashboard import bp as tenant_bp
    app.register_blueprint(tenant_bp)
    
    from app.web.tenant.devices import bp as devices_bp
    app.register_blueprint(devices_bp)
    
    from app.web.tenant.groups import bp as groups_bp
    app.register_blueprint(groups_bp)
    
    from app.web.tenant.users import bp as users_bp
    app.register_blueprint(users_bp)

    from app.web.tenant.settings import bp as tenant_settings_bp
    app.register_blueprint(tenant_settings_bp)

    from app.web.tenant.backups import bp as tenant_backups_bp
    app.register_blueprint(tenant_backups_bp)

    from app.web.tenant.compare import bp as tenant_compare_bp
    app.register_blueprint(tenant_compare_bp)



    from app.web.tenant.activity import bp as tenant_activity_bp
    app.register_blueprint(tenant_activity_bp)

    from app.web.tenant.schedules import bp as tenant_schedules_bp
    app.register_blueprint(tenant_schedules_bp)

    from app.web.tenant.operations import bp as tenant_operations_bp
    app.register_blueprint(tenant_operations_bp)

    from app.web.tenant.reports import bp as tenant_reports_bp
    app.register_blueprint(tenant_reports_bp)

    from app.web.billing.routes import bp as billing_bp
    app.register_blueprint(billing_bp)

    from app.web.billing.webhooks import bp as billing_webhooks_bp
    app.register_blueprint(billing_webhooks_bp)

    from app.web.superadmin.tenants import bp as superadmin_tenants_bp
    app.register_blueprint(superadmin_tenants_bp)

    from app.web.superadmin.device_types import bp as superadmin_device_types_bp
    app.register_blueprint(superadmin_device_types_bp)

    from app.web.superadmin.plans import bp as superadmin_plans_bp
    app.register_blueprint(superadmin_plans_bp)

    from app.web.superadmin.users import bp as superadmin_users_bp
    app.register_blueprint(superadmin_users_bp)

    from app.web.superadmin.billing import bp as superadmin_billing_bp
    app.register_blueprint(superadmin_billing_bp)

    from app.web.tenant.api_tokens import bp as api_tokens_bp
    app.register_blueprint(api_tokens_bp)

    return app


def create_fastapi_app():
    app = FastAPI(title=settings.APP_NAME)

    @app.get("/healthz")
    async def api_healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def api_readyz():
        db_ok = False
        redis_ok = False

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            logging.getLogger(__name__).exception("readyz db check failed")
        finally:
            db.close()

        try:
            redis_ok = bool(Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1).ping())
        except Exception:
            logging.getLogger(__name__).exception("readyz redis check failed")

        return {
            "status": "ready" if db_ok and redis_ok else "degraded",
            "checks": {"database": db_ok, "redis": redis_ok},
        }

    @app.get("/favicon.ico")
    async def api_favicon():
        return {}, 204

    # Include Routers
    from app.api.v1.auth import router as auth_v1_router
    app.include_router(auth_v1_router, prefix="/api/v1/auth", tags=["auth"])

    from app.api.v1.external.routes import router as external_router
    app.include_router(external_router, prefix="/api/v1/external", tags=["external"])

    return app


# The main app will be FastAPI, hosting Flask as a sub-application for SSR
def create_main_app():
    fastapi_app = create_fastapi_app()
    flask_app = create_flask_app()

    # Mount Flask to handle SSR pages
    # FastAPI routes take priority, Flask handles everything else.
    fastapi_app.mount("/", WSGIMiddleware(flask_app))

    return fastapi_app
