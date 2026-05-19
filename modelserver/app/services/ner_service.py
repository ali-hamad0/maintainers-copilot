"""NER service: spaCy en_core_web_sm + regex pass for code-specific entities.

CPU-bound inference is offloaded with asyncio.to_thread so the event loop
is never blocked.  If the spaCy model is not installed the service falls back
to regex-only extraction (all entities are still returned, just without the
standard NLP entity types like PERSON or ORG).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for code-shaped entities in GitHub issues
# ---------------------------------------------------------------------------

# Relative file paths such as pandas/core/frame.py or tests/io/test_csv.py
_RE_FILE_PATH = re.compile(r"(?:[a-zA-Z0-9_.-]+/)+[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]+")

# Backtick-quoted code tokens: `pd.read_csv()`, `DataFrame.merge`, `groupby`
_RE_CODE_ENTITY = re.compile(r"`([^`\n]{1,120})`")

# Bare error/exception type names that appear outside backticks
_RE_ERROR_TYPE = re.compile(r"\b([A-Z][a-zA-Z]+(?:Error|Exception|Warning|Fault))\b")

# Semantic version strings: 1.5.3, 2.0.0rc1, v2.1.0
_RE_VERSION = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?(?:[a-zA-Z]\d*)?)\b")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NEREntity:
    text: str
    label: str
    source: str  # "regex" | "spacy"


@dataclass
class NERResult:
    entities: list[NEREntity] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure extraction helpers (sync, run in a thread)
# ---------------------------------------------------------------------------


def _extract_regex(text: str) -> list[NEREntity]:
    entities: list[NEREntity] = []

    for m in _RE_FILE_PATH.finditer(text):
        entities.append(NEREntity(text=m.group(), label="FILE_PATH", source="regex"))

    for m in _RE_CODE_ENTITY.finditer(text):
        entities.append(NEREntity(text=m.group(1), label="CODE_ENTITY", source="regex"))

    for m in _RE_ERROR_TYPE.finditer(text):
        entities.append(NEREntity(text=m.group(), label="ERROR_TYPE", source="regex"))

    for m in _RE_VERSION.finditer(text):
        entities.append(NEREntity(text=m.group(1), label="VERSION", source="regex"))

    return entities


def _extract_sync(text: str, nlp: object | None) -> NERResult:
    entities = _extract_regex(text)

    if nlp is not None:
        doc = nlp(text[:10_000])  # spaCy upper limit to avoid memory issues
        for ent in doc.ents:
            entities.append(NEREntity(text=ent.text, label=ent.label_, source="spacy"))

    # Deduplicate on (text, label) preserving insertion order
    seen: set[tuple[str, str]] = set()
    deduped: list[NEREntity] = []
    for entity in entities:
        key = (entity.text, entity.label)
        if key not in seen:
            seen.add(key)
            deduped.append(entity)

    return NERResult(entities=deduped)


# ---------------------------------------------------------------------------
# Service class — instantiated once in lifespan
# ---------------------------------------------------------------------------


class NERService:
    """Extracts named entities from issue text using spaCy + regex."""

    def __init__(self, nlp: object | None) -> None:
        self._nlp = nlp
        mode = "spacy+regex" if nlp is not None else "regex-only"
        log.info("ner_service.ready", mode=mode)

    async def extract(self, text: str) -> NERResult:
        """Extract entities from *text* off the event loop thread."""
        return await asyncio.to_thread(_extract_sync, text, self._nlp)
