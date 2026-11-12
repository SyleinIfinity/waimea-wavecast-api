"""
packages/gcs/client.py

GCS client wrapper for NOAA raw file storage.

Key behaviours:
  - upload_if_absent: uses `if_generation_match=0` so an object is only
    created if it does NOT exist — prevents overwriting existing raw files.
  - If a 412 PreconditionFailed occurs, checks whether the existing object
    has the same SHA-256; if yes, returns the existing object info (idempotent).
  - download_pinned: downloads a specific object generation to prevent
    race conditions with concurrent uploads.

The real GCS client (`google.cloud.storage`) is used in production.
Tests inject a fake adapter conforming to the same interface.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class GCSObjectInfo:
    """Metadata returned after a successful upload or existence check."""
    bucket: str
    object_path: str
    gcs_uri: str
    generation: int
    content_sha256: str
    size_bytes: int


@runtime_checkable
class GCSClientProtocol(Protocol):
    """Interface that both real and fake GCS clients must satisfy."""

    def upload_if_absent(
        self,
        bucket: str,
        object_path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> GCSObjectInfo: ...

    def download(self, bucket: str, object_path: str, generation: int | None = None) -> bytes: ...

    def object_info(self, bucket: str, object_path: str) -> GCSObjectInfo | None: ...


class RealGCSClient:
    """
    Production GCS client backed by google-cloud-storage.

    Uses Application Default Credentials. On Compute Engine, these come from
    the service account attached to the VM; no key file is needed.
    """

    def __init__(self) -> None:
        from google.cloud import storage  # type: ignore[import-untyped]
        self._client = storage.Client()

    def bucket_accessible(self, bucket: str) -> bool:
        next(iter(self._client.list_blobs(bucket, max_results=1)), None)
        return True

    def upload_if_absent(
        self,
        bucket: str,
        object_path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> GCSObjectInfo:
        from google.api_core.exceptions import PreconditionFailed  # type: ignore[import-untyped]

        sha256 = hashlib.sha256(data).hexdigest()
        bucket_obj = self._client.bucket(bucket)
        blob = bucket_obj.blob(object_path)
        blob.metadata = {**(metadata or {}), "content_sha256": sha256}

        # Attempt to create only if the object is absent
        try:
            blob.upload_from_string(
                data,
                content_type=content_type,
                if_generation_match=0,
            )
            blob.reload()
            logger.info("Uploaded gs://%s/%s (generation=%s)", bucket, object_path, blob.generation)
        except PreconditionFailed:
            # Object already exists — verify it has the same content
            blob.reload()
            existing_sha = (blob.metadata or {}).get("content_sha256")
            if existing_sha is None:
                existing_sha = hashlib.sha256(blob.download_as_bytes()).hexdigest()
            if existing_sha != sha256:
                raise ValueError(
                    f"gs://{bucket}/{object_path} already exists with DIFFERENT SHA-256 "
                    f"({existing_sha} != {sha256}); refusing to proceed"
                )
            logger.info(
                "Object gs://%s/%s already present (generation=%s) — reusing",
                bucket, object_path, blob.generation,
            )

        return GCSObjectInfo(
            bucket=bucket,
            object_path=object_path,
            gcs_uri=f"gs://{bucket}/{object_path}",
            generation=blob.generation,
            content_sha256=sha256,
            size_bytes=len(data),
        )

    def download(self, bucket: str, object_path: str, generation: int | None = None) -> bytes:
        bucket_obj = self._client.bucket(bucket)
        blob = bucket_obj.blob(object_path, generation=generation)
        return blob.download_as_bytes()

    def object_info(self, bucket: str, object_path: str) -> GCSObjectInfo | None:
        from google.api_core.exceptions import NotFound  # type: ignore[import-untyped]

        bucket_obj = self._client.bucket(bucket)
        blob = bucket_obj.blob(object_path)
        try:
            blob.reload()
        except NotFound:
            return None
        sha = (blob.metadata or {}).get("content_sha256", "")
        return GCSObjectInfo(
            bucket=bucket,
            object_path=object_path,
            gcs_uri=f"gs://{bucket}/{object_path}",
            generation=blob.generation,
            content_sha256=sha,
            size_bytes=blob.size or 0,
        )
