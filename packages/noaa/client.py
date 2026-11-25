"""
packages/noaa/client.py

HTTP client for downloading NOAA NDBC Standard Meteorological files.

Features:
- Configurable timeout and retry with exponential backoff + jitter
- Treats HTTP 404 as SourceUnavailableError (non-retriable) — NOAA simply
  hasn'"'"'t published that period yet
- Treats 429, 502, 503, 504 as retriable
- Caps response size to prevent runaway downloads
- Returns raw bytes; caller decides how to parse / store

Usage:
    client = NOAAClient()
    raw = client.download(annual_url(2023))
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field

import requests

from packages.common.errors import SourceUnavailableError

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES: int = 50 * 1024 * 1024   # 50 MB safety cap
_RETRIABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass
class NOAAClient:
    """
    Thin HTTP client for NOAA NDBC file downloads.

    Args:
        timeout_s:       Per-request connect+read timeout in seconds.
        max_retries:     Number of retry attempts after the first failure.
        backoff_base_s:  Base delay (seconds) for exponential backoff.
        max_backoff_s:   Maximum retry delay cap in seconds.
        user_agent:      User-Agent header string.
    """
    timeout_s: float = 30.0
    max_retries: int = 3
    backoff_base_s: float = 2.0
    max_backoff_s: float = 60.0
    user_agent: str = "WaimeaWaveCast/1.0 (educational; contact: team@example.com)"
    _session: requests.Session = field(default_factory=requests.Session, init=False, repr=False)
    _cache: dict[str, tuple[str | None, str | None, bytes]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._session.headers["User-Agent"] = self.user_agent

    def download(self, url: str) -> bytes:
        """
        Download *url* and return raw bytes.

        Raises:
            SourceUnavailableError: 404 or other non-retriable HTTP error.
            requests.RequestException: After all retries exhausted.
        """
        last_exc: Exception | None = None
        retry_after = 0.0

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                delay = max(retry_after, min(
                    self.backoff_base_s * (2 ** (attempt - 1)) + random.uniform(0, 1),
                    self.max_backoff_s,
                ))
                logger.info("Retry %d/%d for %s after %.1fs", attempt, self.max_retries, url, delay)
                time.sleep(delay)

            try:
                cached = self._cache.get(url)
                headers = {}
                if cached and cached[0]:
                    headers["If-None-Match"] = cached[0]
                elif cached and cached[1]:
                    headers["If-Modified-Since"] = cached[1]
                resp = self._session.get(url, timeout=self.timeout_s, stream=True, headers=headers)
            except requests.RequestException as exc:
                logger.warning("HTTP error on attempt %d for %s: %s", attempt + 1, url, exc)
                last_exc = exc
                continue

            if resp.status_code == 304 and cached is not None:
                resp.close()
                return cached[2]

            if resp.status_code == 404:
                resp.close()
                raise SourceUnavailableError(url, http_status=404)

            if resp.status_code in _RETRIABLE_STATUSES:
                logger.warning(
                    "HTTP %d on attempt %d for %s — will retry",
                    resp.status_code, attempt + 1, url,
                )
                last_exc = requests.HTTPError(response=resp)
                retry_after = 0.0
                if resp.status_code == 429 and resp.headers.get("Retry-After"):
                    value = resp.headers["Retry-After"]
                    try:
                        retry_after = float(value)
                    except ValueError:
                        try:
                            retry_after = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                        except (TypeError, ValueError):
                            retry_after = 0.0
                    retry_after = min(self.max_backoff_s, max(0.0, retry_after))
                resp.close()
                continue

            if not resp.ok:
                # 4xx other than 404 — non-retriable, fail immediately
                raise SourceUnavailableError(url, http_status=resp.status_code,
                                             message=f"HTTP {resp.status_code}")

            # Stream response to enforce size cap
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise ValueError(
                        f"Response from {url} exceeds {_MAX_RESPONSE_BYTES} bytes — aborting"
                    )
                chunks.append(chunk)

            logger.info("Downloaded %d bytes from %s", total, url)
            payload = b"".join(chunks)
            if resp.headers.get("ETag") or resp.headers.get("Last-Modified"):
                self._cache[url] = (resp.headers.get("ETag"), resp.headers.get("Last-Modified"), payload)
            resp.close()
            return payload

        raise last_exc or RuntimeError(f"Failed to download {url} after {self.max_retries + 1} attempts")
