"""Real-data majority and persistence baselines for the pinned TL08-B split."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date

from packages.ml.analytics import _quantile
from packages.ml.features import build_samples, chronological_split
from packages.ml.gateway import HttpMLGateway
from packages.ml.metrics import classification
from packages.ml.worker import WorkerSettings
from training.train_classification import LABELS, label


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-hst", type=date.fromisoformat, required=True)
    parser.add_argument("--end-hst", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = WorkerSettings()
    gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    rows = gateway.get_daily_features(args.start_hst, args.end_hst)
    rows = [row for row in rows if (row.get("valid_wave_count") or 0) > 0
            and (row.get("coverage_ratio") or 0) >= .9
            and not any(row.get(k) for k in ("suspect_count", "rejected_count", "unverified_count", "imputed_count"))]
    train, validation, test = chronological_split(build_samples(rows))
    thresholds = (_quantile([float(x.target) for x in train], 1/3),
                  _quantile([float(x.target) for x in train], 2/3))
    train_labels = [label(float(x.target), thresholds) for x in train]
    majority = Counter(train_labels).most_common(1)[0][0]
    actual = [label(float(x.target), thresholds) for x in test]
    persistence = [label(float(x.features[0]), thresholds) for x in test]
    result = {"train_count": len(train), "validation_count": len(validation), "test_count": len(test),
              "train_only_thresholds_m": thresholds, "train_majority_label": majority,
              "test_class_counts": dict(Counter(actual)),
              "majority": classification(actual, [majority] * len(test), LABELS),
              "persistence": classification(actual, persistence, LABELS)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
