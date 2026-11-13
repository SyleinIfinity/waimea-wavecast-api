"""Bounded NOAA HTTP behaviour and archive URL contracts."""
from unittest.mock import Mock

import pytest
import requests

from packages.common.errors import SourceUnavailableError
from packages.noaa.client import NOAAClient
from packages.noaa.urls import monthly_url, realtime_url


def response(status: int, data: bytes = b"payload"):
    return Mock(status_code=status, ok=status < 400, headers={},
                iter_content=Mock(return_value=[data]))


def test_month_codes_for_final_quarter():
    assert monthly_url(2025, 10).endswith("/Oct/51201a2025.txt.gz")
    assert monthly_url(2025, 11).endswith("/Nov/51201b2025.txt.gz")
    assert monthly_url(2025, 12).endswith("/Dec/51201c2025.txt.gz")


def test_rate_limit_and_server_error_retry(monkeypatch):
    monkeypatch.setattr("packages.noaa.client.time.sleep", lambda _: None)
    client = NOAAClient(max_retries=2)
    get = Mock(side_effect=[response(429), response(503), response(200)])
    monkeypatch.setattr(client._session, "get", get)
    assert client.download(realtime_url()) == b"payload"
    assert get.call_count == 3


def test_404_does_not_retry(monkeypatch):
    client = NOAAClient(max_retries=3)
    get = Mock(return_value=response(404))
    monkeypatch.setattr(client._session, "get", get)
    with pytest.raises(SourceUnavailableError):
        client.download(realtime_url())
    assert get.call_count == 1


def test_timeout_stops_after_limit(monkeypatch):
    monkeypatch.setattr("packages.noaa.client.time.sleep", lambda _: None)
    client = NOAAClient(max_retries=1)
    get = Mock(side_effect=requests.Timeout("timed out"))
    monkeypatch.setattr(client._session, "get", get)
    with pytest.raises(requests.Timeout):
        client.download(realtime_url())
    assert get.call_count == 2


def test_conditional_request_reuses_cached_payload(monkeypatch):
    client = NOAAClient(max_retries=0)
    first = response(200)
    first.headers = {"ETag": '"abc"'}
    second = response(304)
    get = Mock(side_effect=[first, second])
    monkeypatch.setattr(client._session, "get", get)
    assert client.download(realtime_url()) == b"payload"
    assert client.download(realtime_url()) == b"payload"
    assert get.call_args.kwargs["headers"] == {"If-None-Match": '"abc"'}
