"""Baseline schema: all core tables.

Revision ID: 0001
Revises:
Create Date: 2026-05-18

Tables created:
  users, audit_log, widget_config, conversations, messages,
  memory_long_term (pgvector + HNSW), eval_runs.

HNSW index parameters (D-09):
  m=16, ef_construction=64 — default values that balance recall and build time.
  Benchmark in Phase 8 will confirm or tune these.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector extension — must exist before vector columns are created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("role", sa.String(50), nullable=False, server_default=sa.text("'user'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── audit_log ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])

    # ── widget_config ────────────────────────────────────────────────────────
    # CORS allowlist is enforced from this table (CLAUDE.md §6 Origin allowlisting).
    op.create_table(
        "widget_config",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("allowed_origins", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── conversations ────────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    # ── messages ─────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),  # user | assistant | tool
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_calls", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # ── memory_long_term ─────────────────────────────────────────────────────
    # embedding column uses pgvector; HNSW index chosen over IVFFlat (D-09).
    op.create_table(
        "memory_long_term",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        # vector(768) — dimension locked to D-02 (gemini-embedding-001)
        sa.Column("embedding", sa.Text, nullable=True),  # placeholder; replaced below
        sa.Column("source_tool", sa.String(100), nullable=False),
        sa.Column("trust_score", sa.Float, nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # Replace placeholder Text column with actual vector(768) type.
    op.execute("ALTER TABLE memory_long_term DROP COLUMN embedding")
    op.execute("ALTER TABLE memory_long_term ADD COLUMN embedding vector(768)")

    # HNSW index: m=16, ef_construction=64 (D-09).
    op.execute(
        "CREATE INDEX ix_memory_embedding_hnsw "
        "ON memory_long_term "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.create_index("ix_memory_user_id", "memory_long_term", ["user_id"])

    # ── eval_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "eval_runs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("suite", sa.String(100), nullable=False),  # classification | rag
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("report_minio_key", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_eval_runs_suite", "eval_runs", ["suite"])
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("memory_long_term")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("widget_config")
    op.drop_table("audit_log")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
