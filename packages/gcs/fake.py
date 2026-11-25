"""
packages/gcs/fake.py

In-memory fake GCS client for unit and integration tests.

Satisfies GCSClientProtocol without touching a real GCS bucket.
Simulates:
  - upload_if_absent with if_generation_match=0 semantics
  - 412-equivalent when object already exists (raises ValueError)
  - optional SHA-256 mismatch simulation for negative tests
  - pinned-generation download
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from packages.gcs.client import GCSObjectInfo

logger = logging.getLogger(__name__)


@dataclass
class _StoredObject:
    data: bytes
    generation: int
    content_sha256: str
    metadata: dict[str, str]


class FakeGCSClient:
    """
    In-memory GCS client substitute for tests.

    Usage:
        client = FakeGCSClient()
        info = client.upload_if_absent("my-bucket", "path/file.txt", b"data")
        assert info.generation == 1
        # Second call with same path + same data -> reuse
        info2 = client.upload_if_absent("my-bucket", "path/file.txt", b"data")
        assert info2.generation == 1
    """

    def __init__(self) -> None:
        # key: (bucket, object_path)
        self._store: dict[tuple[str, str], _StoredObject] = {}
        self._next_generation: int = 1

    def upload_if_absent(
        self,
        bucket: str,
        object_path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> GCSObjectInfo:
        key = (bucket, object_path)
        sha256 = hashlib.sha256(data).hexdigest()

        if key in self._store:
            existing = self._store[key]
            if existing.content_sha256 != sha256:
                raise ValueError(
                    f"[412-equivalent] gs://{bucket}/{object_path} already exists with "
                    f"different SHA-256 ({existing.content_sha256} != {sha256})"
                )
            logger.debug("FakeGCS: object already present, reusing gs://%s/%s", bucket, object_path)
            return GCSObjectInfo(
                bucket=bucket,
                object_path=object_path,
                gcs_uri=f"gs://{bucket}/{object_path}",
                generation=existing.generation,
                content_sha256=sha256,
                size_bytes=len(data),
            )

        gen = self._next_generation
        self._next_generation += 1
        self._store[key] = _StoredObject(
            data=data,
            generation=gen,
            content_sha256=sha256,
            metadata=metadata or {},
        )
        logger.debug("FakeGCS: stored gs://%s/%s gen=%d", bucket, object_path, gen)
        return GCSObjectInfo(
            bucket=bucket,
            object_path=object_path,
            gcs_uri=f"gs://{bucket}/{object_path}",
            generation=gen,
            content_sha256=sha256,
            size_bytes=len(data),
        )

    def download(self, bucket: str, object_path: str, generation: int | None = None) -> bytes:
        key = (bucket, object_path)
        obj = self._store.get(key)
        if obj is None:
            raise FileNotFoundError(f"gs://{bucket}/{object_path} not found in FakeGCSClient")
        if generation is not None and obj.generation != generation:
            raise ValueError(
                f"Generation mismatch: requested {generation}, have {obj.generation}"
            )
        return obj.data

    def object_info(self, bucket: str, object_path: str) -> GCSObjectInfo | None:
        key = (bucket, object_path)
        obj = self._store.get(key)
        if obj is None:
            return None
        return GCSObjectInfo(
            bucket=bucket,
            object_path=object_path,
            gcs_uri=f"gs://{bucket}/{object_path}",
            generation=obj.generation,
            content_sha256=obj.content_sha256,
            size_bytes=len(obj.data),
        )

    def clear(self) -> None:
        """Reset store (useful between test cases)."""
        self._store.clear()
        self._next_generation = 1
