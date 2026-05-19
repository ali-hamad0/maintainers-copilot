"""Auth endpoint tests (Phase 12).

Tests the three mandatory cases from the review checklist:
  1. No token   → 401
  2. Valid token but wrong role → 403
  3. Valid token + correct role → 200

And the DTO correctness tests:
  4. UserRead schema does NOT include hashed_password
  5. /users/me JSON response does NOT include hashed_password

Strategy: build a minimal FastAPI app that mounts the auth router and overrides
`get_user_manager` and `get_jwt_strategy` with lightweight mocks so no Vault,
DB, or Redis is required in CI.

Why override get_user_manager and get_jwt_strategy instead of current_active_user:
  fastapi-users' get_users_router() creates its own internal current_active_user
  dependency built from the same get_user_manager and get_jwt_strategy.
  Overriding the two upstream dependencies affects ALL routes in the router,
  whereas overriding current_active_user only works for routes that explicitly
  Depend on the exported symbol.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_users.authentication import JWTStrategy
from jose import jwt

from app.api.errors import register_exception_handlers
from app.domain.user import UserRead
from app.infra.auth import get_jwt_strategy, get_user_manager, require_role
from app.repositories.orm_models import User

# ── Constants ──────────────────────────────────────────────────────────────────

_TEST_SECRET = "test-only-jwt-secret-not-used-in-production"
_USER_ID = uuid.uuid4()
_ADMIN_ID = uuid.uuid4()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_orm_user(role: str, user_id: uuid.UUID | None = None) -> User:
    """Construct an in-memory User ORM object without touching the DB."""
    user = User()
    user.id = user_id or uuid.uuid4()
    user.email = f"{role}@example.com"
    user.hashed_password = "$2b$12$fakehash"
    user.is_active = True
    user.is_superuser = role == "superuser"
    user.is_verified = True
    user.role = role if role != "superuser" else "admin"
    return user


def _sign_token(user_id: uuid.UUID) -> str:
    """Create a minimal JWT that fastapi-users JWTStrategy can decode."""
    return jwt.encode(
        {"sub": str(user_id), "aud": ["fastapi-users:auth"]},
        _TEST_SECRET,
        algorithm="HS256",
    )


def _make_mock_user_manager(fake_user: User | None) -> Any:
    """Return an async generator that yields a mock UserManager.

    The mock's `get` method returns fake_user so JWT strategy lookup succeeds.
    """

    async def _dep() -> AsyncGenerator[Any, None]:
        manager = MagicMock()
        manager.get = AsyncMock(return_value=fake_user)
        manager.parse_id = lambda v: uuid.UUID(v)
        yield manager

    return _dep


def _make_test_app(
    *,
    fake_user: User | None = None,
    current_user_override: Any = None,
) -> FastAPI:
    """Minimal app: error handlers + auth router + mocked auth dependencies.

    get_user_manager and get_jwt_strategy are always mocked so no DB or Vault
    is required.  current_user_override additionally overrides the exported
    current_active_user (used for our custom /admin/ping route).
    """
    from app.api.auth_router import router as auth_router
    from app.infra.auth import current_active_user

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)

    # Mock the two upstream dependencies that need Vault/DB.
    app.dependency_overrides[get_user_manager] = _make_mock_user_manager(fake_user)
    app.dependency_overrides[get_jwt_strategy] = lambda: JWTStrategy(
        secret=_TEST_SECRET, lifetime_seconds=3600
    )

    if current_user_override is not None:
        app.dependency_overrides[current_active_user] = current_user_override

    return app


# ── Test 1: No token → 401 ────────────────────────────────────────────────────


def test_no_token_returns_401() -> None:
    """Calling a protected route without a Bearer token must return 401.

    get_user_manager and get_jwt_strategy are mocked so DB/Vault are not needed.
    With no Authorization header the BearerTransport returns token=None, the
    JWT strategy returns None, and fastapi-users raises 401.
    """
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/users/me")
    assert response.status_code == 401


# ── Test 2: Wrong role → 403 ──────────────────────────────────────────────────


def test_wrong_role_returns_403() -> None:
    """A user with role='user' hitting an admin-only endpoint gets 403."""
    regular_user = _make_orm_user("user")

    app = _make_test_app(
        fake_user=regular_user,
        current_user_override=lambda: regular_user,
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/admin/ping")
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"


# ── Test 3: Valid token + correct role → 200 ──────────────────────────────────


def test_admin_role_returns_200() -> None:
    """A user with role='admin' hitting /admin/ping gets 200."""
    admin_user = _make_orm_user("admin", _ADMIN_ID)

    app = _make_test_app(
        fake_user=admin_user,
        current_user_override=lambda: admin_user,
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/admin/ping")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["user"] == admin_user.email


def test_superuser_bypasses_role_check() -> None:
    """is_superuser=True bypasses the role='admin' gate."""
    super_user = _make_orm_user("superuser")

    app = _make_test_app(
        fake_user=super_user,
        current_user_override=lambda: super_user,
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/admin/ping")
    assert response.status_code == 200


# ── Test 4: UserRead schema does not expose hashed_password ───────────────────


def test_users_me_dto_excludes_hashed_password() -> None:
    """UserRead schema must not include hashed_password field."""
    user_fields = set(UserRead.model_fields.keys())
    assert "hashed_password" not in user_fields, (
        "hashed_password must NOT be in UserRead. "
        "Returning it would leak the bcrypt hash to the client."
    )


def test_user_read_includes_role() -> None:
    """UserRead must include the 'role' field so the client can gate UI features."""
    assert "role" in UserRead.model_fields


# ── Test 5: /users/me response does not include hashed_password ──────────────


def test_users_me_response_excludes_hashed_password() -> None:
    """/users/me endpoint must not return hashed_password in the JSON body.

    Uses a signed JWT so fastapi-users JWT strategy decodes it, calls
    manager.get(user_id) (which the mock returns fake_user for), and returns
    the UserRead DTO.
    """
    fake_user = _make_orm_user("user", _USER_ID)
    token = _sign_token(_USER_ID)

    app = _make_test_app(fake_user=fake_user)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert (
        "hashed_password" not in body
    ), f"hashed_password must not appear in /users/me response. Got keys: {list(body.keys())}"
    assert "role" in body
    assert body["email"] == fake_user.email


# ── Test 6: require_role logic ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_role_raises_permission_denied_for_wrong_role() -> None:
    """require_role("admin") raises PermissionDenied when user.role == "user"."""
    from app.domain.exceptions import PermissionDenied

    regular_user = _make_orm_user("user")
    dep = require_role("admin")
    with pytest.raises(PermissionDenied):
        await dep(user=regular_user)


@pytest.mark.asyncio
async def test_require_role_passes_for_correct_role() -> None:
    """require_role("admin") returns the user when user.role == "admin"."""
    admin_user = _make_orm_user("admin")
    dep = require_role("admin")
    result = await dep(user=admin_user)
    assert result is admin_user
