#!/usr/bin/env python3
"""Ingest RAG corpus (rag_corpus.jsonl) into the pgvector chunks table.

Usage
-----
    python backend/scripts/ingest_corpus.py

Prerequisites
-------------
- Docker stack running: db, minio, vault
  (`docker compose up -d db minio vault vault-bootstrap`)
- rag_corpus.jsonl present in MinIO (run split_issues.py first)
- Gemini API key stored in Vault at secret/gemini.api_key

The script reads DB password and Gemini API key from Vault, then runs
IngestService which is idempotent (re-running skips already-ingested rows).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add backend/ to sys.path so `from app.*` imports work when run as a script.
_BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND_DIR))
_REPO_ROOT = _BACKEND_DIR.parent

import httpx
import structlog
from dotenv import load_dotenv
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.infra.embedder import Embedder
from app.services.ingest import IngestService

log = structlog.get_logger(__name__)


async def _vault_read(vault_addr: str, token: str, path: str, field: str) -> str:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{vault_addr}/v1/secret/data/{path}",
            headers={"X-Vault-Token": token},
        )
        resp.raise_for_status()
        return str(resp.json()["data"]["data"][field])


async def main() -> None:
    load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=False)

    vault_addr = os.getenv("VAULT_ADDR", "http://vault:8200")
    # When running locally outside Docker, swap the service name for localhost.
    if "vault:" in vault_addr:
        vault_addr = vault_addr.replace("vault:", "localhost:")
    vault_token = os.getenv("VAULT_ROOT_TOKEN", "dev-root-token")

    db_host = "localhost"
    db_port = os.getenv("DB_PORT", "5432")
    db_user = os.getenv("DB_USER", "postgres")
    db_name = os.getenv("DB_NAME", "maintainers_copilot")

    minio_endpoint = os.getenv("MINIO_LOCAL_ENDPOINT", "localhost:9000")
    minio_access = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    minio_secret = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
    bucket = os.getenv("MINIO_BUCKET", "maintainers-copilot")

    print("Reading secrets from Vault …")
    try:
        gemini_key = await _vault_read(vault_addr, vault_token, "gemini", "api_key")
        db_password = await _vault_read(vault_addr, vault_token, "db", "password")
    except Exception as exc:
        print(f"ERROR: Cannot read secrets from Vault: {exc}", file=sys.stderr)
        print("Is the Docker stack running?  docker compose up -d vault db minio", file=sys.stderr)
        sys.exit(1)

    db_url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    endpoint = minio_endpoint.removeprefix("http://").removeprefix("https://")
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    minio_client = Minio(
        endpoint,
        access_key=minio_access,
        secret_key=minio_secret,
        secure=secure,
    )

    embedder = Embedder(api_key=gemini_key)
    service = IngestService(embedder=embedder, minio_client=minio_client, bucket=bucket)

    engine = create_async_engine(db_url, echo=False)
    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("Running ingestion pipeline …")
    async with async_session_factory() as session:
        stats = await service.run(session)

    await engine.dispose()
    await embedder.close()

    print()
    print("Ingestion complete:")
    print(f"  Issues loaded    : {stats.issues_loaded}")
    print(f"  Parent chunks    : {stats.parents_created}")
    print(f"  Child chunks     : {stats.children_created}")
    print(f"  Rows inserted    : {stats.rows_inserted}")
    print(f"  Rows skipped     : {stats.rows_skipped}  (already existed — idempotent)")


if __name__ == "__main__":
    asyncio.run(main())
