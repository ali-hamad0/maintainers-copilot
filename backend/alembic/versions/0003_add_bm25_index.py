"""Add GIN index on chunks.content for BM25 sparse retrieval (Phase 9).

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-19

The GIN index lets Postgres use the tsvector plan for ts_rank / @@ queries
instead of a sequential scan.  Without it, sparse_search degrades to O(N)
at corpus size.

downgrade() drops the index; the chunks data is preserved.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_chunks_content_tsv "
        "ON chunks USING gin (to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_tsv")
