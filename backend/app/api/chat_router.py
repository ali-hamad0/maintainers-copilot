"""POST /chat — authenticated SSE chatbot endpoint.

The route:
  1. Validates the JWT (current_active_user dependency).
  2. Resolves or creates a conversation_id.
  3. Delegates to AgentService.run() which yields SSE strings.
  4. Returns a StreamingResponse with media_type="text/event-stream".

NO business logic lives here.  AgentService owns the tool-calling loop.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_active_user, get_db
from app.repositories.orm_models import User

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(
        description="The maintainer's message or question.",
        min_length=1,
        max_length=4_000,
    )
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="Resume an existing conversation. Omit to start a new one.",
    )


async def _generate_events(
    stream: AsyncGenerator[str, None],
) -> AsyncGenerator[bytes, None]:
    """Wrap the agent's string generator into bytes for StreamingResponse."""
    try:
        async for chunk in stream:
            yield chunk.encode("utf-8")
    except Exception as exc:
        log.exception("chat.stream_error", error=str(exc))
        error_event = 'data: {"type":"error","message":"Stream interrupted."}\n\n'
        yield error_event.encode("utf-8")


@router.post("")
async def chat(
    request: Request,
    body: ChatRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
) -> StreamingResponse:
    """Stream a chatbot response as Server-Sent Events.

    Events (each prefixed with `data: ` and terminated by double newline):
      {"type":"tool_start","tool":"<name>","args":{...}}
      {"type":"tool_end","tool":"<name>","result":{...},"is_error":false}
      {"type":"text_delta","content":"..."}
      {"type":"done"}
      {"type":"error","message":"..."}
    """
    agent = request.app.state.agent
    redis: Any = request.app.state.redis
    request_id: str | None = getattr(request.state, "request_id", None)

    conversation_id = body.conversation_id or uuid.uuid4()

    log.info(
        "chat.request",
        user_id=str(user.id),
        conversation_id=str(conversation_id),
        request_id=request_id,
    )

    stream = await agent.run(
        user_message=body.message,
        conversation_id=conversation_id,
        user_id=user.id,
        session=session,
        redis=redis,
        request_id=request_id,
    )

    return StreamingResponse(
        _generate_events(stream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Conversation-Id": str(conversation_id),
        },
    )
