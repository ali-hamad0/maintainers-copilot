"""Agent service — ONE tool-calling loop.

Architecture:
  - The LLM (Gemini) chooses which tools to call.  The loop executes them.
  - Non-streaming generateContent throughout the loop.  Text from the final
    response is emitted as a single text_delta SSE event (no second LLM call).
  - Tool failures return ToolError (not raised) so the loop continues.
  - Max iterations cap prevents runaway loops.
  - Every LLM call and tool call is a LangSmith span via @traceable.
  - Short-term memory (Redis) is read at the start and written after each turn.
  - Long-term memory is queried for relevant episodic context.

This file is the ONE place that registers tools=[...] passed to the LLM.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

# langsmith==0.1.77 has a pydantic.v1 / Python 3.12 forward-ref incompatibility
# that raises TypeError at import time in some environments (noted in Phase 11
# project memory).  Fall back to a transparent no-op so the agent module can be
# imported in tests without a running LangSmith setup.
try:
    from langsmith import traceable
except (ImportError, TypeError):

    def traceable(_name: str | None = None, **_kwargs: Any) -> Any:  # type: ignore[no-redef]
        def _inner(fn: Any) -> Any:
            return fn

        return _inner

from app.domain.tool_result import ToolError
from app.infra.embedder import Embedder
from app.infra.tracing import sanitise_span_inputs
from app.repositories.memory_repo import MemoryRepo
from app.services.memory_short import MemoryShortService
from app.tools import ToolContext
from app.tools.classify import ClassifyTool
from app.tools.extract_entities import ExtractEntitiesTool
from app.tools.rag_search import RagSearchTool
from app.tools.summarise import SummariseTool
from app.tools.write_memory import WriteMemoryTool

log = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_GENERATE_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# SSE event type constants.
_EV_TOOL_START = "tool_start"
_EV_TOOL_END = "tool_end"
_EV_TEXT_DELTA = "text_delta"
_EV_DONE = "done"
_EV_ERROR = "error"

_AnyTool = ClassifyTool | ExtractEntitiesTool | SummariseTool | RagSearchTool | WriteMemoryTool


def _sse(event_type: str, **kwargs: Any) -> str:
    return f"data: {json.dumps({'type': event_type, **kwargs})}\n\n"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


class AgentService:
    """One instance per process; created in lifespan and closed on shutdown."""

    def __init__(
        self,
        gemini_api_key: str,
        model: str,
        max_iterations: int,
        classify_tool: ClassifyTool,
        extract_entities_tool: ExtractEntitiesTool,
        summarise_tool: SummariseTool,
        rag_search_tool: RagSearchTool,
        write_memory_tool: WriteMemoryTool,
        embedder: Embedder,
    ) -> None:
        self._api_key = gemini_api_key
        self._model = model
        self._max_iterations = max_iterations
        # ONE place that holds the tool registry.
        self._tools: list[_AnyTool] = [
            classify_tool,
            extract_entities_tool,
            summarise_tool,
            rag_search_tool,
            write_memory_tool,
        ]
        self._tool_map: dict[str, _AnyTool] = {t.name: t for t in self._tools}
        self._embedder = embedder
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

        # Prompt templates loaded once at construction; immutable thereafter.
        self._system_tmpl = _load_prompt("system_chat.md")
        self._memory_decision = _load_prompt("memory_decision.md")

    async def close(self) -> None:
        await self._http.aclose()

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(
        self,
        *,
        user_message: str,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        session: AsyncSession,
        redis: Any,
        request_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Run one conversation turn; yield SSE-formatted strings."""
        return self._stream_turn(
            user_message=user_message,
            conversation_id=conversation_id,
            user_id=user_id,
            session=session,
            redis=redis,
            request_id=request_id,
        )

    async def _stream_turn(
        self,
        *,
        user_message: str,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        session: AsyncSession,
        redis: Any,
        request_id: str | None,
    ) -> AsyncGenerator[str, None]:
        mem_short = MemoryShortService(redis)
        ctx = ToolContext(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
        )

        # 1. Load short-term (Redis) conversation history.
        history: list[dict[str, Any]] = await mem_short.get_all(conversation_id)

        # 2. Query long-term memories relevant to this message.
        past_memories = await self._recall_memories(user_message, user_id, session)

        # 3. Build system prompt with memories injected.
        system_prompt = self._system_tmpl.format(
            memory_guidelines=self._memory_decision,
            past_memories=past_memories or "No relevant memories.",
        )

        # 4. Append current user message to the working message list.
        messages: list[dict[str, Any]] = list(history)
        messages.append({"role": "user", "parts": [{"text": user_message}]})

        # 5. Tool-calling loop — non-streaming while there are function calls.
        function_declarations = [t.declaration() for t in self._tools]
        full_reply = ""

        for iteration in range(self._max_iterations):
            response = await self._llm_generate(
                messages=messages,
                system_prompt=system_prompt,
                function_declarations=function_declarations,
            )

            parts = self._extract_parts(response)
            if not parts:
                yield _sse(_EV_ERROR, message="LLM returned an empty response.")
                return

            function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
            text_parts = [p["text"] for p in parts if "text" in p]

            if function_calls:
                # Execute all function calls in this response, collect results.
                function_responses: list[dict[str, Any]] = []
                for fc in function_calls:
                    tool_name: str = fc["name"]
                    tool_args: dict[str, Any] = fc.get("args", {})

                    yield _sse(_EV_TOOL_START, tool=tool_name, args=sanitise_span_inputs(tool_args))

                    result = await self._execute_tool(tool_name, tool_args, ctx)

                    is_error = isinstance(result, ToolError)
                    result_dict = result.model_dump()
                    yield _sse(_EV_TOOL_END, tool=tool_name, result=result_dict, is_error=is_error)

                    function_responses.append(
                        {
                            "functionResponse": {
                                "name": tool_name,
                                "response": {"result": result_dict},
                            }
                        }
                    )

                # Feed results back; model role message must come before user response.
                messages.append({"role": "model", "parts": parts})
                messages.append({"role": "user", "parts": function_responses})
                continue

            # No function calls — emit the text response as SSE text_delta events.
            # We already have the full text from the non-streaming call; emitting it
            # here avoids a second LLM round-trip that would generate different text.
            if text_parts:
                full_reply = " ".join(text_parts)
                yield _sse(_EV_TEXT_DELTA, content=full_reply)
                break

            # Gemini returned neither text nor function calls (finish_reason=SAFETY etc).
            log.warning("agent.no_usable_parts", iteration=iteration)
            yield _sse(
                _EV_TEXT_DELTA,
                content="I was unable to generate a response for that request.",
            )
            break

        else:
            # Max iterations exhausted.
            log.warning("agent.max_iterations_reached", max=self._max_iterations)
            yield _sse(
                _EV_TEXT_DELTA,
                content=(
                    "I reached the maximum number of tool calls for this request. "
                    "Here is a partial answer based on what I gathered."
                ),
            )

        yield _sse(_EV_DONE)

        # 6. Persist turn to short-term memory (after streaming so it doesn't block).
        await mem_short.append(conversation_id, {"role": "user", "parts": [{"text": user_message}]})
        if full_reply:
            await mem_short.append(
                conversation_id, {"role": "model", "parts": [{"text": full_reply}]}
            )

    # ── LLM helpers ───────────────────────────────────────────────────────────

    @traceable(name="agent.llm_generate")
    async def _llm_generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        function_declarations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Non-streaming generateContent call; returns the raw response dict."""
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": messages,
            "tools": [{"function_declarations": function_declarations}],
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.1},
        }
        url = _GENERATE_URL_TMPL.format(model=self._model)
        resp = await self._http.post(url, params={"key": self._api_key}, json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        log.debug(
            "agent.llm_generate.done",
            model=self._model,
            usage=sanitise_span_inputs(data.get("usageMetadata", {})),
        )
        return data

    # ── Tool execution ────────────────────────────────────────────────────────

    @traceable(name="agent.execute_tool")
    async def _execute_tool(
        self, tool_name: str, tool_args: dict[str, Any], ctx: ToolContext
    ) -> Any:
        tool = self._tool_map.get(tool_name)
        if tool is None:
            log.warning("agent.unknown_tool", tool=tool_name)
            return ToolError(error=f"Unknown tool: {tool_name}", retryable=False)

        log.info("agent.tool_call", tool=tool_name, args=sanitise_span_inputs(tool_args))
        try:
            result = await tool.run(tool_args, ctx)
        except Exception as exc:
            log.exception("agent.tool_exception", tool=tool_name, error=str(exc))
            return ToolError(error=f"Tool raised unexpectedly: {exc}", retryable=False)

        if isinstance(result, ToolError):
            log.warning(
                "agent.tool_error",
                tool=tool_name,
                error=result.error,
                retryable=result.retryable,
            )
        return result

    # ── Memory helpers ────────────────────────────────────────────────────────

    async def _recall_memories(
        self,
        query: str,
        user_id: uuid.UUID,
        session: AsyncSession,
    ) -> str:
        """Embed query and return formatted relevant long-term memories."""
        try:
            vectors = await self._embedder.embed_batch([query])
            embedding = vectors[0]
            repo = MemoryRepo(session)
            memories = await repo.find_similar(user_id=user_id, query_embedding=embedding, top_k=3)
        except Exception as exc:
            log.warning("agent.recall_failed", error=str(exc))
            return ""

        if not memories:
            return ""

        lines = [f"- {m.content}" for m in memories]
        return "\n".join(lines)

    # ── Response parsing ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_parts(response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the parts list from a Gemini generateContent response."""
        candidates = response.get("candidates", [])
        if not candidates:
            return []
        content = candidates[0].get("content", {})
        parts: list[dict[str, Any]] = content.get("parts", [])
        return parts
