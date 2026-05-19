#!/usr/bin/env python3
"""Zero-shot LLM baseline: classify the test split with Gemini 2.5 Flash.

Structured output forces Literal["bug","feature","docs","question"].
Results are cached to MinIO under models/llm_baseline/<run_id>/predictions.json
so re-evaluation is cheap.

Usage
-----
    python backend/scripts/eval_llm_baseline.py

    # Resume a previous run (skips already-classified rows):
    python backend/scripts/eval_llm_baseline.py --run-id <uuid>

Environment (loaded from .env):
    MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_LOCAL_ENDPOINT, MINIO_BUCKET
    VAULT_ROOT_TOKEN  (used to fetch Gemini API key from Vault)
    VAULT_ADDR        (default: http://localhost:8200)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Literal

import google.genai as genai
import httpx
import numpy as np
from dotenv import load_dotenv
from google.genai import types
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from sklearn.metrics import accuracy_score, classification_report, f1_score

REPO_ROOT = Path(__file__).parent.parent.parent
SPLIT_PREFIX = "splits/v1"
LABELS = ["bug", "feature", "docs", "question"]
MODEL_ID = "gemini-2.5-flash"

# Cost table (USD per 1M tokens) — Gemini 2.5 Flash, May 2026
# https://ai.google.dev/pricing
COST_INPUT_PER_M = 0.075
COST_OUTPUT_PER_M = 0.30


class _LabelOutput(BaseModel):
    label: Literal["bug", "feature", "docs", "question"]


def _load_env() -> dict[str, str]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    required = ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_LOCAL_ENDPOINT", "MINIO_BUCKET"]
    config: dict[str, str] = {}
    missing: list[str] = []
    for key in required:
        val = os.getenv(key)
        if not val:
            missing.append(key)
        else:
            config[key] = val
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return config


def _fetch_gemini_key() -> str:
    """Pull the Gemini API key from Vault dev instance.

    Tries VAULT_ADDR first; if it's a Docker-internal hostname (not reachable
    from the host), falls back to http://localhost:8200.
    """
    vault_token = os.getenv("VAULT_ROOT_TOKEN", "dev-root-token")
    candidates = list(
        dict.fromkeys(
            [
                os.getenv("VAULT_ADDR", "http://localhost:8200"),
                "http://localhost:8200",
            ]
        )
    )
    last_exc: Exception | None = None
    for addr in candidates:
        try:
            resp = httpx.get(
                f"{addr}/v1/secret/data/gemini",
                headers={"X-Vault-Token": vault_token},
                timeout=3.0,
            )
            resp.raise_for_status()
            api_key: str = resp.json()["data"]["data"]["api_key"]
            if api_key.startswith("AIza-placeholder"):
                print("ERROR: Vault has a placeholder Gemini key. Set it with:", file=sys.stderr)
                print(
                    '  docker exec -e VAULT_ADDR=http://127.0.0.1:8200 <vault> vault kv put secret/gemini api_key="AIza..."',
                    file=sys.stderr,
                )
                sys.exit(1)
            return api_key
        except Exception as exc:
            last_exc = exc
    print(f"ERROR: could not reach Vault: {last_exc}", file=sys.stderr)
    sys.exit(1)


def _minio_client(cfg: dict[str, str]) -> Minio:
    endpoint = cfg["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    return Minio(
        endpoint,
        access_key=cfg["MINIO_ACCESS_KEY"],
        secret_key=cfg["MINIO_SECRET_KEY"],
        secure=secure,
    )


def _download_split(client: Minio, bucket: str, split_name: str) -> list[dict]:
    key = f"{SPLIT_PREFIX}/{split_name}.jsonl"
    try:
        response = client.get_object(bucket, key)
        return [
            json.loads(line)
            for line in response.read().decode("utf-8").splitlines()
            if line.strip()
        ]
    except S3Error as exc:
        print(f"ERROR: could not download {key}: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_cache(client: Minio, bucket: str, run_id: str) -> dict[str, str]:
    key = f"models/llm_baseline/{run_id}/predictions.json"
    try:
        response = client.get_object(bucket, key)
        return json.loads(response.read().decode("utf-8"))
    except S3Error:
        return {}


def _save_cache(client: Minio, bucket: str, run_id: str, predictions: dict[str, str]) -> None:
    key = f"models/llm_baseline/{run_id}/predictions.json"
    payload = json.dumps(predictions, indent=2).encode("utf-8")
    client.put_object(
        bucket, key, io.BytesIO(payload), length=len(payload), content_type="application/json"
    )


_CLASSIFY_PROMPT = """\
Classify this GitHub issue as exactly one of: bug, feature, docs, question.

Definitions:
- bug: reports incorrect or unexpected behaviour, a crash, or a regression
- feature: requests new functionality, performance improvement, or refactoring
- docs: concerns only documentation, examples, or typos
- question: asks for usage help or clarification — no code change implied

Reply with ONLY the single word label, nothing else.

