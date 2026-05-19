#!/usr/bin/env python3
"""Download split JSONL files from MinIO to a local data/ directory.

Run this locally, then upload the files to Google Drive before running
notebooks/train_classifier.ipynb on Colab.

Usage
-----
    python backend/scripts/download_splits.py

Output
------
    data/train.jsonl
    data/val.jsonl
    data/test.jsonl

Environment (loaded from .env via python-dotenv):
    MINIO_ACCESS_KEY        MinIO root user
    MINIO_SECRET_KEY        MinIO root password
    MINIO_LOCAL_ENDPOINT    host:port of MinIO (e.g. localhost:9000)
    MINIO_BUCKET            Bucket name (e.g. maintainers-copilot)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
SPLITS = ["train", "val", "test"]


def main() -> None:
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)

    required = ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_LOCAL_ENDPOINT", "MINIO_BUCKET"]
    config: dict[str, str] = {}
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    for k in required:
        config[k] = os.environ[k]

    endpoint = config["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    client = Minio(
        endpoint,
        access_key=config["MINIO_ACCESS_KEY"],
        secret_key=config["MINIO_SECRET_KEY"],
        secure=secure,
    )

    DATA_DIR.mkdir(exist_ok=True)

    for split in SPLITS:
        key = f"splits/v1/{split}.jsonl"
        dest = DATA_DIR / f"{split}.jsonl"
        try:
            client.fget_object(config["MINIO_BUCKET"], key, str(dest))
            print(f"Downloaded {key} → {dest}")
        except S3Error as exc:
            print(f"ERROR: could not download {key}: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"\nDone. Upload the files in {DATA_DIR} to Google Drive:")
    print("  MyDrive/maintainers-copilot/splits/train.jsonl")
    print("  MyDrive/maintainers-copilot/splits/val.jsonl")
    print("  MyDrive/maintainers-copilot/splits/test.jsonl")


if __name__ == "__main__":
    main()
