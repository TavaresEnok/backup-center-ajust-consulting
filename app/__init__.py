from flask import Flask, session, request, abort, jsonify, flash, redirect, url_for, g
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
from app.models.user import User
import uuid
import secrets
from sqlalchemy import text
from redis import Redis
from werkzeug.exceptions import HTTPException
from urllib.parse import urlsplit, urlunsplit
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import joinedload

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
    from app.services.platform_settings_service import PlatformSettingsService
    from app.services.tenant_access_service import TenantAccessService
    from app.services.billing_policy_service import BillingPolicyService
    from app.services.plan_limits_service import PlanLimitsService
    from app.services.auth_service import AuthService
    from app.services.device_subgroup_service import DeviceSubgroupService
    PlatformSettingsService.ensure_schema()
    TenantAccessService.apply_builtin_overrides()
    BillingPolicyService.ensure_schema()
    PlanLimitsService.ensure_schema()
    DeviceSubgroupService.ensure_schema()
    _schema_db = SessionLocal()
    try:
        AuthService.ensure_schema(_schema_db)
    finally:
        _schema_db.close()

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
    def _attach_request_id():
        header_rid = (request.headers.get("X-Request-ID") or "").strip()
        g.request_id = header_rid or str(uuid.uuid4())

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
    def _guard_force_password_change():
        if request.path.startswith('/static/') or request.path.startswith('/healthz'):
            return
        if request.path.startswith('/auth/logout') or request.path.startswith('/auth/forgot-password') or request.path.startswith('/auth/reset-password/'):
            return
        if request.path.startswith('/auth/force-password-change'):
            return
        user_id = session.get('user_id')
        if not user_id:
            return
        db = SessionLocal()
        try:
            try:
                user_uuid = uuid.UUID(str(user_id))
            except Exception:
                session.clear()
                return redirect(url_for('auth.login'))
            user = db.query(User).filter(User.id == user_uuid, User.is_active.is_(True)).first()
            if user and getattr(user, 'must_change_password', False):
                return redirect(url_for('auth.force_password_change'))
        finally:
            db.close()

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
                if getattr(tenant, "deleted_at", None):
                    flash("Este cliente foi movido para a lixeira e não está disponível.", "error")
                elif getattr(tenant, "billing_blocked_at", None):
                    flash("Cliente bloqueado por inadimplencia. Entre em contato com o suporte para reativacao.", "error")
                else:
                    flash("Este cliente esta desativado. Entre em contato com o suporte.", "error")
                return redirect(url_for("auth.login"))
        finally:
            db.close()

    @app.before_request
    def _enforce_https():
        if settings.APP_ENV.lower() == "development":
            return
        host = (request.host or "").split(":")[0]
        proto = request.headers.get("X-Forwarded-Proto", "http").lower()
        is_secure = request.is_secure or proto == "https"
        if is_secure:
            return
        # Allow local/direct access without redirect for troubleshooting/operations.
        if host in {"127.0.0.1", "localhost", "168.194.14.85"}:
            return
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)

    @app.after_request
    def _audit_user_requests(resp):
        from app.services.activity_service import ActivityService

        if not settings.AUDIT_AUTO_LOG_REQUESTS_ENABLED:
            return resp
        if request.path.startswith("/static/"):
            return resp
        if request.path.startswith("/healthz") or request.path.startswith("/readyz"):
            return resp
        if request.path.startswith("/internal/metrics/"):
            return resp
        if request.path.startswith("/webhooks/"):
            return resp

        user_id = session.get("user_id")
        if not user_id:
            return resp

        action = f"HTTP_{request.method.upper()}"
        if request.path.startswith("/tenant/") and request.path.endswith("/activity/"):
            action = "VIEW_ACTIVITY_LOGS"

        db = SessionLocal()
        try:
            tenant_id = None
            tenant_slug = (request.view_args or {}).get("tenant_slug") or session.get("tenant_slug")
            if tenant_slug and tenant_slug != "admin":
                tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
                tenant_id = tenant.id if tenant else None

            details = {
                "resource_type": "http_request",
                "resource_id": request.path,
                "result": "success" if 200 <= resp.status_code < 400 else "error",
                "message": f"{request.method} {request.path} -> {resp.status_code}",
                "status_code": resp.status_code,
                "endpoint": request.endpoint or "",
            }
            ActivityService.log_action(
                db=db,
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                details=details,
                ip_address=request.remote_addr,
            )
        except Exception:
            db.rollback()
        finally:
            db.close()
        return resp

    @app.after_request
    def _set_security_headers(resp):
        resp.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        proto = request.headers.get("X-Forwarded-Proto", "http").lower()
        is_secure = request.is_secure or proto == "https"
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://cdn.jsdelivr.net/npm; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://unpkg.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "connect-src 'self' wss: ws: https://cdn.jsdelivr.net https://unpkg.com; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self'"
        )
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
        return resp

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

    @app.context_processor
    def inject_tenant_billing_alert():
        if session.get("user_role") == "super_admin":
            return {}
        tenant_slug = (session.get("tenant_slug") or "").strip()
        if not tenant_slug or tenant_slug == "admin":
            return {}

        db = SessionLocal()
        try:
            tenant = (
                db.query(Tenant)
                .options(joinedload(Tenant.plan))
                .filter(Tenant.slug == tenant_slug)
                .first()
            )
            if not tenant or not tenant.is_active:
                return {}

            from app.services.billing_policy_service import BillingPolicyService

            alert = BillingPolicyService.build_runtime_alert(tenant)
            if not alert:
                return {}
            return {
                "tenant_billing_alert": alert,
                "tenant_billing_url": url_for("billing.dashboard", tenant_slug=tenant_slug),
            }
        finally:
            db.close()
    
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

    @app.get("/internal/metrics/backups")
    def backup_metrics():
        from app.services.backup_observability import (
            metrics_token_is_valid,
            render_prometheus_metrics,
        )

        auth_header = request.headers.get("Authorization")
        if not metrics_token_is_valid(auth_header):
            return "forbidden\n", 403

        payload = render_prometheus_metrics()
        return payload, 200, {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"}
    
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

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        proto = request.headers.get("x-forwarded-proto", "http").lower()
        is_secure = request.url.scheme == "https" or proto == "https"
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://cdn.jsdelivr.net/npm; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://unpkg.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "connect-src 'self' wss: ws: https://cdn.jsdelivr.net https://unpkg.com; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self'"
        )
        headers = {
            "Content-Security-Policy": csp,
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
        if is_secure:
            headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        for k, v in headers.items():
            if k not in response.headers:
                response.headers[k] = v
        return response

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

    # ── WebSocket: Terminal Interativo Jump Host ──────────────────────────
    from fastapi import WebSocket, WebSocketDisconnect
    import asyncio, json as _json, select as _select, uuid as _uuid, logging as _logging

    _ws_log = _logging.getLogger("backup_center.ws_console")

    @app.websocket("/ws/jump-console/{token}")
    async def jump_console_ws(websocket: WebSocket, token: str):
        from app.services.realtime_backup_logs import get_redis_client
        from app.core.database import SessionLocal
        from app.services.device_service import DeviceGroupService
        from app.services.connection_test_service import connection_test_service as _cts

        await websocket.accept()

        redis_client = get_redis_client()
        if not redis_client:
            await websocket.close(code=1011, reason="Redis indisponivel")
            return

        raw = redis_client.get(f"backup_center:wsconsole:{token}")
        if not raw:
            await websocket.close(code=4401, reason="Token invalido ou expirado")
            return
        redis_client.delete(f"backup_center:wsconsole:{token}")

        try:
            payload = _json.loads(raw)
        except Exception:
            await websocket.close(code=1011, reason="Payload invalido")
            return

        group_id_str = payload.get("group_id", "")
        tenant_id_str = payload.get("tenant_id", "")

        db = SessionLocal()
        ssh_client = None
        channel = None
        try:
            group = DeviceGroupService.get_group(db, _uuid.UUID(group_id_str))
            if not group or str(group.tenant_id) != tenant_id_str:
                await websocket.close(code=4403, reason="Grupo nao encontrado")
                return

            jump_cfg = _cts._build_jump_host_config(group)
            if not jump_cfg:
                await websocket.send_bytes(b"\r\n\x1b[31mJump Host nao configurado.\x1b[0m\r\n")
                await websocket.close(code=1000)
                return

            loop = asyncio.get_event_loop()

            try:
                ssh_client = await loop.run_in_executor(None, lambda: _cts._open_jump_client(jump_cfg, 12))
            except Exception as e:
                await websocket.send_bytes(f"\r\n\x1b[31mFalha ao conectar no Jump Host: {e}\x1b[0m\r\n".encode())
                await websocket.close(code=1000)
                return

            try:
                channel = await loop.run_in_executor(
                    None,
                    lambda: ssh_client.invoke_shell(term="xterm-256color", width=220, height=50)
                )
            except Exception as e:
                await websocket.send_bytes(f"\r\n\x1b[31mFalha ao abrir shell: {e}\x1b[0m\r\n".encode())
                await websocket.close(code=1000)
                return

            def _read_channel():
                try:
                    ready, _, _ = _select.select([channel], [], [], 0.1)
                    if ready:
                        data = channel.recv(8192)
                        return data if data else None
                    return b""
                except Exception:
                    return None

            async def _send_output():
                while True:
                    data = await loop.run_in_executor(None, _read_channel)
                    if data is None:
                        try:
                            await websocket.send_bytes(b"\r\n\x1b[33mSessao SSH encerrada.\x1b[0m\r\n")
                            await websocket.close(code=1000)
                        except Exception:
                            pass
                        break
                    if data:
                        try:
                            await websocket.send_bytes(data)
                        except Exception:
                            break

            async def _recv_input():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        data = msg.get("bytes") or (msg.get("text") or "").encode()
                        if not data:
                            continue
                        try:
                            parsed = _json.loads(data.decode("utf-8", errors="ignore"))
                            if parsed.get("type") == "resize":
                                channel.resize_pty(
                                    width=max(1, int(parsed.get("cols", 80))),
                                    height=max(1, int(parsed.get("rows", 24)))
                                )
                                continue
                        except Exception:
                            pass
                        channel.send(data)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            send_task = asyncio.create_task(_send_output())
            recv_task = asyncio.create_task(_recv_input())
            done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception:
            _ws_log.exception("Erro no WebSocket jump console")
            try:
                await websocket.close(code=1011)
            except Exception:
                pass
        finally:
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            if ssh_client is not None:
                try:
                    ssh_client.close()
                except Exception:
                    pass
            db.close()

    return app


# The main app will be FastAPI, hosting Flask as a sub-application for SSR
def create_main_app():
    fastapi_app = create_fastapi_app()
    flask_app = create_flask_app()

    # Mount Flask to handle SSR pages
    # FastAPI routes take priority, Flask handles everything else.
    fastapi_app.mount("/", WSGIMiddleware(flask_app))

    return fastapi_app
