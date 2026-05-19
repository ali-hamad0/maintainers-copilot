#!/usr/bin/env python3
"""RAG golden-set evaluator.

Loads evals/golden/rag.jsonl (25 hand-curated triples), computes retrieval
metrics from a pre-computed CI fixture, optionally runs an LLM judge for
generation metrics, reports human-vs-judge agreement on 5 hand-labelled
examples, checks results against eval_thresholds.yaml, and uploads the
report to MinIO.

Exit 0  = all gated metrics passed their thresholds (or judge was skipped).
Exit 1  = one or more threshold regressions.

Retrieval metrics (from pre-computed fixture, always runs):
  hit@5    — fraction of questions where a relevant chunk appears in top-5
  MRR@10   — mean reciprocal rank of first relevant result in top-10
  Recall@10 — fraction of relevant chunks found in top-10 (avg over questions
               with at least one relevant chunk; not-in-corpus excluded)

Generation metrics (LLM judge, requires GEMINI_API_KEY or --skip-judge):
  faithfulness      -- answer grounded in retrieved context (0-1)
  answer_relevance  -- answer addresses the question (0-1)
  not_in_corpus questions are excluded from both metrics.

Agreement (reported, not gated):
  Percent of hand-labelled scores within 0.2 of judge scores on the same metric.
  not_in_corpus faithfulness disagreement is noted separately (known edge case).

Environment variables
---------------------
GEMINI_API_KEY
    Gemini API key for the judge calls.  Alternative: set VAULT_ROOT_TOKEN +
    VAULT_ADDR to fetch from Vault.

MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_LOCAL_ENDPOINT, MINIO_BUCKET
    MinIO credentials for uploading the report.  Skipped if absent.

GIT_SHA
    Commit SHA embedded in the MinIO upload path.  Defaults to git rev-parse.

Flags
-----
--skip-judge         Skip LLM judge generation eval entirely.
--skip-minio-upload  Do not upload the report to MinIO.
--report-path PATH   Where to write the JSON report (default: rag_eval_report.json).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

REPO_ROOT = Path(__file__).parent.parent
GOLDEN_PATH = Path(__file__).parent / "golden" / "rag.jsonl"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rag_retrieval_ci.jsonl"
THRESHOLDS_PATH = REPO_ROOT / "backend" / "eval_thresholds.yaml"
JUDGE_PROMPT_PATH = REPO_ROOT / "prompts" / "rag_judge.md"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-2.5-flash:generateContent"
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return os.getenv("GIT_SHA", "unknown")


def _load_judge_prompt() -> str:
    raw = JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# MinIO helpers
# ---------------------------------------------------------------------------


def _minio_cfg_from_env() -> dict[str, str] | None:
    keys = [
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_LOCAL_ENDPOINT",
        "MINIO_BUCKET",
    ]
    if not all(os.getenv(k) for k in keys):
        return None
    return {k: os.environ[k] for k in keys}


def _build_minio_client(cfg: dict[str, str]) -> Any:
    from minio import Minio  # type: ignore[import]

    endpoint = (
        cfg["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    )
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    return Minio(
        endpoint,
        access_key=cfg["MINIO_ACCESS_KEY"],
        secret_key=cfg["MINIO_SECRET_KEY"],
        secure=secure,
    )


def _upload_report(cfg: dict[str, str], git_sha: str, report_bytes: bytes) -> None:
    client = _build_minio_client(cfg)
    key = f"evals/{git_sha}/rag.json"
    client.put_object(
        cfg["MINIO_BUCKET"],
        key,
        io.BytesIO(report_bytes),
        length=len(report_bytes),
        content_type="application/json",
    )
    print(f"  Uploaded -> minio:{key}")


# ---------------------------------------------------------------------------
# Retrieval metrics (fixture-based, always runs)
# ---------------------------------------------------------------------------


def compute_retrieval_metrics(
    golden: list[dict[str, Any]],
    fixture: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute hit@5, MRR@10, Recall@10 from the pre-computed relevance fixture.

    not_in_corpus questions (n_relevant == 0):
      - hit@5:    0 (by definition, no relevant chunk exists)
      - MRR@10:   0 (no first-relevant rank possible)
      - Recall@10: excluded from the average (undefined, not vacuously 1)
    """
    by_id = {row["id"]: row for row in fixture}

    hit5_scores: list[float] = []
    mrr_scores: list[float] = []
    recall_scores: list[float] = []

    per_question: list[dict[str, Any]] = []

    for row in golden:
        qid = row["id"]
        n_relevant = int(row.get("n_relevant", 0))
        relevance = by_id[qid]["top_10_relevance"]  # list[bool], length 10

        if n_relevant == 0:
            hit5 = 0.0
            mrr = 0.0
            recall = None  # excluded
        else:
            top5 = relevance[:5]
            hit5 = 1.0 if any(top5) else 0.0

            first_rank: int | None = None
            for i, rel in enumerate(relevance):
                if rel:
                    first_rank = i + 1  # 1-indexed
                    break
            mrr = (1.0 / first_rank) if first_rank is not None else 0.0

            found = sum(relevance[:10])
            recall = found / n_relevant

        hit5_scores.append(hit5)
        mrr_scores.append(mrr)
        if recall is not None:
            recall_scores.append(recall)

        per_question.append(
            {
                "id": qid,
                "category": row.get("category"),
                "n_relevant": n_relevant,
                "hit_at_5": hit5,
                "mrr_at_10": round(mrr, 4),
                "recall_at_10": round(recall, 4) if recall is not None else None,
            }
        )

    return {
        "hit_at_5": round(sum(hit5_scores) / len(hit5_scores), 4),
        "mrr_at_10": round(sum(mrr_scores) / len(mrr_scores), 4),
        "recall_at_10": round(sum(recall_scores) / len(recall_scores), 4)
        if recall_scores
        else None,
        "n_questions": len(golden),
        "n_with_relevant": sum(1 for r in golden if r.get("n_relevant", 0) > 0),
        "per_question": per_question,
    }


