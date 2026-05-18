# EVALS.md — Evaluation

To be filled in Phase 15.

## Dataset Class Balance

Source: `pandas-dev/pandas` closed issues, label mapping per `DECISIONS.md` §D-P3-02.
Run `python backend/scripts/split_issues.py` to populate real counts.

| Class | Train | Val | Test | RAG corpus | Notes |
|---|---|---|---|---|---|
| `bug` | 828 (63.4%) | 138 (55.4%) | 185 (59.5%) | 124 (59.6%) | Regression + Crash labels mapped here |
| `feature` | 208 (15.9%) | 67 (26.9%) | 51 (16.4%) | 51 (24.5%) | Enhancement + Performance + Refactor |
| `docs` | 216 (16.5%) | 39 (15.7%) | 74 (23.8%) | 25 (12.0%) | Docs + Documentation |
| `question` | 55 (4.2%) | 5 (2.0%) | 1 (0.3%) | 8 (3.8%) | Question + Usage Question |
| **Total** | **1307** | **249** | **311** | **208** | 379 discarded (no mappable label) |

Split fractions (time-based, not random): train ~63%, val 12%, test 15%, rag 10%.
Time-order invariant: `max(train.closed_at) < min(val.closed_at) < min(test.closed_at) < min(rag.closed_at)`.

## Classification Golden Set

(Provenance, 25 examples, coverage)

## RAG Golden Set

(25 question/ideal-answer/ground-truth triples)

## Metrics Chosen

(Why these metrics, not others)

## Judge Model

(LLM judge or RAGAS choice + rationale)

## Hand-Labelled Agreement

(5 of 25, κ or percent agreement with automated judge)

## Thresholds & Rationale

(Why X for macro-F1, why Y for hit@5)

## Regression History

(Any regressions caught in CI, how fixed)
