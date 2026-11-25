"""
packages/common/errors.py
Domain error hierarchy shared across all services.
"""
from __future__ import annotations


class WaimeaError(Exception):
    """Base class for all Waimea WaveCast errors."""


class DomainError(WaimeaError):
    """A business-rule or data-integrity violation."""


class SourceUnavailableError(WaimeaError):
    """The upstream NOAA source returned a non-retriable error (e.g. 404)."""

    def __init__(self, url: str, http_status: int | None = None, message: str = "") -> None:
        self.url = url
        self.http_status = http_status
        super().__init__(message or f"Source unavailable: {url} (HTTP {http_status})")


class InvalidMeasurementError(DomainError):
    """A parsed measurement value is physically impossible."""

    def __init__(self, field: str, value: object, reason: str = "") -> None:
        self.field = field
        self.value = value
        super().__init__(f"Invalid measurement {field}={value!r}: {reason}")


class DependencyUnavailableError(WaimeaError):
    """A downstream dependency (DB, GCS) is not reachable."""

    def __init__(self, service: str, reason: str = "") -> None:
        self.service = service
        super().__init__(f"Dependency unavailable: {service} — {reason}")


class ChecksumMismatchError(WaimeaError):
    """GCS object checksum does not match the expected SHA-256."""

    def __init__(self, uri: str, expected: str, actual: str) -> None:
        self.uri = uri
        super().__init__(f"SHA-256 mismatch for {uri}: expected {expected}, got {actual}")


class ParseError(DomainError):
    """NOAA source file could not be fully parsed."""

    def __init__(self, filename: str, reason: str, line: int | None = None) -> None:
        self.filename = filename
        self.line = line
        super().__init__(f"Parse error in {filename}" + (f" line {line}" if line else "") + f": {reason}")