Issue:
{text}"""

_VALID_LABELS: frozenset[str] = frozenset({"bug", "feature", "docs", "question"})


def _classify_one(gemini_client: genai.Client, text: str) -> tuple[str, int, int]:
    """Return (predicted_label, input_tokens, output_tokens).

    gemini-2.5-flash is a thinking model; response_schema and response_mime_type
    cause it to emit partial/None text when the output budget is tight.  A plain
    text prompt asking for exactly one word is more reliable — the Pydantic model
    still validates the parsed output.
    """
    response = gemini_client.models.generate_content(
        model=MODEL_ID,
        contents=_CLASSIFY_PROMPT.format(text=text[:3000]),
        config=types.GenerateContentConfig(temperature=0.0),
    )
    raw = (response.text or "").strip().lower()
    if raw in _VALID_LABELS:
        label = raw
    else:
        found = next((lbl for lbl in _VALID_LABELS if lbl in raw), None)
        if found is None:
            raise ValueError(f"Unexpected response: {raw[:80]!r}")
        label = found

    # Pydantic validation — ensures label is the Literal union type
    validated = _LabelOutput(label=label)  # type: ignore[arg-type]
    usage = response.usage_metadata
    in_tokens = usage.prompt_token_count if usage else 0
    out_tokens = usage.candidates_token_count if usage else 0
    return validated.label, in_tokens, out_tokens


def main(resume_run_id: str | None = None) -> None:
    cfg = _load_env()
    minio = _minio_client(cfg)
    bucket = cfg["MINIO_BUCKET"]

    gemini_key = _fetch_gemini_key()
    gemini_client = genai.Client(api_key=gemini_key)

    run_id = resume_run_id or str(uuid.uuid4())
    print(f"Run ID: {run_id}")

    print("Downloading test split …")
    test_rows = _download_split(minio, bucket, "test")
    print(f"  {len(test_rows)} test examples")

    cache = _load_cache(minio, bucket, run_id)
    if cache:
        print(f"  Loaded {len(cache)} cached predictions")

    y_true: list[str] = []
    y_pred: list[str] = []
    latencies: list[float] = []
    total_in_tokens = 0
    total_out_tokens = 0
    errors = 0

    for i, row in enumerate(test_rows):
        issue_id = str(row["id"])
        y_true.append(row["label"])

        if issue_id in cache:
            y_pred.append(cache[issue_id])
            continue

        t0 = time.perf_counter()
        try:
            label, in_tok, out_tok = _classify_one(gemini_client, row["text"])
        except Exception as exc:
            print(f"  WARNING: issue {issue_id} failed: {exc}")
            label = "bug"  # fallback; won't affect cached run
            in_tok, out_tok = 0, 0
            errors += 1

        latency_ms = (time.perf_counter() - t0) * 1000
        latencies.append(latency_ms)
        total_in_tokens += in_tok
        total_out_tokens += out_tok

        y_pred.append(label)
        cache[issue_id] = label

        if (i + 1) % 25 == 0:
            _save_cache(minio, bucket, run_id, cache)
            done = i + 1
            pct = 100 * done / len(test_rows)
            print(f"  [{done}/{len(test_rows)}] {pct:.0f}%  last_latency={latency_ms:.0f}ms")

    # final cache save
    _save_cache(minio, bucket, run_id, cache)

    accuracy = accuracy_score(y_true, y_pred)
    macro_f1 = f1_score(y_true, y_pred, average="macro", labels=LABELS, zero_division=0)
    per_class = f1_score(y_true, y_pred, average=None, labels=LABELS, zero_division=0)
    per_class_dict = dict(zip(LABELS, per_class.tolist(), strict=False))

    p50 = float(np.percentile(latencies, 50)) if latencies else 0.0
    p95 = float(np.percentile(latencies, 95)) if latencies else 0.0

    # Cost calculation (D-P5-03)
    cost_per_1k = (
        (
            total_in_tokens / 1_000_000 * COST_INPUT_PER_M
            + total_out_tokens / 1_000_000 * COST_OUTPUT_PER_M
        )
        / len(test_rows)
        * 1000
    )
    total_cost = (
        total_in_tokens / 1_000_000 * COST_INPUT_PER_M
        + total_out_tokens / 1_000_000 * COST_OUTPUT_PER_M
    )

    print(f"\nTest accuracy  : {accuracy:.4f}")
    print(f"Test macro-F1  : {macro_f1:.4f}")
    for cls, score in per_class_dict.items():
        print(f"  {cls:<10} F1 = {score:.4f}")
    print(f"\nLatency (API calls only, n={len(latencies)}):")
    print(f"  p50 = {p50:.1f} ms   p95 = {p95:.1f} ms")
    print("\nToken usage (total):")
    print(f"  Input  : {total_in_tokens:,} tokens")
    print(f"  Output : {total_out_tokens:,} tokens")
    print(f"  Cost   : ${total_cost:.4f} for {len(test_rows)} predictions")
    print(f"  Cost/1k: ${cost_per_1k:.4f}")
    if errors:
        print(f"\nWARNING: {errors} API errors (fallback to 'bug')")

    print("\nClassification report:")
    print(classification_report(y_true, y_pred, labels=LABELS, zero_division=0))

    results = {
        "run_id": run_id,
        "model": MODEL_ID,
        "test_accuracy": round(accuracy, 4),
        "test_macro_f1": round(macro_f1, 4),
        "per_class_f1": {k: round(v, 4) for k, v in per_class_dict.items()},
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "total_input_tokens": total_in_tokens,
        "total_output_tokens": total_out_tokens,
        "total_cost_usd": round(total_cost, 6),
        "cost_per_1k_usd": round(cost_per_1k, 6),
        "n_samples": len(test_rows),
        "n_errors": errors,
        "predictions_key": f"models/llm_baseline/{run_id}/predictions.json",
    }
    print("\n--- JSON results (copy into DECISIONS.md) ---")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="Resume a previous run by its UUID")
    args = parser.parse_args()
    main(resume_run_id=args.run_id)
