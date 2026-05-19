#!/usr/bin/env python3
"""Append hardcoded question issues into the main issues.json in MinIO.

pandas-dev/pandas has almost no 'question'-labelled issues so the test split
ends up with ~1 sample and question F1 = 0.00.  This script merges
backend/data/question_issues.json into the existing pandas raw JSON, then
re-uploads it so split_issues.py can be re-run unchanged.

Run ONCE before re-running split_issues.py.  Pass --force to re-apply.

Usage
-----
    python backend/scripts/inject_questions.py [--force]

Environment (same as other scripts):
    MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_LOCAL_ENDPOINT, MINIO_BUCKET
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_FILE = Path(__file__).parent.parent / "data" / "question_issues.json"
MINIO_KEY = "raw/issues/pandas-dev_pandas/issues.json"
INJECTED_FLAG_KEY = "raw/issues/pandas-dev_pandas/.questions_injected"


def _load_env() -> dict[str, str]:
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)
    required = ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_LOCAL_ENDPOINT", "MINIO_BUCKET"]
    config: dict[str, str] = {}
    missing: list[str] = []
    for key in required:
        value = os.getenv(key)
        if not value:
            missing.append(key)
        else:
            config[key] = value
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return config


def _minio_client(config: dict[str, str]) -> Minio:
    endpoint = config["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
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
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return False
        raise


def _download_json(client: Minio, bucket: str, key: str) -> Any:
    try:
        response = client.get_object(bucket, key)
        return json.loads(response.read())
    except S3Error as exc:
        print(f"ERROR: could not download {key}: {exc}", file=sys.stderr)
        sys.exit(1)


def _upload_json(client: Minio, bucket: str, key: str, data: list[dict[str, Any]]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    client.put_object(
        bucket,
        key,
        BytesIO(payload),
        length=len(payload),
        content_type="application/json",
    )


def main(force: bool) -> None:
    config = _load_env()
    client = _minio_client(config)
    bucket = config["MINIO_BUCKET"]

    if not force and _object_exists(client, bucket, INJECTED_FLAG_KEY):
        print(
            "Question issues already injected (flag object found).\n" "Pass --force to re-inject.",
            file=sys.stderr,
        )
        return

    if not DATA_FILE.exists():
        print(f"ERROR: data file not found: {DATA_FILE}", file=sys.stderr)
        sys.exit(1)

    question_issues: list[dict[str, Any]] = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(question_issues)} question issues from {DATA_FILE.name}")

    print(f"Downloading existing {MINIO_KEY} ...")
    existing: list[dict[str, Any]] = _download_json(client, bucket, MINIO_KEY)
    print(f"Existing issues: {len(existing)}")

    # Guard against double-injection on --force: remove any previously injected numbers.
    injected_numbers = {q["number"] for q in question_issues}
    existing_clean = [i for i in existing if i.get("number") not in injected_numbers]
    removed = len(existing) - len(existing_clean)
    if removed:
        print(f"Removed {removed} previously injected question issues before re-injecting.")

    merged = existing_clean + question_issues
    print(f"Merged total: {len(merged)} issues")

    print(f"Uploading merged list -> {MINIO_KEY} ...")
    _upload_json(client, bucket, MINIO_KEY, merged)

    # Write a flag object so accidental re-runs are safe.
    flag_payload = b"injected"
    client.put_object(
        bucket,
        INJECTED_FLAG_KEY,
        BytesIO(flag_payload),
        length=len(flag_payload),
        content_type="text/plain",
    )

    print("Done. Now re-run: python backend/scripts/split_issues.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inject hardcoded question issues into the main issues.json in MinIO."
    )
    parser.add_argument("--force", action="store_true", help="Re-inject even if already done.")
    args = parser.parse_args()
    main(force=args.force)
