"""Add chunks table for RAG corpus (parent-child chunking + pgvector HNSW).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-19

Design (D-P8-01 / D-P8-02):
  parent chunk  — full issue text up to 2000 chars, NOT embedded, returned as context.
  child chunks  — ~256 char slices of the parent, embedded with gemini-embedding-001 (768 dim).
  Dedup key     — content_hash (SHA-256 of content); re-running ingest skips existing rows.
  HNSW params   — m=16, ef_construction=64 (same as memory_long_term, D-09).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("doc_id", sa.String(255), nullable=False),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("chunk_role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("doc_metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_foreign_key(
        "fk_chunks_parent_id",
        "chunks",
        "chunks",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])
    op.create_index("ix_chunks_parent_id", "chunks", ["parent_id"])
    op.create_index("ix_chunks_content_hash", "chunks", ["content_hash"], unique=True)

    # Add vector(768) column for child embeddings (pgvector type, not a native SA type).
    op.execute("ALTER TABLE chunks ADD COLUMN embedding vector(768)")

    # HNSW index for cosine-similarity search. NULL embeddings (parent chunks) are skipped.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw "
        "ON chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_index("ix_chunks_content_hash", table_name="chunks")
    op.drop_index("ix_chunks_parent_id", table_name="chunks")
    op.drop_index("ix_chunks_doc_id", table_name="chunks")
    op.drop_constraint("fk_chunks_parent_id", "chunks", type_="foreignkey")
    op.drop_table("chunks")
