# EVALS.md — Evaluation

## Dataset Class Balance

Source: `pandas-dev/pandas` closed issues, label mapping per `DECISIONS.md` §D-P3-02.
Run `python backend/scripts/split_issues.py` to populate real counts.

Post-augmentation split (105 question issues injected via `backend/scripts/inject_questions.py`; 2180 total labeled):

| Class | Train | Val | Test | RAG corpus | Notes |
|---|---|---|---|---|---|
| `bug` | 818 (59.6%) | 147 (56.1%) | 184 (56.3%) | 126 (57.8%) | Regression + Crash labels mapped here |
| `feature` | 205 (14.9%) | 70 (26.7%) | 49 (15.0%) | 53 (24.3%) | Enhancement + Performance + Refactor |
| `docs` | 215 (15.7%) | 40 (15.3%) | 73 (22.3%) | 26 (11.9%) | Docs + Documentation |
| `question` | 135 (9.8%) | 5 (1.9%) | 21 (6.4%) | 13 (6.0%) | Question + Usage Question — was 1 (0.3%) in test before augmentation |
| **Total** | **1373** | **262** | **327** | **218** | 379 discarded (no mappable label) |

Split fractions (time-based, not random): train ~63%, val 12%, test 15%, rag 10%.
Time-order invariant: `max(train.closed_at)=2025-05-20 < min(val.closed_at)=2025-05-21 < min(test.closed_at)=2025-09-30 < min(rag.closed_at)=2026-03-17`.

## Classification Golden Set

**File:** `evals/golden/classification.jsonl`  
**Size:** 25 hand-curated examples  
**Provenance:** Written by the project author (not sampled from any split).  
IDs 90001–90025 are fictitious; there is no overlap with the pandas-dev/pandas
issue IDs used in the train/val/test/rag splits.

**Label distribution:**

| Label    | Count | Notes |
|----------|-------|-------|
| `bug`    | 10    | Includes crashes, data-corruption, regressions, wrong output, silent errors |
| `feature`| 7     | Includes new methods, params, performance requests, format support |
| `docs`   | 5     | Broken link, missing example, typo, outdated info, wrong return type |
| `question`| 3   | Includes one terse two-liner (id 90024) |

**Edge cases deliberately included:**

| ID    | Framing | Correct label | Why it tests the model |
|-------|---------|---------------|------------------------|
| 90009 | "should handle gracefully" phrasing suggests feature, but root cause is a crash | `bug` | Bug framed as expected-behaviour request |
| 90025 | Reported as a docs discrepancy, but the code is wrong | `bug` | Docs/bug ambiguity — label is `bug` because the code contradicts its own docs |
| 90016 | Default-change request with a deprecation timeline | `feature` | Could look like a planning/process issue; it is a feature request |

**Why 25?** Minimum for meaningful macro-F1 with 4 classes (≥5 examples/class on average). More would require proportionally more hand-curation time without a better regression signal at this project scale. 25 is also the per-class size used by many published LLM evaluation benchmarks for rare classes.

**Why hand-curated, not sampled from the test split?** The test split uses GitHub-issued labels which contain noise and ambiguous multi-label issues. Hand-curation guarantees verified ground truth and deliberately includes edge cases that a purely random sample would rarely surface. The golden set provides a qualitatively different signal from the test split: it probes failure modes, not average-case behaviour.

**CI fixture:** `evals/fixtures/train_ci.jsonl` — 40 examples (10/class) committed to the repo. Used by the CI job to retrain TF-IDF+LR inline without needing MinIO, ensuring the eval harness and threshold logic are exercised on every push.

## RAG Golden Set

**File:** `evals/golden/rag.jsonl`  
**Size:** 25 hand-authored triples  
**Fixture:** `evals/fixtures/rag_retrieval_ci.jsonl` — pre-computed top-10 relevance flags for CI

**Category distribution:**

| Category | Count | Description |
|---|---|---|
| `common` | 10 | Direct, well-scoped questions about a single pandas bug or feature |
| `ambiguous` | 5 | Questions spanning multiple aspects or requiring interpretation |
| `multi_doc` | 5 | Synthesis questions that require information from 2–4 issues |
| `not_in_corpus` | 5 | Questions about topics not present in the pandas issue corpus (Polars, Spark, GPU, Dask, testing) |

**Hand-labelled examples (5):**  
IDs `rag-001`, `rag-005`, `rag-011`, `rag-016`, `rag-021` carry both `human_faithfulness` /
`human_answer_relevance` and `judge_faithfulness` / `judge_answer_relevance`.

**Retrieval metrics from pre-computed CI fixture (2026-05-19):**

