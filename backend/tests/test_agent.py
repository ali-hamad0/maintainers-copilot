"""Phase 13 agent and tool tests.

What MUST be tested (CLAUDE.md §5):
  1. Every Pydantic schema — one happy, one rejection per tool args schema.
  2. Every tool — mock HTTP/LLM, assert structured return, including ToolError path.
  3. One end-to-end happy path through the agent with all external calls mocked.
  4. write_memory redaction — a fake API key in the input is scrubbed before write.
  5. write_memory only fires when the LLM calls it (auto-write prevention via design).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.domain.tool_result import ToolError
from app.tools import ToolContext, clean_schema_for_gemini
from app.tools.classify import ClassifyArgs, ClassifyResult, ClassifyTool
from app.tools.extract_entities import ExtractEntitiesArgs, ExtractEntitiesTool
from app.tools.rag_search import RagSearchArgs, RagSearchTool
from app.tools.summarise import SummariseArgs, SummariseTool
from app.tools.write_memory import WriteMemoryArgs, WriteMemoryTool

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ctx() -> ToolContext:
    return ToolContext(
        session=AsyncMock(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        request_id="req-test",
    )


def _mock_http_response(status: int, body: dict[str, Any]) -> MagicMock:
    """Return a mock httpx.Response. raise_for_status() is a no-op for 2xx."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = body
    mock.raise_for_status.return_value = None
    return mock


# ── 1. Pydantic schema validation ─────────────────────────────────────────────


def test_classify_args_valid() -> None:
    args = ClassifyArgs(issue_title="Fix crash on startup", issue_body="Stack trace attached.")
    assert args.issue_title == "Fix crash on startup"


def test_classify_args_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        ClassifyArgs(issue_title="")


def test_extract_entities_args_valid() -> None:
    args = ExtractEntitiesArgs(text="Error in pandas/core/frame.py line 42")
    assert "frame.py" in args.text


def test_extract_entities_args_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        ExtractEntitiesArgs(text="")


def test_rag_search_args_valid() -> None:
    args = RagSearchArgs(query="How to merge DataFrames?", top_k=3)
    assert args.top_k == 3


def test_rag_search_args_rejects_top_k_zero() -> None:
    with pytest.raises(ValidationError):
        RagSearchArgs(query="test", top_k=0)


def test_summarise_args_valid() -> None:
    args = SummariseArgs(issue_title="Bug", issue_body="Details here")
    assert args.issue_body == "Details here"


def test_summarise_args_defaults_empty_body() -> None:
    args = SummariseArgs(issue_title="Bug")
    assert args.issue_body == ""


def test_write_memory_args_valid() -> None:
    args = WriteMemoryArgs(content="Issue #42 is a duplicate of #7.")
    assert args.content.startswith("Issue")


def test_write_memory_args_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        WriteMemoryArgs(content="")


# ── 2. Tool happy paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_tool_happy_path() -> None:
    """ClassifyTool returns ClassifyResult on a successful modelserver response."""
    fake_resp = _mock_http_response(
        200, {"label": "bug", "score": 0.92, "scores": {"bug": 0.92, "feature": 0.05}}
    )
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=fake_resp)

    tool = ClassifyTool(mock_http, "http://modelserver:8001")
    result = await tool.run(
        {"issue_title": "Crash on startup", "issue_body": "Details."}, _make_ctx()
    )

    assert isinstance(result, ClassifyResult)
    assert result.label == "bug"
    assert result.confidence == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_extract_entities_tool_happy_path() -> None:
    """ExtractEntitiesTool returns ExtractEntitiesResult on success."""
    fake_resp = _mock_http_response(
        200,
        {
            "entities": [{"text": "frame.py", "label": "FILE_PATH", "source": "regex"}],
            "spacy_available": True,
        },
    )
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=fake_resp)

    from app.tools.extract_entities import ExtractEntitiesResult

    tool = ExtractEntitiesTool(mock_http, "http://modelserver:8001")
    result = await tool.run({"text": "Error in frame.py"}, _make_ctx())

    assert isinstance(result, ExtractEntitiesResult)
    assert len(result.entities) == 1
    assert result.entities[0].text == "frame.py"


