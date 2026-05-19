"""Short-term memory backed by Redis.

Stores the message history for an active conversation as a Redis list.

Key schema : memory:short:{conversation_id}
Value      : JSON-serialised list items appended one at a time (RPUSH)
TTL        : 1800 s (30 minutes) — see D-P12-01 in DECISIONS.md

The TTL is refreshed on every write so an active session never expires mid-
conversation.  On read the TTL is NOT refreshed (reads are idempotent).
"""

import json
import uuid
from typing import Any

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

# 30 minutes.  Long enough for a coffee break; short enough that abandoned
# sessions evict automatically and do not consume Redis memory indefinitely.
# Documented as D-P12-01 in DECISIONS.md.
_TTL_SECONDS = 1800


def _key(conversation_id: uuid.UUID) -> str:
    return f"memory:short:{conversation_id}"


class MemoryShortService:
    """Append-only short-term memory for one conversation.

    All methods are async so they compose naturally into FastAPI routes and
    tool calls without blocking the event loop.
    """

    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def append(self, conversation_id: uuid.UUID, message: dict[str, Any]) -> None:
        """Append one message dict and refresh the TTL."""
        key = _key(conversation_id)
        serialised = json.dumps(message)
        await self._redis.rpush(key, serialised)
        await self._redis.expire(key, _TTL_SECONDS)
        log.debug("memory.short.append", conversation_id=str(conversation_id))

    async def get_all(self, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        """Return all messages in insertion order; empty list if key absent."""
        key = _key(conversation_id)
        raw: list[str] = await self._redis.lrange(key, 0, -1)
        return [json.loads(item) for item in raw]

    async def clear(self, conversation_id: uuid.UUID) -> None:
        """Delete the key, removing all messages for this conversation."""
        key = _key(conversation_id)
        await self._redis.delete(key)
        log.info("memory.short.cleared", conversation_id=str(conversation_id))

    async def ttl(self, conversation_id: uuid.UUID) -> int:
        """Return remaining TTL in seconds; -2 if key does not exist."""
        key = _key(conversation_id)
        result: int = await self._redis.ttl(key)
        return result