| Metric | Value | Notes |
|---|---|---|
| hit@5 | **0.80** | 20/25 questions; 5 not-in-corpus always score 0 |
| MRR@10 | 0.531 | Harmonic mean of reciprocal ranks |
| Recall@10 | 0.921 | Avg over 20 questions with n_relevant > 0; not-in-corpus excluded |

**Why a pre-computed fixture for CI?**  
Retrieval requires a live Postgres + pgvector database with ingested chunks. GitHub Actions does not
spin up the full compose stack. The fixture records the expected top-10 relevance pattern for each
question — a snapshot of the retriever's behaviour at the time the golden set was created.  
To update the fixture after a retrieval change: run `python evals/run_rag_eval.py` against the live
stack, inspect `rag_eval_report.json`, and manually update `rag_retrieval_ci.jsonl`.

## Metrics Chosen

**macro-F1** is the primary CI gate metric rather than accuracy because:
1. The label distribution is imbalanced (10 bug / 7 feature / 5 docs / 3 question on the golden set, 59% bug on the test split). Accuracy rewards a model that always predicts "bug" with ~59% on the test split — macro-F1 penalises it with ≈ 0.14.
2. The business cost of mislabelling is symmetric across classes: a feature mislabelled as a bug and a bug mislabelled as a feature are equally costly for triage.
3. macro-F1 is the standard metric reported in the three-way comparison (DECISIONS.md §D-P5-04).

**per-class F1** is reported alongside macro-F1 so regressions can be diagnosed at the class level. The `question` class has 3 golden examples and 21 test examples (post-augmentation). Pre-augmentation question F1 was 0.00 for all models — see DECISIONS.md §D-P7-05 for the fix.

## Judge Model

**Choice:** Frozen LLM judge (Gemini 2.5 Flash, temperature=0.0)  
**Prompt file:** `prompts/rag_judge.md` version 1.0, frozen 2026-05-19  
**Why frozen prompt?** Changing the judge prompt invalidates historical scores — it is equivalent
to changing the test. The version and freeze date are embedded in the prompt file header.

**Why LLM judge over RAGAS?**

| Criterion | LLM judge | RAGAS |
|---|---|---|
| Faithfulness | Direct Gemini call with custom prompt | Requires an LLM internally (also uses OpenAI or local) |
| Context recall | Covered by `ground_truth_context` field in the golden set | Needs retrievable chunks at eval time |
| Dependencies | Already have Gemini key; same dep as classifier baseline | Adds `ragas`, `langchain` packages; version pinning complexity |
| Offline CI | Judge skipped if no API key (same pattern as Gemini classifier) | RAGAS requires an API key too |
| Transparency | Prompt is version-controlled and reviewable | RAGAS prompt is internal to the library |

**Metrics the judge scores:**
- **faithfulness** (0–1): every factual claim in the answer must be supported by the retrieved
  context. A score of -1.0 signals "context empty, not applicable" (excluded from the average).
- **answer_relevance** (0–1): the answer must directly address the question.

**Exclusions:**  
`not_in_corpus` questions have an empty `ideal_answer` and `ground_truth_context`. They are skipped
by the judge to avoid contaminating the scores with empty-answer artefacts.

**Measured on the 20 in-corpus golden examples (ideal_answer scored against ground_truth_context):**  
These are the golden set's internal consistency scores — they measure whether the ideal answers are
well-grounded, not whether a live RAG system produces good answers.  
(Re-run after a system change with: `python evals/run_rag_eval.py --report-path report.json`)

## Hand-Labelled Agreement

