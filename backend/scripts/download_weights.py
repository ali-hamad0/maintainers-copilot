#!/usr/bin/env python3
"""Download weights.pt from MinIO to the local cache after Colab training.

Usage
-----
    python backend/scripts/download_weights.py

Output file: backend/weights/weights.pt

Environment (loaded from .env via python-dotenv):
    MINIO_ACCESS_KEY        MinIO root user
    MINIO_SECRET_KEY        MinIO root password
    MINIO_LOCAL_ENDPOINT    host:port of MinIO (e.g. localhost:9000)
    MINIO_BUCKET            Bucket name (e.g. maintainers-copilot)
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

REPO_ROOT    = Path(__file__).parent.parent.parent
WEIGHTS_DIR  = Path(__file__).parent.parent / "weights"
WEIGHTS_PATH = WEIGHTS_DIR / "weights.pt"
MINIO_KEY    = "models/classifier/weights.pt"


def main() -> None:
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)

    required = ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_LOCAL_ENDPOINT", "MINIO_BUCKET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    endpoint = os.environ["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    secure   = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    client   = Minio(
        endpoint,
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=secure,
    )

    bucket = os.environ["MINIO_BUCKET"]
    try:
        client.stat_object(bucket, MINIO_KEY)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            print(
                f"ERROR: '{MINIO_KEY}' not found in bucket '{bucket}'.\n"
                "Run the Colab notebook first, then upload_weights.py.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    print(f"Downloading {MINIO_KEY} from bucket '{bucket}' …")
    client.fget_object(bucket, MINIO_KEY, str(WEIGHTS_PATH))

    weights_sha256 = hashlib.sha256(WEIGHTS_PATH.read_bytes()).hexdigest()
    size_mb = WEIGHTS_PATH.stat().st_size / 1e6

    print(f"Downloaded  → {WEIGHTS_PATH}")
    print(f"Size        : {size_mb:.1f} MB")
    print(f"SHA-256     : {weights_sha256}")
    print("\nVerify this SHA-256 matches model_card.md before starting the modelserver.")


if __name__ == "__main__":
    main()