@pytest.mark.asyncio
async def test_summarise_tool_happy_path() -> None:
    """SummariseTool returns SummariseResult on success."""
    fake_resp = _mock_http_response(
        200,
        {"summary": "A memory leak occurs when...", "model": "gemini-2.5-flash", "fallback": False},
    )
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=fake_resp)

    from app.tools.summarise import SummariseResult

    tool = SummariseTool(mock_http, "http://modelserver:8001")
    result = await tool.run(
        {"issue_title": "Memory leak", "issue_body": "Long body..."}, _make_ctx()
    )

    assert isinstance(result, SummariseResult)
    assert "memory leak" in result.summary.lower()
    assert result.fallback is False


@pytest.mark.asyncio
async def test_rag_search_tool_happy_path() -> None:
    """RagSearchTool returns RagSearchResult using the injected RetrievalService."""
    from app.domain.chunk import ChunkMetadata, ChunkRecord, RetrievedChunk

    fake_chunk = ChunkRecord(
        id=uuid.uuid4(),
        parent_id=None,
        doc_id="issue-123",
        doc_type="issue",
        chunk_role="child",
        content="DataFrame.merge() raises KeyError when...",
        content_hash="abc",
        metadata=ChunkMetadata(doc_type="issue", doc_id="issue-123", chunk_role="child"),
    )
    fake_retrieved = RetrievedChunk(child=fake_chunk, parent=None, score=0.85)

    mock_retrieval = MagicMock()
    mock_retrieval.retrieve = AsyncMock(return_value=[fake_retrieved])

    tool = RagSearchTool(mock_retrieval)
    from app.tools.rag_search import RagSearchResult

    result = await tool.run({"query": "How to merge DataFrames?"}, _make_ctx())

    assert isinstance(result, RagSearchResult)
    assert result.found is True
    assert result.chunks[0].doc_id == "issue-123"
    assert result.chunks[0].score == pytest.approx(0.85)


# ── 3. Tool error paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_tool_timeout_returns_retryable_error() -> None:
    """Timeout from modelserver → ToolError(retryable=True)."""
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    tool = ClassifyTool(mock_http, "http://modelserver:8001")
    result = await tool.run({"issue_title": "Crash", "issue_body": ""}, _make_ctx())

    assert isinstance(result, ToolError)
    assert result.retryable is True


@pytest.mark.asyncio
async def test_classify_tool_503_returns_retryable_error() -> None:
    """503 from modelserver → ToolError(retryable=True)."""
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock(status_code=503)
        )
    )

    tool = ClassifyTool(mock_http, "http://modelserver:8001")
    result = await tool.run({"issue_title": "Crash", "issue_body": ""}, _make_ctx())

    assert isinstance(result, ToolError)
    assert result.retryable is True


@pytest.mark.asyncio
async def test_classify_tool_invalid_args_returns_terminal_error() -> None:
    """Args that fail Pydantic validation → ToolError(retryable=False)."""
    mock_http = MagicMock(spec=httpx.AsyncClient)
    tool = ClassifyTool(mock_http, "http://modelserver:8001")

    result = await tool.run({"issue_title": ""}, _make_ctx())  # empty title

    assert isinstance(result, ToolError)
    assert result.retryable is False


@pytest.mark.asyncio
async def test_rag_search_returns_empty_on_no_results() -> None:
    """Empty retrieval result → RagSearchResult(found=False)."""
    mock_retrieval = MagicMock()
    mock_retrieval.retrieve = AsyncMock(return_value=[])

    tool = RagSearchTool(mock_retrieval)
    from app.tools.rag_search import RagSearchResult

    result = await tool.run({"query": "obscure query with no matches"}, _make_ctx())

    assert isinstance(result, RagSearchResult)
    assert result.found is False
    assert result.chunks == []


# ── 4. write_memory redaction ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_memory_redacts_api_key_before_db_write() -> None:
    """An API key in the content must be redacted before reaching the DB.

    This is redaction call site #3 (app/infra/redaction.py).
    """
    content_with_key = "Remember: our API key is sk-proj-abc1234567890abcdefghijklmno"
    captured_content: list[str] = []

    mock_embedder = MagicMock()
    mock_embedder.embed_batch = AsyncMock(return_value=[[0.1] * 768])

    mock_memory_svc = AsyncMock()

    async def capture_write(**kwargs: Any) -> MagicMock:
        captured_content.append(kwargs["content"])
        m = MagicMock()
        m.id = uuid.uuid4()
        return m

    mock_memory_svc.write = capture_write

    tool = WriteMemoryTool(embedder=mock_embedder)

    with (
        patch("app.tools.write_memory.MemoryRepo"),
        patch("app.tools.write_memory.MemoryLongService", return_value=mock_memory_svc),
    ):
        result = await tool.run({"content": content_with_key}, _make_ctx())

    from app.tools.write_memory import WriteMemoryResult

    assert isinstance(result, WriteMemoryResult)
    assert len(captured_content) == 1
    assert "sk-proj-abc1234567890abcdefghijklmno" not in captured_content[0]
    assert "[REDACTED" in captured_content[0]


