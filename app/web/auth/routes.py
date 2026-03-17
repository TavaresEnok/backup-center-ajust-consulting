from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from app.services.auth_service import AuthService
from app.models.user import UserRole
from app.core.database import SessionLocal
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
import logging
from app.core.config import settings
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import smtplib
from email.message import EmailMessage
import redis
import uuid
from sqlalchemy import func

MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 900
_login_attempts = {}
_redis_client = None

try:
    _redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis_client.ping()
except Exception:
    _redis_client = None


def _get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def _prune_attempts(attempts, now):
    return [ts for ts in attempts if (now - ts).total_seconds() < LOGIN_WINDOW_SECONDS]


def _attempt_key(client_ip: str) -> str:
    return f"login_attempts:{client_ip}"


def _get_attempt_count(client_ip: str, now: datetime) -> int:
    if _redis_client:
        value = _redis_client.get(_attempt_key(client_ip))
        return int(value) if value else 0
    attempts = _login_attempts.get(client_ip, [])
    attempts = _prune_attempts(attempts, now)
    _login_attempts[client_ip] = attempts
    return len(attempts)


def _record_failed_attempt(client_ip: str, now: datetime):
    if _redis_client:
        count = _redis_client.incr(_attempt_key(client_ip))
        if count == 1:
            _redis_client.expire(_attempt_key(client_ip), LOGIN_WINDOW_SECONDS)
        return
    attempts = _login_attempts.get(client_ip, [])
    attempts = _prune_attempts(attempts, now)
    attempts.append(now)
    _login_attempts[client_ip] = attempts


def _clear_attempts(client_ip: str):
    if _redis_client:
        _redis_client.delete(_attempt_key(client_ip))
        return
    _login_attempts.pop(client_ip, None)


def _get_serializer():
    return URLSafeTimedSerializer(settings.SECRET_KEY)


def _send_reset_email(to_email: str, reset_url: str):
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        logging.getLogger(__name__).info("reset link: %s", reset_url)
        return

    msg = EmailMessage()
    msg["Subject"] = "Reset de senha - Backup Center"
    msg["From"] = settings.MAIL_FROM
    msg["To"] = to_email
    msg.set_content(
        "Acesse o link para redefinir sua senha:\n\n"
        f"{reset_url}\n\n"
        "Se voce nao solicitou, ignore este email."
    )

    with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(msg)