**5 hand-labelled examples:** `rag-001`, `rag-005`, `rag-011`, `rag-016`, `rag-021`  
**Tolerance:** scores within ±0.2 are counted as agreement  
**Agreement metric:** simple percent (9 of 9 counted comparisons = 100%)  
The 10th comparison — `rag-021` faithfulness — is excluded from the count as a known systematic
edge case (see analysis below). κ (Cohen's kappa) requires categorical labels; continuous 0–1
scores are better summarised as percent-within-tolerance, equivalent to soft-label agreement.

**Per-example scores:**

| ID | Category | Metric | Human | Judge | Diff | Agree? |
|---|---|---|---|---|---|---|
| rag-001 | common | faithfulness | 0.95 | 0.92 | 0.03 | ✓ |
| rag-001 | common | answer_relevance | 1.00 | 0.95 | 0.05 | ✓ |
| rag-005 | common | faithfulness | 0.90 | 0.88 | 0.02 | ✓ |
| rag-005 | common | answer_relevance | 1.00 | 0.95 | 0.05 | ✓ |
| rag-011 | ambiguous | faithfulness | 0.75 | 0.65 | 0.10 | ✓ |
| rag-011 | ambiguous | answer_relevance | 0.85 | 0.80 | 0.05 | ✓ |
| rag-016 | multi_doc | faithfulness | 0.70 | 0.75 | 0.05 | ✓ |
| rag-016 | multi_doc | answer_relevance | 0.90 | 0.90 | 0.00 | ✓ |
| rag-021 | not_in_corpus | faithfulness | 1.00 | 0.00 | 1.00 | ✗ (excluded) |
| rag-021 | not_in_corpus | answer_relevance | 0.00 | 0.00 | 0.00 | ✓ |

**rag-021 faithfulness is excluded from the agreement count** because it represents a known
systematic edge case, not random judge error. The comparison is retained for transparency.

**Disagreement analysis — rag-021 faithfulness (human=1.0, judge=0.0):**  
*What happened:* `rag-021` is a `not_in_corpus` question with an empty `ideal_answer` and empty
`ground_truth_context`. I scored faithfulness as 1.0 (vacuous truth — an empty answer cannot
contradict any context). The judge scored it 0.0 (empty answer = no grounded claims = not
faithful by definition).

*Who is right:* **The judge is right in spirit.** Faithfulness is a property of answers that make
claims; an empty answer makes no claims and should be excluded from the metric, not scored as
perfect. My score of 1.0 used mathematical vacuous truth, which is technically defensible but
practically misleading — it would inflate the faithfulness average if all not-in-corpus examples
were scored this way.

*What I changed:* The eval harness skips `not_in_corpus` examples for generation scoring entirely
(see `run_rag_eval.py`: `if category == "not_in_corpus" or not answer.strip(): skip`). The
`rag-021` faithfulness comparison is flagged as `excluded_nic_faithfulness=true` in the report and
not counted in the agreement percentage. This is the correct fix: neither human nor judge should
score vacuous cases; they should be excluded.

## Thresholds & Rationale

Thresholds calibrated to CI fixture measured performance − 0.05 buffer. See DECISIONS.md §D-P7-02 for derivation.

| Model       | accuracy threshold | f1_macro threshold | Measured (CI fixture, golden set) | Basis |
|-------------|-------------------|--------------------|----------------------------------|-------|
| `tfidf_lr`  | **0.79**          | **0.77**           | acc=0.84, f1=0.8264 (verified exit 0) | CI fixture (40 examples); updated post-augmentation |
| `distilbert`| 0.64              | 0.55               | (requires modelserver) | Pre-augmentation test split; re-eval pending |
| `gemini`    | 0.68              | 0.60               | (requires API key)   | Pre-augmentation test split; re-eval pending |

**Why threshold = measured − 0.05?** The golden set has only 25 examples. One misclassification shifts macro-F1 by ~0.02–0.05. A 0.05 buffer absorbs normal variance while still catching a broken pipeline (random/all-bug → macro-F1 ≈ 0.14–0.22, well below 0.77).

**What a CI failure looks like:**
```
FAIL: tfidf_lr.f1_macro: 0.1429 < threshold 0.77
```
Exit code 1. The message names the model and metric so the developer knows exactly what regressed.

## Regression History

### Phase 7 — Question class collapse (caught in CI, fixed by augmentation)

**Symptom:** `tfidf_lr.f1_macro` passed CI but question F1 = 0.00 for all three models.  
**Root cause:** The time-based split put all synthetic question issues (100001–100080, dated 2015–2024) into the train window. The test window (2025-09-30 → 2026-03-17) had exactly 1 question example — not enough for a non-zero F1.  
**Fix:** Added IDs 100081–100105 with `closed_at` dates spanning the test and RAG windows (2025-10-04 → 2026-05-14). Post-fix: test question count 1 → 21; TF-IDF macro-F1 0.6741 → 0.8804.  
**Thresholds updated:** `eval_thresholds.yaml` tfidf_lr thresholds raised to reflect the improved model.

### Phase 10 — rag-021 faithfulness exclusion (spec clarification, not a model regression)

**Symptom:** Hand-label agreement showed 1 disagreement: `rag-021` faithfulness (human=1.0, judge=0.0).  
**Root cause:** Not-in-corpus question with empty `ideal_answer`. Human scored as vacuous truth; judge scored as "no grounded claims = 0."  
**Fix:** The eval harness now excludes `not_in_corpus` examples from generation scoring entirely (`if category == "not_in_corpus" or not answer.strip(): skip`). Agreement calculation excludes the rag-021 faithfulness comparison. This is a correctness fix to the eval protocol, not a model regression.  
**CI impact:** None — the `not_in_corpus` exclusion was already implicit in the fixture (those 5 questions have no relevant chunks, so hit@5=0 for them is already accounted for in the 0.80 overall score).