# ---------------------------------------------------------------------------
# Generation metrics (LLM judge)
# ---------------------------------------------------------------------------


def _fetch_gemini_key_from_vault() -> str | None:
    vault_token = os.getenv("VAULT_ROOT_TOKEN", "dev-root-token")
    vault_addr = os.getenv("VAULT_ADDR", "http://localhost:8200")
    try:
        resp = httpx.get(
            f"{vault_addr}/v1/secret/data/gemini",
            headers={"X-Vault-Token": vault_token},
            timeout=3.0,
        )
        resp.raise_for_status()
        key: str = resp.json()["data"]["data"]["api_key"]
        return None if key.startswith("AIza-placeholder") else key
    except Exception:
        return None


def _call_judge(
    http: httpx.Client,
    api_key: str,
    prompt_template: str,
    question: str,
    context: str,
    answer: str,
) -> dict[str, float] | None:
    prompt = (
        prompt_template.replace("{question}", question)
        .replace("{context}", context)
        .replace("{answer}", answer)
    )
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 64, "temperature": 0.0},
    }
    try:
        resp = http.post(
            GEMINI_URL, params={"key": api_key}, json=payload, timeout=30.0
        )
        resp.raise_for_status()
        raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip any accidental markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1].lstrip("json").strip()
        parsed: dict[str, float] = json.loads(raw_text)
        return {
            "faithfulness": float(parsed.get("faithfulness", 0.0)),
            "answer_relevance": float(parsed.get("answer_relevance", 0.0)),
        }
    except (
        httpx.HTTPError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        ValueError,
    ) as exc:
        print(f"    WARN: judge call failed: {exc}", file=sys.stderr)
        return None


def compute_generation_metrics(
    golden: list[dict[str, Any]],
    api_key: str,
) -> dict[str, Any]:
    """Run LLM judge on each non-empty example and return aggregate scores."""
    prompt_template = _load_judge_prompt()
    faithfulness_scores: list[float] = []
    relevance_scores: list[float] = []
    per_question: list[dict[str, Any]] = []
    errors = 0

    with httpx.Client() as http:
        for i, row in enumerate(golden):
            qid = row["id"]
            question = row["question"]
            context = row.get("ground_truth_context", "")
            answer = row.get("ideal_answer", "")
            category = row.get("category", "")

            # Skip not_in_corpus: empty answer has no generation quality signal
            if category == "not_in_corpus" or not answer.strip():
                per_question.append(
                    {
                        "id": qid,
                        "category": category,
                        "faithfulness": None,
                        "answer_relevance": None,
                        "skipped": True,
                    }
                )
                continue

            print(f"  [{i + 1}/25] {qid} ...", end=" ", flush=True)
            scores = _call_judge(
                http, api_key, prompt_template, question, context, answer
            )
            if scores is None:
                errors += 1
                print("ERROR", flush=True)
                per_question.append(
                    {
                        "id": qid,
                        "category": category,
                        "faithfulness": None,
                        "answer_relevance": None,
                        "skipped": False,
                        "error": True,
                    }
                )
                continue

            faith = scores["faithfulness"]
            relevance = scores["answer_relevance"]

            # faithfulness = -1.0 signals "context empty, not applicable"
            if faith >= 0.0:
                faithfulness_scores.append(faith)
            relevance_scores.append(relevance)

            print(f"faithfulness={faith:.2f}  relevance={relevance:.2f}", flush=True)
            per_question.append(
                {
                    "id": qid,
                    "category": category,
                    "faithfulness": faith if faith >= 0.0 else None,
                    "answer_relevance": relevance,
                    "skipped": False,
                }
            )

    if errors:
        print(f"  {errors} judge call(s) failed", file=sys.stderr)

    return {
        "faithfulness": round(sum(faithfulness_scores) / len(faithfulness_scores), 4)
        if faithfulness_scores
        else None,
        "answer_relevance": round(sum(relevance_scores) / len(relevance_scores), 4)
        if relevance_scores
        else None,
        "n_scored": len(faithfulness_scores),
        "n_skipped": sum(1 for q in per_question if q.get("skipped")),
        "n_errors": errors,
        "per_question": per_question,
    }


