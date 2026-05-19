"""Auth and users routers.

Mounts fastapi-users' built-in routes:
  POST /auth/register        — email + password registration
  POST /auth/jwt/login       — returns {access_token, token_type}
  POST /auth/jwt/logout      — invalidates client-side token (no server state)
  GET  /users/me             — current user (UserRead DTO, no hashed_password)
  PATCH /users/me            — update current user
  GET  /users/{id}           — admin only

Additional custom route:
  GET  /admin/ping           — demo endpoint requiring the "admin" role
"""

from fastapi import APIRouter, Depends

from app.domain.user import UserCreate, UserRead, UserUpdate
from app.infra.auth import auth_backend, fastapi_users, require_role
from app.repositories.orm_models import User

router = APIRouter()

# ── Registration ──────────────────────────────────────────────────────────────
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

# ── JWT login / logout ────────────────────────────────────────────────────────
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)

# ── Users /me and /id ─────────────────────────────────────────────────────────
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)


# ── Demo admin-only endpoint ──────────────────────────────────────────────────


@router.get("/admin/ping", tags=["admin"])
async def admin_ping(admin: User = Depends(require_role("admin"))) -> dict[str, str]:
    """Returns 200 only for users with role='admin' or is_superuser=True.

    No token   → 401 (fastapi-users BearerTransport)
    User role  → 403 (require_role raises PermissionDenied)
    Admin role → 200
    """
    return {"status": "ok", "user": admin.email}