bp = Blueprint('auth', __name__, url_prefix='/auth')

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        now = datetime.utcnow()
        client_ip = _get_client_ip()
        attempt_count = _get_attempt_count(client_ip, now)
        if attempt_count >= MAX_LOGIN_ATTEMPTS:
            flash('Muitas tentativas. Tente novamente em alguns minutos.', 'error')
            return render_template('auth/login.html')
        
        db = SessionLocal()
        try:
            user = AuthService.authenticate_user(db, email, password)
            if user:
                logging.getLogger(__name__).info("user authenticated: %s", user.email)
                session.permanent = True
                session['user_id'] = str(user.id)
                session['user_name'] = user.full_name
                session['user_role'] = user.role.value
                session['tenant_slug'] = user.tenant.slug if user.tenant else 'admin'
                _clear_attempts(client_ip)
                
                # LOG ACTIVITY: Login Success
                from app.services.activity_service import ActivityService
                tenant_id = user.tenant_id if user.tenant else None
                ActivityService.log_action(db, tenant_id, user.id, "LOGIN", f"User logged in from IP {request.remote_addr}", request.remote_addr)
                
                flash('Login realizado com sucesso!', 'success')
                
                if user.role == UserRole.SUPER_ADMIN:
                    logging.getLogger(__name__).info("superadmin login")
                    return redirect(url_for('superadmin_dashboard.dashboard'))
                
                if user.tenant:
                    logging.getLogger(__name__).info("tenant login: %s", user.tenant.slug)
                    return redirect(url_for('tenant.dashboard', tenant_slug=user.tenant.slug))
                
                logging.getLogger(__name__).warning("user without tenant")
                flash('Sua conta nao possui uma empresa associada.', 'error')
                return redirect(url_for('auth.login'))
            else:
                logging.getLogger(__name__).warning("authentication failed")
                flash('Email ou senha invalidos.', 'error')
                _record_failed_attempt(client_ip, now)
                from app.services.activity_service import ActivityService
                from app.models.user import User
                existing_user = db.query(User).filter(User.email == email).first()
                if existing_user:
                    ActivityService.log_action(db, existing_user.tenant_id, existing_user.id, 'LOGIN_FAILED', f'Failed login from IP {client_ip}', client_ip)
                # LOG ACTIVITY: Login Failed could be logged if we had the tenant/user context, 
                # but since auth failed, we might only log if we found the user by email but pass was wrong.
                # For now, let's skip anonymous failed login logging to avoid DB span without tenant context.
        except Exception as e:
            logging.getLogger(__name__).exception("error during login process")
            flash(f"Erro interno: {str(e)}", "error")
        finally:
            db.close()
            
    return render_template('auth/login.html')

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        company_name = request.form.get('company_name')
        password = request.form.get('password')
        db = SessionLocal()
        try:
            # Check if user exists
            from app.models.user import User
            if db.query(User).filter(User.email == email).first():
                flash('Este email ja esta cadastrado.', 'error')
                return render_template('auth/register.html')
            
            user = AuthService.register_tenant(db, email, password, full_name, company_name)
            
            # LOG ACTIVITY: Register
            from app.services.activity_service import ActivityService
            ActivityService.log_action(db, user.tenant.id, user.id, "REGISTER", f"Tenant registered: {company_name}", request.remote_addr)

            flash('Conta criada com sucesso! Faca login para comecar.', 'success')
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.rollback()
            flash(f'Erro ao criar conta: {str(e)}', 'error')
        finally:
            db.close()
            
    return render_template('auth/register.html')


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('Voce saiu da sua conta.', 'info')
    return redirect(url_for('auth.login'))

@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or "").strip()
        email_lookup = email.lower()
        
        db = SessionLocal()
        try:
            # Check if user exists
            from app.models.user import User
            user = None
            if email:
                user = db.query(User).filter(func.lower(User.email) == email_lookup).first()
            
            if user:
                logging.getLogger(__name__).info("password reset requested for %s", email)
                serializer = _get_serializer()
                token = serializer.dumps({"user_id": str(user.id), "email": user.email})
                reset_url = url_for('auth.reset_password', token=token, _external=True)
                _send_reset_email(user.email, reset_url)

            flash('Se o email estiver cadastrado, voce recebera instrucoes de recuperacao.', 'info')
            return redirect(url_for('auth.login'))
        except Exception:
            logging.getLogger(__name__).exception("error requesting password reset")
            flash('Erro ao processar solicitacao de reset.', 'error')
        finally:
            db.close()
            
    return render_template('auth/forgot_password.html')


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    serializer = _get_serializer()
    try:
        data = serializer.loads(token, max_age=3600)
    except SignatureExpired:
        flash('Token expirado. Solicite um novo reset.', 'error')
        return redirect(url_for('auth.forgot_password'))
    except BadSignature:
        flash('Token invalido.', 'error')
        return redirect(url_for('auth.forgot_password'))

    db = SessionLocal()
    try:
        from app.models.user import User
        try:
            user_uuid = uuid.UUID(data.get("user_id"))
        except (TypeError, ValueError):
            flash('Token invalido.', 'error')
            return redirect(url_for('auth.forgot_password'))

        user = db.query(User).filter(User.id == user_uuid, User.email == data.get("email")).first()
        if not user:
            flash('Usuario nao encontrado.', 'error')
            return redirect(url_for('auth.forgot_password'))

        if request.method == 'POST':
            password = request.form.get('password')
            confirm = request.form.get('confirm_password')
            if not password or password != confirm:
                flash('As senhas nao conferem.', 'error')
                return render_template('auth/reset_password.html', token=token)

            user.password_hash = AuthService.get_password_hash(password)
            db.commit()
            flash('Senha atualizada com sucesso.', 'success')
            return redirect(url_for('auth.login'))
    finally:
        db.close()

    return render_template('auth/reset_password.html', token=token)


