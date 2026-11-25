"""Chronological regression training with a persistence baseline."""
from __future__ import annotations

from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from packages.ml.features import FEATURE_NAMES, Sample, matrix
from packages.ml.metrics import regression


def train(train_rows: list[Sample], validation: list[Sample], test: list[Sample], *, seed: int = 42) -> dict:
    if any(row.target is None for row in train_rows + validation + test):
        raise ValueError("supervised targets required")
    model = Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                      ("regressor", RandomForestRegressor(n_estimators=100, random_state=seed, n_jobs=1))])
    model.fit(matrix(train_rows), [row.target for row in train_rows])
    def evaluate(rows: list[Sample]) -> dict:
        actual = [float(row.target) for row in rows]
        predicted = [float(value) for value in model.predict(matrix(rows))]
        baseline = [float(row.features[0]) for row in rows]
        return {"model": regression(actual, predicted), "persistence": regression(actual, baseline),
                "actual": actual, "predicted": predicted}
    return {"model": model, "validation": evaluate(validation), "test": evaluate(test),
            "feature_names": list(FEATURE_NAMES)}
