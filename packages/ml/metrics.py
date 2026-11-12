"""Metrics with explicit undefined values for degenerate samples."""
from __future__ import annotations

from math import sqrt
from statistics import mean


def regression(actual: list[float], predicted: list[float]) -> dict:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("paired nonempty samples required")
    errors = [a - p for a, p in zip(actual, predicted)]
    mse = mean(e * e for e in errors)
    denominator = sum((a - mean(actual)) ** 2 for a in actual)
    return {"sample_count": len(actual), "mae": mean(abs(e) for e in errors),
            "rmse": sqrt(mse), "r2_score": None if denominator == 0 else 1 - sum(e * e for e in errors) / denominator}


def classification(actual: list[str], predicted: list[str], labels: list[str]) -> dict:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("paired nonempty samples required")
    matrix = [[sum(a == x and p == y for a, p in zip(actual, predicted)) for y in labels] for x in labels]
    f1 = []
    for i in range(len(labels)):
        tp = matrix[i][i]
        fp = sum(row[i] for row in matrix) - tp
        fn = sum(matrix[i]) - tp
        f1.append(None if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn))
    defined = [value for value in f1 if value is not None]
    return {"sample_count": len(actual), "accuracy": sum(a == p for a, p in zip(actual, predicted)) / len(actual),
            "f1_macro": mean(defined) if defined else None, "confusion_matrix": matrix, "labels": labels}
