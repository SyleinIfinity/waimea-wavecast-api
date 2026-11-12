"""Filesystem GCS substitute for TL05 demos and tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

from packages.gcs.client import GCSObjectInfo


class LocalGCSClient:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, bucket: str, object_path: str) -> Path:
        path = (self.root / bucket / object_path).resolve()
        if not path.is_relative_to(self.root) or not bucket or ".." in Path(object_path).parts:
            raise ValueError("invalid object path")
        return path

    def upload_if_absent(self, bucket: str, object_path: str, data: bytes,
                         content_type: str = "application/octet-stream", metadata: dict | None = None) -> GCSObjectInfo:
        path = self._path(bucket, object_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as output:
                output.write(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("immutable object exists with different bytes")
        return GCSObjectInfo(bucket, object_path, f"gs://{bucket}/{object_path}", 1,
                             hashlib.sha256(data).hexdigest(), len(data))

    def download(self, bucket: str, object_path: str, generation: int | None = None) -> bytes:
        if generation not in (None, 1):
            raise ValueError("generation mismatch")
        return self._path(bucket, object_path).read_bytes()

    def object_info(self, bucket: str, object_path: str) -> GCSObjectInfo | None:
        path = self._path(bucket, object_path)
        if not path.exists():
            return None
        data = path.read_bytes()
        return GCSObjectInfo(bucket, object_path, f"gs://{bucket}/{object_path}", 1,
                             hashlib.sha256(data).hexdigest(), len(data))