# ── 5. Schema cleaning ────────────────────────────────────────────────────────


def test_clean_schema_removes_pydantic_extras() -> None:
    """clean_schema_for_gemini strips title, $defs, default, anyOf."""
    raw = {
        "type": "object",
        "title": "ClassifyArgs",
        "$defs": {},
        "properties": {
            "issue_title": {
                "type": "string",
                "title": "Issue Title",
                "description": "The title.",
                "default": "",
            }
        },
        "required": ["issue_title"],
    }
    cleaned = clean_schema_for_gemini(raw)

    assert "title" not in cleaned
    assert "$defs" not in cleaned
    assert "title" not in cleaned["properties"]["issue_title"]
    assert "default" not in cleaned["properties"]["issue_title"]
    assert cleaned["properties"]["issue_title"]["type"] == "string"
    assert cleaned["properties"]["issue_title"]["description"] == "The title."


def test_tool_declarations_contain_no_pydantic_extras() -> None:
    """All tool declarations must be safe to pass to the Gemini API."""
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_retrieval = MagicMock()
    mock_embedder = MagicMock()

    tools = [
        ClassifyTool(mock_http, "http://modelserver:8001"),
        ExtractEntitiesTool(mock_http, "http://modelserver:8001"),
        SummariseTool(mock_http, "http://modelserver:8001"),
        RagSearchTool(mock_retrieval),
        WriteMemoryTool(mock_embedder),
    ]

    for tool in tools:
        decl = tool.declaration()
        assert "name" in decl
        assert "description" in decl
        params = decl.get("parameters", {})
        assert "title" not in params
        assert "$defs" not in params


# ── 6. Agent _execute_tool dispatch ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_unknown_name_returns_tool_error() -> None:
    """An unregistered tool name returns ToolError, the agent does NOT raise."""
    from app.services.agent import AgentService

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.aclose = AsyncMock()
    mock_retrieval = MagicMock()
    mock_embedder = MagicMock()

    agent = AgentService(
        gemini_api_key="fake-key",
        model="gemini-2.5-flash",
        max_iterations=5,
        classify_tool=ClassifyTool(mock_http, "http://modelserver:8001"),
        extract_entities_tool=ExtractEntitiesTool(mock_http, "http://modelserver:8001"),
        summarise_tool=SummariseTool(mock_http, "http://modelserver:8001"),
        rag_search_tool=RagSearchTool(mock_retrieval),
        write_memory_tool=WriteMemoryTool(mock_embedder),
        embedder=mock_embedder,
    )

    ctx = _make_ctx()
    result = await agent._execute_tool("nonexistent_tool", {}, ctx)

    assert isinstance(result, ToolError)
    assert "Unknown tool" in result.error
    assert result.retryable is False


# ── 7. write_memory is NOT auto-called ───────────────────────────────────────


def test_write_memory_not_in_tool_map_by_accident() -> None:
    """The write_memory tool name in the registry exactly matches 'write_memory'.

    If the LLM never emits 'write_memory', the tool never runs.
    The agent loop is the only caller — there is no code path that calls
    WriteMemoryTool.run() outside of _execute_tool(tool_name, ...).
    """
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_retrieval = MagicMock()
    mock_embedder = MagicMock()

    from app.services.agent import AgentService

    agent = AgentService(
        gemini_api_key="fake",
        model="gemini-2.5-flash",
        max_iterations=5,
        classify_tool=ClassifyTool(mock_http, "http://modelserver:8001"),
        extract_entities_tool=ExtractEntitiesTool(mock_http, "http://modelserver:8001"),
        summarise_tool=SummariseTool(mock_http, "http://modelserver:8001"),
        rag_search_tool=RagSearchTool(mock_retrieval),
        write_memory_tool=WriteMemoryTool(mock_embedder),
        embedder=mock_embedder,
    )

    # The tool is registered under exactly "write_memory".
    assert "write_memory" in agent._tool_map
    # It is NOT reachable under any other name — no ambiguity.
    assert agent._tool_map["write_memory"].name == "write_memory"
    # The only way write_memory executes is if tool_name == "write_memory",
    # which only happens when the LLM emits that function call.
    assert len([t for t in agent._tools if t.name == "write_memory"]) == 1
