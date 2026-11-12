"""Immutable model bundles with generation and SHA-256 verification."""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import sklearn

from packages.gcs.client import GCSClientProtocol
from packages.gcs.paths import MODEL_PREFIX


def _uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://") or "/" not in uri[5:]:
        raise ValueError("invalid artifact URI")
    return uri[5:].split("/", 1)


class ModelStore:
    def __init__(self, client: GCSClientProtocol, bucket: str):
        self.client = client
        self.bucket = bucket

    def save(self, model: Any, *, task: str, version: str, manifest: dict) -> dict:
        if not task or not version or "/" in version or ".." in version:
            raise ValueError("invalid artifact version")
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        prefix = f"{MODEL_PREFIX}/{task}/{version}"
        model_info = self.client.upload_if_absent(self.bucket, prefix + "/model.joblib", data)
        if model_info.content_sha256 != digest:
            raise ValueError("artifact hash mismatch")
        if hashlib.sha256(self.client.download(self.bucket, prefix + "/model.joblib", model_info.generation)).hexdigest() != digest:
            raise ValueError("stored artifact hash mismatch")
        manifest_bytes = json.dumps({**manifest, "artifact_sha256": digest}, sort_keys=True, default=str).encode("utf-8")
        manifest_info = self.client.upload_if_absent(self.bucket, prefix + "/manifest.json", manifest_bytes, "application/json")
        if hashlib.sha256(self.client.download(self.bucket, prefix + "/manifest.json", manifest_info.generation)).hexdigest() != hashlib.sha256(manifest_bytes).hexdigest():
            raise ValueError("stored manifest hash mismatch")
        return {"artifact_gcs_uri": model_info.gcs_uri, "artifact_generation": str(model_info.generation),
                "artifact_sha256": digest, "artifact_size_bytes": len(data), "manifest_gcs_uri": manifest_info.gcs_uri,
                "manifest_generation": str(manifest_info.generation),
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()}

    def load(self, metadata: dict) -> tuple[Any, dict]:
        bucket, path = _uri(metadata["artifact_gcs_uri"])
        if bucket != self.bucket or not path.startswith(f"{MODEL_PREFIX}/"):
            raise ValueError("untrusted model location")
        generation = metadata.get("artifact_generation")
        digest = metadata.get("artifact_sha256")
        if not generation or not digest:
            raise ValueError("artifact generation and SHA-256 required")
        data = self.client.download(bucket, path, int(generation))
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("artifact hash mismatch")
        manifest_path = str(Path(path).parent).replace("\\", "/") + "/manifest.json"
        params = metadata.get("hyperparameters") or {}
        manifest_generation = params.get("manifest_generation")
        manifest_bytes = self.client.download(bucket, manifest_path, int(manifest_generation) if manifest_generation else None)
        manifest_sha = params.get("manifest_sha256")
        if manifest_sha and hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha:
            raise ValueError("manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        if manifest.get("artifact_sha256") != digest:
            raise ValueError("manifest hash mismatch")
        if manifest.get("python_version") and manifest["python_version"] != f"{sys.version_info.major}.{sys.version_info.minor}":
            raise ValueError("Python version incompatible with model")
        if manifest.get("sklearn_version") and manifest["sklearn_version"] != sklearn.__version__:
            raise ValueError("scikit-learn version incompatible with model")
        # joblib may execute code; only the configured private bucket is trusted.
        return joblib.load(io.BytesIO(data)), manifest