# ---------------------------------------------------------------------------
# Hand-label agreement
# ---------------------------------------------------------------------------

_AGREE_TOLERANCE = 0.2


def compute_agreement(golden: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare human scores vs judge scores for hand-labelled examples.

    Agreement = both scores within AGREE_TOLERANCE = 0.2 on the same metric.
    not_in_corpus faithfulness is flagged separately as a known edge case.
    """
    labelled = [r for r in golden if r.get("hand_labelled")]
    comparisons: list[dict[str, Any]] = []
    agree_count = 0
    total_count = 0

    for row in labelled:
        qid = row["id"]
        category = row.get("category", "")

        for metric in ("faithfulness", "answer_relevance"):
            human_key = f"human_{metric}"
            judge_key = f"judge_{metric}"

            human_val = row.get(human_key)
            judge_val = row.get(judge_key)

            if human_val is None or judge_val is None:
                continue

            diff = abs(human_val - judge_val)
            agrees = diff <= _AGREE_TOLERANCE
            is_nic_faith = category == "not_in_corpus" and metric == "faithfulness"

            if not is_nic_faith:
                total_count += 1
                agree_count += 1 if agrees else 0

            comparisons.append(
                {
                    "id": qid,
                    "metric": metric,
                    "human": human_val,
                    "judge": judge_val,
                    "diff": round(diff, 3),
                    "agrees": agrees,
                    "excluded_nic_faithfulness": is_nic_faith,
                }
            )

    pct = round(agree_count / total_count, 4) if total_count > 0 else None

    return {
        "n_hand_labelled": len(labelled),
        "n_comparisons_counted": total_count,
        "n_agree": agree_count,
        "agreement_pct": pct,
        "tolerance": _AGREE_TOLERANCE,
        "comparisons": comparisons,
    }


# ---------------------------------------------------------------------------
# Threshold gate
# ---------------------------------------------------------------------------


def check_thresholds(
    retrieval: dict[str, Any],
    generation: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    rag_thresh: dict[str, Any] = thresholds.get("rag", {})

    hit5_thresh = float(rag_thresh.get("hit_at_5", 0))
    hit5_actual = retrieval.get("hit_at_5")
    if hit5_actual is not None and hit5_actual < hit5_thresh:
        failures.append(f"rag.hit_at_5: {hit5_actual:.4f} < threshold {hit5_thresh}")

    if generation is not None:
        faith_thresh = float(rag_thresh.get("faithfulness", 0))
        faith_actual = generation.get("faithfulness")
        if faith_actual is not None and faith_actual < faith_thresh:
            failures.append(
                f"rag.faithfulness: {faith_actual:.4f} < threshold {faith_thresh}"
            )

        rel_thresh = float(rag_thresh.get("answer_relevance", 0))
        rel_actual = generation.get("answer_relevance")
        if rel_actual is not None and rel_actual < rel_thresh:
            failures.append(
                f"rag.answer_relevance: {rel_actual:.4f} < threshold {rel_thresh}"
            )

    return failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RAG golden-set evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip LLM judge generation eval (retrieval metrics still run)",
    )
    parser.add_argument("--skip-minio-upload", action="store_true")
    parser.add_argument(
        "--report-path",
        default="rag_eval_report.json",
        help="Path to write the JSON report (default: rag_eval_report.json)",
    )
    args = parser.parse_args(argv)

    golden = load_jsonl(GOLDEN_PATH)
    if not golden:
        print(f"ERROR: golden set is empty at {GOLDEN_PATH}", file=sys.stderr)
        return 2
    if len(golden) != 25:
        print(f"ERROR: expected 25 golden examples, got {len(golden)}", file=sys.stderr)
        return 2

    fixture = load_jsonl(FIXTURE_PATH)
    if len(fixture) != 25:
        print(f"ERROR: expected 25 fixture rows, got {len(fixture)}", file=sys.stderr)
        return 2

    from collections import Counter

    categories = Counter(r.get("category") for r in golden)
    print(f"Loaded {len(golden)} golden examples  (categories: {dict(categories)})")

    thresholds: dict[str, Any] = yaml.safe_load(
        THRESHOLDS_PATH.read_text(encoding="utf-8")
    )
    minio_cfg = _minio_cfg_from_env()
    git_sha = _git_sha()

    report: dict[str, Any] = {
        "git_sha": git_sha,
        "n_golden": len(golden),
        "retrieval": {},
        "generation": None,
        "agreement": {},
    }

    # ------------------------------------------------------------------
    # Phase 1: Retrieval metrics (always runs from fixture)
    # ------------------------------------------------------------------
    print("\n[1/3] Retrieval metrics (pre-computed fixture)")
    retrieval_metrics = compute_retrieval_metrics(golden, fixture)
    report["retrieval"] = retrieval_metrics
    print(
        f"  hit@5={retrieval_metrics['hit_at_5']:.4f}  "
        f"MRR@10={retrieval_metrics['mrr_at_10']:.4f}  "
        f"Recall@10={retrieval_metrics['recall_at_10']}"
    )

    # ------------------------------------------------------------------
    # Phase 2: Generation metrics (LLM judge, optional)
    # ------------------------------------------------------------------
    print("\n[2/3] Generation metrics (LLM judge)")
    generation_metrics: dict[str, Any] | None = None

    if args.skip_judge:
        print("  Skipped by --skip-judge flag")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY") or _fetch_gemini_key_from_vault()
        if gemini_key:
            generation_metrics = compute_generation_metrics(golden, gemini_key)
            faith = generation_metrics.get("faithfulness")
            rel = generation_metrics.get("answer_relevance")
            print(
                f"\n  faithfulness={faith}  answer_relevance={rel}  "
                f"(n_scored={generation_metrics['n_scored']}, "
                f"n_skipped={generation_metrics['n_skipped']})"
            )
        else:
            print(
                "  SKIP: GEMINI_API_KEY not set and Vault unreachable"
                " — run with --skip-judge for offline mode"
            )

    report["generation"] = generation_metrics

    # ------------------------------------------------------------------
    # Phase 3: Hand-label agreement
    # ------------------------------------------------------------------
    print("\n[3/3] Hand-label agreement (5 examples)")
    agreement = compute_agreement(golden)
    report["agreement"] = agreement
    pct = agreement.get("agreement_pct")
    print(
        f"  {agreement['n_agree']}/{agreement['n_comparisons_counted']} agree "
        f"(tolerance ±{agreement['tolerance']})  "
        f"agreement={pct:.1%}"
        if pct is not None
        else "  (no comparisons)"
    )
    for cmp in agreement["comparisons"]:
        flag = "" if cmp["agrees"] else "  << DISAGREE"
        nic = " [nic-faith-excluded]" if cmp.get("excluded_nic_faithfulness") else ""
        print(
            f"    {cmp['id']} {cmp['metric']}: human={cmp['human']}  "
            f"judge={cmp['judge']}  diff={cmp['diff']}{flag}{nic}"
        )

    # ------------------------------------------------------------------
    # Threshold gate
    # ------------------------------------------------------------------
    failures = check_thresholds(retrieval_metrics, generation_metrics, thresholds)
    report["threshold_failures"] = failures
    report["passed"] = not failures

    # Write report
    report_bytes = json.dumps(report, indent=2).encode("utf-8")
    report_path = Path(args.report_path)
    report_path.write_bytes(report_bytes)
    print(f"\nReport -> {report_path}")

    # Upload to MinIO
    if not args.skip_minio_upload and minio_cfg is not None:
        try:
            _upload_report(minio_cfg, git_sha, report_bytes)
        except Exception as exc:
            print(f"WARN: MinIO upload failed: {exc}", file=sys.stderr)

    # Final verdict
    if failures:
        print(f"\nFAIL -- {len(failures)} threshold regression(s):")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    if generation_metrics is None and not args.skip_judge:
        print(
            "\nWARN: judge skipped (no API key) — faithfulness and answer_relevance "
            "thresholds not enforced"
        )

    print("\nPASS -- all enforced thresholds met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
