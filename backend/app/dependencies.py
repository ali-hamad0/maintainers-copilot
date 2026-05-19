"""Shared FastAPI dependencies.

get_db        — async DB session (re-exported from infra/database to keep
                existing import paths working for routers and tests).
require_role  — role-based access control factory (from infra/auth).
current_active_user — active-user dependency (from infra/auth).
"""

from app.infra.auth import current_active_user, require_role
from app.infra.database import get_db

__all__ = ["current_active_user", "get_db", "require_role"]
