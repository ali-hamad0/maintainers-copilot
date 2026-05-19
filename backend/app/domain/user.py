"""User DTOs (Data Transfer Objects).

These are the shapes that cross the HTTP boundary.  They are distinct from the
ORM model (repositories/orm_models.py) and must NEVER expose hashed_password
or other internal fields.

UserRead  — returned by /users/me and POST /auth/register
UserCreate — accepted by POST /auth/register
UserUpdate — accepted by PATCH /users/me
"""

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Public user representation.  hashed_password is NOT included."""

    role: str


class UserCreate(schemas.BaseUserCreate):
    """Registration payload.  password is hashed by UserManager before storage."""

    pass


class UserUpdate(schemas.BaseUserUpdate):
    """Partial update payload for /users/me."""

    role: str | None = None
