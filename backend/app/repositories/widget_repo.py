"""WidgetConfig repository — SQL only, no HTTP, no cache."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.orm_models import WidgetConfig


class WidgetRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        name: str,
        allowed_origins: list[str],
        owner_id: uuid.UUID,
    ) -> WidgetConfig:
        obj = WidgetConfig(name=name, allowed_origins=allowed_origins, owner_id=owner_id)
        self._session.add(obj)
        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    async def get(self, widget_id: uuid.UUID) -> WidgetConfig | None:
        result = await self._session.execute(
            select(WidgetConfig).where(WidgetConfig.id == widget_id)
        )
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID) -> list[WidgetConfig]:
        result = await self._session.execute(
            select(WidgetConfig)
            .where(WidgetConfig.owner_id == owner_id)
            .order_by(WidgetConfig.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_all_active_origins(self) -> set[str]:
        """Return all allowed_origins across all active widget configs."""
        result = await self._session.execute(
            select(WidgetConfig.allowed_origins).where(WidgetConfig.is_active.is_(True))
        )
        origins: set[str] = set()
        for row in result.scalars().all():
            origins.update(row)
        return origins

    async def update(
        self,
        widget: WidgetConfig,
        *,
        name: str | None = None,
        allowed_origins: list[str] | None = None,
        is_active: bool | None = None,
    ) -> WidgetConfig:
        if name is not None:
            widget.name = name
        if allowed_origins is not None:
            widget.allowed_origins = allowed_origins
        if is_active is not None:
            widget.is_active = is_active
        await self._session.flush()
        await self._session.refresh(widget)
        return widget
