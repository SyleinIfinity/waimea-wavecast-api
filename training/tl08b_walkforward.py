"""Expanding-window, embargoed retrospective checks for daily horizons."""
from __future__ import annotations

from datetime import timedelta

from packages.ml.features import Sample
from packages.ml.metrics import regression
from training.train_regression import train as fit_regression


def evaluate_folds(samples: list[Sample], *, horizon_days: int, folds: int = 3) -> list[dict]:
    if horizon_days < 1 or folds < 2:
        raise ValueError("invalid fold configuration")
    ordered = sorted(samples, key=lambda sample: sample.anchor_date_hst)
    if len(ordered) < 300:
        raise ValueError("at least 300 samples required")
    results = []
    n = len(ordered)
    # Disjoint test blocks in the most recent 45% of observations.
    block = max(30, int(n * .45) // folds)
    for fold in range(folds):
        test_start = n - block * (folds - fold)
        test = ordered[test_start:test_start + block]
        validation_start = max(0, test_start - block)
        validation = [sample for sample in ordered[validation_start:test_start]
                      if sample.target_date_hst < test[0].anchor_date_hst]
        train = [sample for sample in ordered[:validation_start]
                 if sample.target_date_hst < validation[0].anchor_date_hst] if validation else []
        if min(len(train), len(validation), len(test)) < 20:
            raise ValueError("fold too small after target embargo")
        if train[-1].target_date_hst >= validation[0].anchor_date_hst or validation[-1].target_date_hst >= test[0].anchor_date_hst:
            raise AssertionError("target leakage across folds")
        trained = fit_regression(train, validation, test)
        outcome = trained["test"]
        train_mean = sum(float(row.target) for row in train) / len(train)
        actual = outcome["actual"]
        baseline = regression(actual, [train_mean] * len(actual))
        model_mae = outcome["model"]["mae"]
        persistence_mae = outcome["persistence"]["mae"]
        results.append({"fold": fold + 1, "horizon_days": horizon_days,
                        "train_end_hst": str(train[-1].anchor_date_hst),
                        "validation_end_hst": str(validation[-1].anchor_date_hst),
                        "test_start_hst": str(test[0].anchor_date_hst),
                        "test_end_hst": str(test[-1].anchor_date_hst),
                        "train_count": len(train), "validation_count": len(validation), "test_count": len(test),
                        "model": outcome["model"], "persistence": outcome["persistence"],
                        "train_mean": baseline,
                        "skill_vs_persistence": None if persistence_mae == 0 else 1 - model_mae / persistence_mae})
    return results
