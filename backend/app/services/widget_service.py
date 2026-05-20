"""Widget configuration service — business logic for WidgetConfig CRUD."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import NotFoundError, PermissionDenied
from app.domain.widget import WidgetConfigCreate, WidgetConfigRead, WidgetConfigUpdate
from app.repositories.widget_repo import WidgetRepo


class WidgetService:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        data: WidgetConfigCreate,
        owner_id: uuid.UUID,
    ) -> WidgetConfigRead:
        repo = WidgetRepo(session)
        obj = await repo.create(
            name=data.name,
            allowed_origins=data.allowed_origins,
            owner_id=owner_id,
        )
        return WidgetConfigRead.model_validate(obj)

    @staticmethod
    async def list_owned(session: AsyncSession, *, owner_id: uuid.UUID) -> list[WidgetConfigRead]:
        repo = WidgetRepo(session)
        objs = await repo.list_by_owner(owner_id)
        return [WidgetConfigRead.model_validate(o) for o in objs]

    @staticmethod
    async def get(session: AsyncSession, *, widget_id: uuid.UUID) -> WidgetConfigRead:
        repo = WidgetRepo(session)
        obj = await repo.get(widget_id)
        if obj is None:
            raise NotFoundError(f"Widget {widget_id} not found.")
        return WidgetConfigRead.model_validate(obj)

    @staticmethod
    async def update(
        session: AsyncSession,
        *,
        widget_id: uuid.UUID,
        data: WidgetConfigUpdate,
        requester_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> WidgetConfigRead:
        repo = WidgetRepo(session)
        obj = await repo.get(widget_id)
        if obj is None:
            raise NotFoundError(f"Widget {widget_id} not found.")
        if obj.owner_id != requester_id and not is_superuser:
            raise PermissionDenied("You do not own this widget.")
        obj = await repo.update(
            obj,
            name=data.name,
            allowed_origins=data.allowed_origins,
            is_active=data.is_active,
        )
        return WidgetConfigRead.model_validate(obj)

    @staticmethod
    async def get_allowed_origins(session: AsyncSession, *, widget_id: uuid.UUID) -> list[str]:
        """Return the allowed_origins for one widget (empty list if inactive or missing)."""
        repo = WidgetRepo(session)
        obj = await repo.get(widget_id)
        if obj is None or not obj.is_active:
            return []
        return list(obj.allowed_origins)

    @staticmethod
    async def get_all_allowed_origins(session: AsyncSession) -> set[str]:
        """Return every allowed origin across all active widget configs."""
        repo = WidgetRepo(session)
        return await repo.get_all_active_origins()
