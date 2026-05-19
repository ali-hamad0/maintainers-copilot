#!/usr/bin/env python3
"""Read raw issues from MinIO, apply label mapping, time-split, and upload JSONL splits.

Splits produced (time-ordered, oldest → newest):
    splits/v1/train.jsonl       ~63%  — fine-tune DistilBERT + classical baseline
    splits/v1/val.jsonl          12%  — hyperparameter tuning / early stopping
    splits/v1/test.jsonl         15%  — held-out evaluation; feeds CI gate
    splits/v1/rag_corpus.jsonl   10%  — dense retrieval index; excluded from classifiers

SHA-256 hashes are written back into model_card.md at the repo root.

Usage
-----
    python backend/scripts/split_issues.py

Environment (loaded from .env via python-dotenv):
    MINIO_ACCESS_KEY        MinIO root user
    MINIO_SECRET_KEY        MinIO root password
    MINIO_LOCAL_ENDPOINT    host:port of MinIO (e.g. localhost:9000)
    MINIO_BUCKET            Bucket name (e.g. maintainers-copilot)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
MODEL_CARD_PATH = REPO_ROOT / "model_card.md"

RAW_OBJECT_KEY = "raw/issues/pandas-dev_pandas/issues.json"
SPLIT_PREFIX = "splits/v1"

# Split fractions — applied to ALL labeled issues sorted by closed_at ascending.
# Oldest chunk → train; newest chunk → rag_corpus.
FRAC_RAG = 0.10  # newest 10%
FRAC_TEST = 0.15  # next 15% (second newest)
FRAC_VAL = 0.12  # next 12%
# train gets the remainder (~63%)

# ---------------------------------------------------------------------------
# Label mapping  (D-P3-02)
# Priority: bug > feature > docs > question
# ---------------------------------------------------------------------------

LABEL_MAP: dict[str, str] = {
    # bug
    "bug": "bug",
    "regression": "bug",
    "crash": "bug",
    # feature
    "enhancement": "feature",
    "new feature": "feature",
    "performance": "feature",
    "refactor": "feature",
    # docs
    "docs": "docs",
    "documentation": "docs",
    # question
    "question": "question",
    "usage question": "question",
}

PRIORITY: dict[str, int] = {"bug": 0, "feature": 1, "docs": 2, "question": 3}


def map_labels(raw_labels: list[str]) -> str | None:
    """Return the highest-priority mapped class, or None if no labels match."""
    mapped = [LABEL_MAP[lbl.lower()] for lbl in raw_labels if lbl.lower() in LABEL_MAP]
    if not mapped:
        return None
    return min(mapped, key=lambda c: PRIORITY[c])


# ---------------------------------------------------------------------------
# MinIO helpers
# ---------------------------------------------------------------------------


def _load_env() -> dict[str, str]:
    env_path = REPO_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    required = [
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


def _download_json(client: Minio, bucket: str, key: str) -> Any:
    try:
        response = client.get_object(bucket, key)
        data = json.loads(response.read())
        return data
    except S3Error as exc:
        print(f"ERROR: could not download {key} from MinIO: {exc}", file=sys.stderr)
        sys.exit(1)


def _upload_jsonl(
    client: Minio,
    bucket: str,
    key: str,
    rows: list[dict[str, Any]],
) -> str:
    """Serialise rows as JSONL, upload, return hex SHA-256 of the bytes."""
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    client.put_object(
        bucket,
        key,
        BytesIO(payload),
        length=len(payload),
        content_type="application/x-ndjson",
    )
    return digest


# ---------------------------------------------------------------------------
# Split logic
# ---------------------------------------------------------------------------


def _boundary(total: int, frac: float) -> int:
    """Return the number of items that belong to a chunk of size `frac`."""
    return max(1, round(total * frac))


def _time_split(
    labeled: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],]:
    """
    Return (train, val, test, rag) slices.

    Issues must be pre-sorted ascending by closed_at. The newest items go to
    rag_corpus, then test, then val, then train.

    Invariant: max(train.closed_at) < min(val.closed_at) < min(test.closed_at)
               < min(rag.closed_at)
    """
    n = len(labeled)
    n_rag = _boundary(n, FRAC_RAG)
    n_test = _boundary(n, FRAC_TEST)
    n_val = _boundary(n, FRAC_VAL)
    n_train = n - n_rag - n_test - n_val

    if n_train < 1:
        print("ERROR: not enough labeled issues to form a train split.", file=sys.stderr)
        sys.exit(1)

    # Slice ascending by closed_at
    train = labeled[:n_train]
    val = labeled[n_train : n_train + n_val]
    test = labeled[n_train + n_val : n_train + n_val + n_test]
    rag = labeled[n_train + n_val + n_test :]

    return train, val, test, rag


def _validate_time_order(
    train: list[dict[str, Any]],
    val: list[dict[str, Any]],
    test: list[dict[str, Any]],
    rag: list[dict[str, Any]],
) -> None:
    max_train = max(r["closed_at"] for r in train)
    min_val = min(r["closed_at"] for r in val)
    min_test = min(r["closed_at"] for r in test)
    min_rag = min(r["closed_at"] for r in rag)

    ok = max_train < min_val < min_test < min_rag
    if not ok:
        print(
            "ERROR: time-order invariant violated!\n"
            f"  max(train)={max_train}  min(val)={min_val}\n"
            f"  min(test)={min_test}    min(rag)={min_rag}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "Time-order invariant OK:\n"
        f"  max(train)={max_train}\n"
        f"  min(val)  ={min_val}\n"
        f"  min(test) ={min_test}\n"
        f"  min(rag)  ={min_rag}"
    )


def _to_jsonl_row(issue: dict[str, Any], split_name: str) -> dict[str, Any]:
    title = (issue.get("title") or "").strip()
    body = (issue.get("body") or "").strip()
    text = f"{title}\n\n{body}" if body else title
    return {
        "id": issue["number"],
        "text": text,
        "label": issue["label"],
        "closed_at": issue["closed_at"],
        "split": split_name,
    }


def _print_distribution(name: str, rows: list[dict[str, Any]]) -> None:
    counts = Counter(r["label"] for r in rows)
    total = len(rows)
    print(f"\n  {name} ({total} issues):")
    for cls in ("bug", "feature", "docs", "question"):
        n = counts.get(cls, 0)
        pct = 100 * n / total if total else 0
        print(f"    {cls:<10} {n:>5}  ({pct:.1f}%)")


# ---------------------------------------------------------------------------
# model_card.md patch
# ---------------------------------------------------------------------------

SHA_PATTERN = re.compile(
    r"(###\s*SHA-256 Hashes.*?)(\n##|\Z)",
    re.DOTALL,
)


def _update_model_card(hashes: dict[str, str]) -> None:
    """Overwrite the SHA-256 section in model_card.md."""
    if not MODEL_CARD_PATH.exists():
        print(
            f"WARNING: {MODEL_CARD_PATH} not found — skipping hash update.",
            file=sys.stderr,
        )
        return

    text = MODEL_CARD_PATH.read_text(encoding="utf-8")

    new_section_lines = ["### SHA-256 Hashes\n\n"]
    new_section_lines.append("| Split | SHA-256 |\n")
    new_section_lines.append("|---|---|\n")
    for split_name, digest in hashes.items():
        new_section_lines.append(f"| `{split_name}` | `{digest}` |\n")
    new_section = "".join(new_section_lines)

    if SHA_PATTERN.search(text):
        # Replace existing section, preserving whatever comes after it.
        def _replace(m: re.Match[str]) -> str:
            suffix = m.group(2)  # "\n##" or ""
            return new_section + suffix

        updated = SHA_PATTERN.sub(_replace, text, count=1)
    else:
        # Append at end
        updated = text.rstrip() + "\n\n" + new_section

    MODEL_CARD_PATH.write_text(updated, encoding="utf-8")
    print(f"\nmodel_card.md updated with SHA-256 hashes ({MODEL_CARD_PATH})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    config = _load_env()
    client = _minio_client(config)
    bucket = config["MINIO_BUCKET"]

    print(f"Downloading {RAW_OBJECT_KEY} from MinIO …")
    raw_issues: list[dict[str, Any]] = _download_json(client, bucket, RAW_OBJECT_KEY)
    print(f"Downloaded {len(raw_issues)} raw issues.")

    # Apply label mapping and filter
    labeled: list[dict[str, Any]] = []
    for issue in raw_issues:
        raw_labels: list[str] = issue.get("labels") or []
        cls = map_labels(raw_labels)
        if cls is None:
            continue
        if not issue.get("closed_at"):
            continue
        issue["label"] = cls
        labeled.append(issue)

    discarded = len(raw_issues) - len(labeled)
    print(f"Labeled: {len(labeled)}  (discarded {discarded} with no mappable label)")

    # Sort ascending by closed_at (lexicographic on ISO 8601 is correct)
    labeled.sort(key=lambda r: r["closed_at"])

    train_raw, val_raw, test_raw, rag_raw = _time_split(labeled)

    _validate_time_order(train_raw, val_raw, test_raw, rag_raw)

    # Build JSONL rows
    train_rows = [_to_jsonl_row(r, "train") for r in train_raw]
    val_rows = [_to_jsonl_row(r, "val") for r in val_raw]
    test_rows = [_to_jsonl_row(r, "test") for r in test_raw]
    rag_rows = [_to_jsonl_row(r, "rag") for r in rag_raw]

    # Print class distributions
    print("\nClass distributions:")
    _print_distribution("train", train_rows)
    _print_distribution("val", val_rows)
    _print_distribution("test", test_rows)
    _print_distribution("rag_corpus", rag_rows)

    # Upload and collect hashes
    splits = {
        "train": (f"{SPLIT_PREFIX}/train.jsonl", train_rows),
        "val": (f"{SPLIT_PREFIX}/val.jsonl", val_rows),
        "test": (f"{SPLIT_PREFIX}/test.jsonl", test_rows),
        "rag_corpus": (f"{SPLIT_PREFIX}/rag_corpus.jsonl", rag_rows),
    }

    hashes: dict[str, str] = {}
    print()
    for split_name, (key, rows) in splits.items():
        digest = _upload_jsonl(client, bucket, key, rows)
        hashes[split_name] = digest
        print(f"Uploaded {key}  ({len(rows)} rows)  sha256={digest}")

    _update_model_card(hashes)

    print("\nDone. Copy the SHA-256 values into model_card.md if the auto-update failed.")


if __name__ == "__main__":
    main()
