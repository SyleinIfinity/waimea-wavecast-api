"""K-means over daily feature vectors; scaler is part of the bundle."""
from __future__ import annotations

from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from packages.ml.features import Sample, matrix


def train(rows: list[Sample], *, seed: int = 42, max_k: int = 8) -> dict:
    if len(rows) < 8:
        raise ValueError("at least eight daily samples required")
    best = None
    for k in range(2, min(max_k, len(rows) // 3) + 1):
        model = Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                          ("scaler", StandardScaler()), ("cluster", KMeans(n_clusters=k, random_state=seed, n_init=10))])
        labels = model.fit_predict(matrix(rows))
        transformed = model[:-1].transform(matrix(rows))
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(transformed, labels)
        if best is None or score > best["silhouette_score"]:
            best = {"model": model, "k": k, "silhouette_score": float(score),
                    "davies_bouldin_score": float(davies_bouldin_score(transformed, labels)),
                    "calinski_harabasz_score": float(calinski_harabasz_score(transformed, labels)),
                    "sample_count": len(rows), "labels": list(map(int, labels))}
    if best is None:
        raise ValueError("no valid K-means partition")
    return best
