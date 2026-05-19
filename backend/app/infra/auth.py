"""fastapi-users wiring: JWT strategy, UserManager, auth backend, role gating.

Import chain (no circular imports):
  infra/auth.py  ← imports get_db from infra/database.py
  dependencies.py ← imports require_role / current_active_user from here
  api/auth_router.py ← imports fastapi_users, auth_backend from here

JWT signing key is read from app.state.secrets at request time — never from
.env or a module-level global.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import PermissionDenied
from app.infra.database import get_db
from app.repositories.orm_models import User

log = structlog.get_logger(__name__)

# 60 minutes access token.  Documented in DECISIONS.md D-P12-02.
_JWT_LIFETIME_SECONDS = 3600


# ── UserManager ──────────────────────────────────────────────────────────────


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Handles registration lifecycle hooks and token secrets from Vault."""

    def __init__(
        self,
        user_db: SQLAlchemyUserDatabase[User, uuid.UUID],
        secret: str,
    ) -> None:
        super().__init__(user_db)
        # Secrets from Vault injected at request time; never class-level constants.
        self.reset_password_token_secret = secret
        self.verification_token_secret = secret

    async def on_after_register(
        self, user: User, request: Request | None = None  # noqa: ARG002
    ) -> None:
        log.info("user.registered", user_id=str(user.id))

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,  # noqa: ARG002
        response: Any = None,  # noqa: ARG002
    ) -> None:
        log.info("user.login", user_id=str(user.id))


# ── DB dependency ─────────────────────────────────────────────────────────────


async def get_user_db(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[SQLAlchemyUserDatabase[User, uuid.UUID], None]:
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[User, uuid.UUID] = Depends(get_user_db),
    request: Request = ...,  # type: ignore[assignment]
) -> AsyncGenerator[UserManager, None]:
    """Inject Vault JWT secret at request time."""
    secret: str = request.app.state.secrets.jwt_secret
    yield UserManager(user_db, secret)


# ── JWT strategy (secret from Vault at request time) ─────────────────────────


def get_jwt_strategy(request: Request) -> JWTStrategy[User, uuid.UUID]:
    return JWTStrategy(
        secret=request.app.state.secrets.jwt_secret,
        lifetime_seconds=_JWT_LIFETIME_SECONDS,
    )


# ── Auth backend ──────────────────────────────────────────────────────────────

auth_backend: AuthenticationBackend[User, uuid.UUID] = AuthenticationBackend(
    name="jwt",
    transport=BearerTransport(tokenUrl="/auth/jwt/login"),
    get_strategy=get_jwt_strategy,
)

# ── FastAPIUsers instance ─────────────────────────────────────────────────────

fastapi_users: FastAPIUsers[User, uuid.UUID] = FastAPIUsers(
    get_user_manager,
    [auth_backend],
)

# Reusable dependency: active user required (no role check).
current_active_user = fastapi_users.current_user(active=True)


# ── Role-based access control ─────────────────────────────────────────────────


def require_role(role: str) -> Any:
    """Factory returning a dependency that asserts the current user has `role`.

    Superusers bypass the role check (is_superuser → all roles allowed).

    Usage:
        @router.get("/admin/...", dependencies=[Depends(require_role("admin"))])
    Or injected for the user object:
        async def endpoint(user: User = Depends(require_role("admin"))):
    """

    async def dependency(user: User = Depends(current_active_user)) -> User:
        if not user.is_superuser and user.role != role:
            raise PermissionDenied(f"Role '{role}' required.")
        return user

    return dependency
