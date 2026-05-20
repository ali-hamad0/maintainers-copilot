"""Widget configuration endpoints.

GET  /widgets                — list own widget configs (authenticated)
POST /widgets                — create widget config (authenticated)
GET  /widgets/{widget_id}    — get one config (authenticated)
PATCH /widgets/{widget_id}   — update config (owner or superuser)
GET  /embed/{widget_id}      — public embed snippet with Content-Security-Policy
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_active_user, get_db
from app.domain.widget import WidgetConfigCreate, WidgetConfigRead, WidgetConfigUpdate
from app.repositories.orm_models import User
from app.services.widget_service import WidgetService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["widgets"])


@router.get("/widgets", response_model=list[WidgetConfigRead])
async def list_widgets(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
) -> list[WidgetConfigRead]:
    return await WidgetService.list_owned(session, owner_id=user.id)


@router.post("/widgets", response_model=WidgetConfigRead, status_code=201)
async def create_widget(
    body: WidgetConfigCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
) -> WidgetConfigRead:
    result = await WidgetService.create(session, data=body, owner_id=user.id)
    log.info("widget.created", widget_id=str(result.id), owner_id=str(user.id))
    return result


@router.get("/widgets/{widget_id}", response_model=WidgetConfigRead)
async def get_widget(
    widget_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(current_active_user),
) -> WidgetConfigRead:
    return await WidgetService.get(session, widget_id=widget_id)


@router.patch("/widgets/{widget_id}", response_model=WidgetConfigRead)
async def update_widget(
    widget_id: uuid.UUID,
    body: WidgetConfigUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
) -> WidgetConfigRead:
    result = await WidgetService.update(
        session,
        widget_id=widget_id,
        data=body,
        requester_id=user.id,
        is_superuser=user.is_superuser,
    )
    log.info("widget.updated", widget_id=str(widget_id), actor_id=str(user.id))
    return result


@router.get("/embed/{widget_id}", include_in_schema=True, response_class=HTMLResponse)
async def embed_script(
    widget_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Return a JS snippet for embedding the widget with a CSP frame-ancestors header.

    Any host not in widget_config.allowed_origins gets blocked by the browser
    when it tries to embed this snippet inside a frame.

    The snippet sets window.__COPILOT_WIDGET_ID__ so the widget JS can use it.
    """
    origins = await WidgetService.get_allowed_origins(session, widget_id=widget_id)

    frame_ancestors = " ".join(origins) if origins else "'none'"

    snippet = f"<script>\n" f"  window.__COPILOT_WIDGET_ID__ = '{widget_id}';\n" f"</script>\n"

    return HTMLResponse(
        content=snippet,
        headers={
            "Content-Security-Policy": f"frame-ancestors {frame_ancestors}",
            "Cache-Control": "no-store",
        },
    )
