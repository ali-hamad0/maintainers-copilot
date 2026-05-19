#!/usr/bin/env python3
"""Generate confusion matrix PNG from the Phase 4 training run outputs.

The matrix values come directly from the Colab notebook Cell 13 output
(test split, run 1f610ed8-b3a0-4a96-b301-5fe445813019).

Usage
-----
    uv run --with matplotlib --with seaborn python backend/scripts/generate_confusion_matrix.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

# Test-set confusion matrix from notebook Cell 13 output.
# Rows = true label, Columns = predicted label.
# Classes (order matches LABEL2ID): bug=0, feature=1, docs=2, question=3
CM = np.array(
    [
        [179, 5, 1, 0],  # true: bug
        [14, 37, 0, 0],  # true: feature
        [8, 4, 62, 0],  # true: docs
        [1, 0, 0, 0],  # true: question
    ],
    dtype=int,
)

LABELS = ["bug", "feature", "docs", "question"]
SUPPORT = CM.sum(axis=1)
ACCURACY = CM.diagonal().sum() / CM.sum()

OUT_PATH = Path(__file__).parent.parent.parent / "evals" / "artefacts" / "confusion_matrix.png"


def main() -> None:
    fig, ax = plt.subplots(figsize=(7, 6))

    # Normalise by true-label count for colour, show raw counts as text.
    cm_norm = CM.astype(float) / SUPPORT[:, None]

    sns.heatmap(
        cm_norm,
        annot=CM,
        fmt="d",
        cmap="Blues",
        xticklabels=LABELS,
        yticklabels=LABELS,
        linewidths=0.5,
        linecolor="lightgrey",
        ax=ax,
        vmin=0.0,
        vmax=1.0,
        cbar_kws={"label": "Recall (row-normalised)"},
    )

    ax.set_xlabel("Predicted label", fontsize=11)
    ax.set_ylabel("True label", fontsize=11)
    ax.set_title(
        f"DistilBERT Classifier — Test Set Confusion Matrix\n"
        f"run 1f610ed8  |  n=311  |  accuracy={ACCURACY:.4f}  |  macro-F1=0.6483",
        fontsize=10,
        pad=12,
    )
    ax.tick_params(axis="x", rotation=0)
    ax.tick_params(axis="y", rotation=0)

    fig.tight_layout()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    main()
