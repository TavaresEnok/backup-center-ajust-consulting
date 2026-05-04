import uuid

from app import create_flask_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.activity_log import ActivityLog
from app.models.tenant import Tenant
from app.models.user import User, UserRole


def _build_client():
    app = create_flask_app()
    app.config["TESTING"] = True
    return app.test_client()


def _seed_scope_data(slug: str):
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if not tenant:
            tenant = Tenant(
                id=uuid.uuid4(),
                slug=slug,
                name="Tenant Activity Scope",
                email="scope@test.local",
                is_active=True,
            )
            db.add(tenant)
            db.flush()

        owner = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=f"owner-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="x",
            full_name="Owner User",
            role=UserRole.TENANT_OWNER,
            is_active=True,
        )
        tech = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=f"tech-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="x",
            full_name="Tech User",
            role=UserRole.TENANT_TECHNICIAN,
            is_active=True,
        )
        other = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=f"other-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="x",
            full_name="Other User",
            role=UserRole.TENANT_ADMIN,
            is_active=True,
        )
        db.add_all([owner, tech, other])
        db.flush()

        db.add_all(
            [
                ActivityLog(
                    tenant_id=tenant.id,
                    user_id=tech.id,
                    action="TECH_ONLY_ACTION",
                    details="log-tech",
                    ip_address="127.0.0.1",
                ),
                ActivityLog(
                    tenant_id=tenant.id,
                    user_id=other.id,
                    action="OTHER_USER_ACTION",
                    details="log-other",
                    ip_address="127.0.0.1",
                ),
            ]
        )
        db.commit()
        return tenant.slug, str(owner.id), str(tech.id), str(other.id)
    finally:
        db.close()


def _login_session(client, role: UserRole, tenant_slug: str, user_id: str):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["user_role"] = role.value
        sess["tenant_slug"] = tenant_slug
        sess["_csrf_token"] = "csrf-test-token"


def test_activity_scope_non_master_only_own_logs():
    client = _build_client()
    slug = f"tenant-activity-tech-{uuid.uuid4().hex[:8]}"
    tenant_slug, _, tech_id, other_id = _seed_scope_data(slug)
    settings.AUDIT_USER_SCOPING_ENABLED = True
    try:
        _login_session(client, UserRole.TENANT_TECHNICIAN, tenant_slug, tech_id)
        response = client.get(
            f"/tenant/{tenant_slug}/activity/?user_id={other_id}",
            follow_redirects=False,
        )

        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "TECH_ONLY_ACTION" in body
        assert "OTHER_USER_ACTION" not in body
    finally:
        settings.AUDIT_USER_SCOPING_ENABLED = False


def test_activity_scope_master_can_filter_other_user_logs():
    client = _build_client()
    slug = f"tenant-activity-owner-{uuid.uuid4().hex[:8]}"
    tenant_slug, owner_id, _, other_id = _seed_scope_data(slug)
    settings.AUDIT_USER_SCOPING_ENABLED = True
    try:
        _login_session(client, UserRole.TENANT_OWNER, tenant_slug, owner_id)
        response = client.get(
            f"/tenant/{tenant_slug}/activity/?user_id={other_id}",
            follow_redirects=False,
        )

        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "OTHER_USER_ACTION" in body
    finally:
        settings.AUDIT_USER_SCOPING_ENABLED = False
