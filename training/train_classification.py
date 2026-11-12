"""Train-only quantile label scheme and held-out classification metrics."""
from __future__ import annotations

from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from packages.ml.analytics import _quantile
from packages.ml.features import Sample, matrix
from packages.ml.metrics import classification

LABELS = ["low", "medium", "high"]


def label(value: float, boundaries: tuple[float, float]) -> str:
    return LABELS[0] if value < boundaries[0] else LABELS[1] if value < boundaries[1] else LABELS[2]


def train(train_rows: list[Sample], validation: list[Sample], test: list[Sample], *, seed: int = 42) -> dict:
    if any(row.target is None for row in train_rows + validation + test):
        raise ValueError("supervised targets required")
    train_values = [float(row.target) for row in train_rows]
    boundaries = (_quantile(train_values, 1 / 3), _quantile(train_values, 2 / 3))
    if boundaries[0] == boundaries[1]:
        raise ValueError("training target has insufficient variation for three classes")
    scheme = {"scheme_name": "wave-height-terciles", "scheme_version": "v1", "target_metric": "WVHT",
              "label_rules": {"labels": LABELS, "upper_bound_low": boundaries[0], "upper_bound_medium": boundaries[1]},
              "rationale": "Terciles of training daily median WVHT; not a swimming safety rating"}
    model = Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                      ("classifier", RandomForestClassifier(n_estimators=100, random_state=seed, n_jobs=1))])
    model.fit(matrix(train_rows), [label(v, boundaries) for v in train_values])
    def evaluate(rows: list[Sample]) -> dict:
        actual = [label(float(row.target), boundaries) for row in rows]
        predicted = list(model.predict(matrix(rows)))
        return {**classification(actual, predicted, LABELS), "actual": actual, "predicted": predicted}
    return {"model": model, "scheme": scheme, "validation": evaluate(validation), "test": evaluate(test)}
