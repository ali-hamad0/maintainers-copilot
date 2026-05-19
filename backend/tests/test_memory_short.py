"""Short-term memory tests (Phase 12).

Tests MemoryShortService against a fake Redis client so no running Redis is
required in CI.  Covers:
  1. append() stores a message and appends in order
  2. get_all() returns all messages in order
  3. TTL is set on append
  4. clear() removes all messages
  5. get_all() returns empty list for unknown conversation
"""

import uuid
from typing import Any

import pytest

from app.services.memory_short import _TTL_SECONDS, MemoryShortService

# ── Fake Redis ────────────────────────────────────────────────────────────────


class FakeRedis:
    """In-memory Redis stub for unit tests.  Only the methods used by
    MemoryShortService are implemented; everything else raises AttributeError.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}
        self._ttls: dict[str, int] = {}

    async def rpush(self, key: str, value: str) -> int:
        self._store.setdefault(key, []).append(value)
        return len(self._store[key])

    async def expire(self, key: str, seconds: int) -> int:
        self._ttls[key] = seconds
        return 1

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self._store.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    async def delete(self, key: str) -> int:
        removed = 1 if key in self._store else 0
        self._store.pop(key, None)
        self._ttls.pop(key, None)
        return removed

    async def ttl(self, key: str) -> int:
        if key not in self._store:
            return -2
        return self._ttls.get(key, -1)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture()
def svc(fake_redis: FakeRedis) -> MemoryShortService:
    return MemoryShortService(fake_redis)  # type: ignore[arg-type]


@pytest.fixture()
def conv_id() -> uuid.UUID:
    return uuid.uuid4()


# ── Happy-path tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_single_message(svc: MemoryShortService, conv_id: uuid.UUID) -> None:
    msg: dict[str, Any] = {"role": "user", "content": "hello"}
    await svc.append(conv_id, msg)

    messages = await svc.get_all(conv_id)
    assert len(messages) == 1
    assert messages[0] == msg


@pytest.mark.asyncio
async def test_append_preserves_order(svc: MemoryShortService, conv_id: uuid.UUID) -> None:
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]
    for m in msgs:
        await svc.append(conv_id, m)

    result = await svc.get_all(conv_id)
    assert result == msgs


@pytest.mark.asyncio
async def test_ttl_set_on_append(svc: MemoryShortService, conv_id: uuid.UUID) -> None:
    await svc.append(conv_id, {"role": "user", "content": "ping"})
    ttl = await svc.ttl(conv_id)
    assert (
        ttl == _TTL_SECONDS
    ), f"Expected TTL={_TTL_SECONDS} ({_TTL_SECONDS // 60} min), got {ttl}."


@pytest.mark.asyncio
async def test_ttl_refreshed_on_each_append(
    svc: MemoryShortService, conv_id: uuid.UUID, fake_redis: FakeRedis
) -> None:
    """Each append must refresh the TTL so an active session never times out."""
    await svc.append(conv_id, {"role": "user", "content": "msg1"})
    # Simulate time passing by manually reducing the stored TTL.
    key = f"memory:short:{conv_id}"
    fake_redis._ttls[key] = 30  # 30 s left
    await svc.append(conv_id, {"role": "assistant", "content": "msg2"})
    ttl = await svc.ttl(conv_id)
    assert ttl == _TTL_SECONDS, "TTL should be refreshed to full value on each write."


# ── Clear tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_removes_all_messages(svc: MemoryShortService, conv_id: uuid.UUID) -> None:
    await svc.append(conv_id, {"role": "user", "content": "a"})
    await svc.append(conv_id, {"role": "user", "content": "b"})
    await svc.clear(conv_id)
    result = await svc.get_all(conv_id)
    assert result == []


@pytest.mark.asyncio
async def test_clear_nonexistent_is_safe(svc: MemoryShortService) -> None:
    """Clearing a conversation that was never written must not raise."""
    await svc.clear(uuid.uuid4())


# ── Empty-key tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_all_empty_for_unknown_conversation(svc: MemoryShortService) -> None:
    result = await svc.get_all(uuid.uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_ttl_returns_minus_two_for_missing_key(svc: MemoryShortService) -> None:
    ttl = await svc.ttl(uuid.uuid4())
    assert ttl == -2, "Redis convention: -2 means key does not exist."
