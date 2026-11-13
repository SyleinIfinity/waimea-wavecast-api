from datetime import date, timedelta

from packages.ml.features import Sample
from training.tl08b_walkforward import evaluate_folds


def test_walkforward_embargoes_future_labels(monkeypatch):
    samples = [Sample(date(2020, 1, 1) + timedelta(days=i),
                      date(2020, 1, 1) + timedelta(days=i + 30),
                      (float(i),), float(i + 1)) for i in range(400)]

    def fake_fit(train, validation, test):
        assert train[-1].target_date_hst < validation[0].anchor_date_hst
        assert validation[-1].target_date_hst < test[0].anchor_date_hst
        actual = [float(row.target) for row in test]
        return {"test": {"actual": actual, "model": {"mae": 1.0},
                         "persistence": {"mae": 1.0}}}

    monkeypatch.setattr("training.tl08b_walkforward.fit_regression", fake_fit)
    result = evaluate_folds(samples, horizon_days=30)
    assert len(result) == 3
    assert all(row["test_count"] >= 30 for row in result)
