"""Add payload and provenance columns to memory_long_term.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-20

What changed:
  memory_long_term.payload   JSONB nullable — structured memory content
  memory_long_term.provenance JSONB nullable — actor_id, source_tool,
                                               conversation_id, timestamp

Why now (Phase 12): The CLAUDE.md §7 schema requires these columns.
payload replaces the plain `content` text as the canonical structured form;
content is kept for backward-compatible text search.
provenance replaces the plain `source_tool` string with a richer JSONB that
captures the full audit lineage (actor, tool, conversation, timestamp).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_long_term",
        sa.Column("payload", JSONB, nullable=True),
    )
    op.add_column(
        "memory_long_term",
        sa.Column("provenance", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_long_term", "provenance")
    op.drop_column("memory_long_term", "payload")
