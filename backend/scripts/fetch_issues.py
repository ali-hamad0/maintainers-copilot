#!/usr/bin/env python3
"""Fetch closed GitHub issues from pandas-dev/pandas and upload raw JSON to MinIO.

Usage
-----
    python backend/scripts/fetch_issues.py [--limit N] [--force]

Environment (loaded from .env in repo root via python-dotenv):
    GITHUB_TOKEN            Personal access token (read:org, public_repo)
    MINIO_ACCESS_KEY        MinIO root user
    MINIO_SECRET_KEY        MinIO root password
    MINIO_LOCAL_ENDPOINT    host:port of MinIO (e.g. localhost:9000)
    MINIO_BUCKET            Bucket name (e.g. maintainers-copilot)

The script writes one JSON file:
    raw/issues/pandas-dev_pandas/issues.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_OWNER = "pandas-dev"
REPO_NAME = "pandas"
GITHUB_API_BASE = "https://api.github.com"
MINIO_OBJECT_KEY = f"raw/issues/{REPO_OWNER}_{REPO_NAME}/issues.json"
PAGE_SIZE = 100  # GitHub maximum

# Fields we keep from each issue (minimises object size).
ISSUE_FIELDS: tuple[str, ...] = (
    "number",
    "title",
    "body",
    "labels",
    "closed_at",
    "state",
    "html_url",
    "comments",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env() -> dict[str, str]:
    """Load .env from repo root and return required env vars."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    required = [
        "GITHUB_TOKEN",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_LOCAL_ENDPOINT",
        "MINIO_BUCKET",
    ]
    config: dict[str, str] = {}
    missing: list[str] = []
    for key in required:
        value = os.getenv(key)
        if not value:
            missing.append(key)
        else:
            config[key] = value
    if missing:
        print(
            f"ERROR: missing required env vars: {', '.join(missing)}\n"
            "Copy .env.example → .env and fill in the values.",
            file=sys.stderr,
        )
        sys.exit(1)
    return config


def _slim_issue(raw: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields we care about from a raw GitHub issue dict."""
    slimmed: dict[str, Any] = {}
    for field in ISSUE_FIELDS:
        value = raw.get(field)
        if field == "labels" and isinstance(value, list):
            slimmed[field] = [lbl["name"] for lbl in value if isinstance(lbl, dict)]
        else:
            slimmed[field] = value
    return slimmed


def _minio_client(config: dict[str, str]) -> Minio:
    endpoint = config["MINIO_LOCAL_ENDPOINT"]
    # Strip protocol prefix if someone added it in .env
    endpoint = endpoint.removeprefix("http://").removeprefix("https://")
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    return Minio(
        endpoint,
        access_key=config["MINIO_ACCESS_KEY"],
        secret_key=config["MINIO_SECRET_KEY"],
        secure=secure,
    )


def _object_exists(client: Minio, bucket: str, key: str) -> bool:
    try:
        client.stat_object(bucket, key)
        return True
    except S3Error:
        return False


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        print(f"[minio] created bucket '{bucket}'", file=sys.stderr)


def _upload_json(client: Minio, bucket: str, key: str, data: list[dict[str, Any]]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    buf = BytesIO(payload)
    client.put_object(
        bucket,
        key,
        buf,
        length=len(payload),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Async fetch logic
# ---------------------------------------------------------------------------


async def _fetch_all_issues(
    token: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    """Page through all closed issues and return slimmed dicts."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{GITHUB_API_BASE}/repos/{REPO_OWNER}/{REPO_NAME}/issues"
    params: dict[str, str | int] = {
        "state": "closed",
        "per_page": PAGE_SIZE,
        "page": 1,
    }

    issues: list[dict[str, Any]] = []
    page = 1

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        while True:
            params["page"] = page
            print(
                f"[github] fetching page {page} "
                f"(collected {len(issues)} so far) …",
                file=sys.stderr,
            )
            response = await client.get(url, params=params)
            response.raise_for_status()
            batch: list[dict[str, Any]] = response.json()

            if not batch:
                print("[github] no more issues — done.", file=sys.stderr)
                break

            for raw in batch:
                # GitHub issues endpoint returns PRs too; skip them.
                if raw.get("pull_request"):
                    continue
                issues.append(_slim_issue(raw))
                if limit is not None and len(issues) >= limit:
                    print(
                        f"[github] reached --limit {limit}, stopping early.",
                        file=sys.stderr,
                    )
                    return issues

            # Respect GitHub rate-limit headers if we're close to the ceiling.
            remaining = int(response.headers.get("X-RateLimit-Remaining", "999"))
            if remaining < 5:
                reset_at = int(response.headers.get("X-RateLimit-Reset", "0"))
                wait = max(0, reset_at - int(asyncio.get_event_loop().time())) + 1
                print(
                    f"[github] rate-limit nearly exhausted, sleeping {wait}s …",
                    file=sys.stderr,
                )
                await asyncio.sleep(wait)

            page += 1

    return issues


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(limit: int | None, force: bool) -> None:
    config = _load_env()
    client = _minio_client(config)
    bucket = config["MINIO_BUCKET"]

    _ensure_bucket(client, bucket)

    if not force and _object_exists(client, bucket, MINIO_OBJECT_KEY):
        print(
            f"[minio] object '{MINIO_OBJECT_KEY}' already exists — skipping upload.\n"
            "Pass --force to re-fetch and overwrite.",
            file=sys.stderr,
        )
        return

    print(
        f"[fetch] starting fetch of closed issues from {REPO_OWNER}/{REPO_NAME} …",
        file=sys.stderr,
    )
    issues = await _fetch_all_issues(config["GITHUB_TOKEN"], limit)
    print(f"[fetch] fetched {len(issues)} issues total.", file=sys.stderr)

    print(f"[minio] uploading → {MINIO_OBJECT_KEY} …", file=sys.stderr)
    _upload_json(client, bucket, MINIO_OBJECT_KEY, issues)
    print("[minio] upload complete.", file=sys.stderr)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch closed pandas-dev/pandas issues and upload to MinIO."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Fetch at most N issues (for local testing).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch and overwrite even if the object already exists in MinIO.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(limit=args.limit, force=args.force))
