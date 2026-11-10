"""
tests/unit/test_priority.py — Source priority arbitration tests
tests/unit/test_hash.py     — Hash stability tests
tests/unit/test_gcs_fake.py — FakeGCSClient 412-equivalent tests
"""
from __future__ import annotations

import pytest


# =================== PRIORITY ===================

class TestSourcePriority:
    def test_annual_has_highest_priority(self):
        from packages.noaa.priority import source_priority
        assert source_priority("historical_annual") > source_priority("historical_monthly")
        assert source_priority("historical_monthly") > source_priority("realtime")

    def test_realtime_does_not_replace_annual_different_hash(self):
        from packages.noaa.priority import should_replace
        assert not should_replace("historical_annual", "realtime", "aaa", "bbb")

    def test_annual_replaces_realtime_different_hash(self):
        from packages.noaa.priority import should_replace
        assert should_replace("realtime", "historical_annual", "aaa", "bbb")

    def test_same_hash_never_replaces(self):
        from packages.noaa.priority import should_replace
        assert not should_replace("historical_annual", "historical_annual", "abc", "abc")
        assert not should_replace("realtime", "historical_annual", "xyz", "xyz")

    def test_monthly_replaces_realtime_different_hash(self):
        from packages.noaa.priority import should_replace
        assert should_replace("realtime", "historical_monthly", "111", "222")

    def test_realtime_does_not_replace_monthly_different_hash(self):
        from packages.noaa.priority import should_replace
        assert not should_replace("historical_monthly", "realtime", "111", "222")

    def test_unknown_source_raises(self):
        from packages.noaa.priority import source_priority
        with pytest.raises(ValueError):
            source_priority("unknown_feed")


# =================== HASH ===================

class TestHash:
    def test_file_sha256_stable(self):
        from packages.noaa.hash import file_sha256
        data = b"hello noaa"
        h1 = file_sha256(data)
        h2 = file_sha256(data)
        assert h1 == h2
        assert len(h1) == 64

    def test_measurement_sha256_stable(self):
        from datetime import datetime, timezone
        from packages.noaa.hash import measurement_sha256
        ts = datetime(2023, 6, 1, 12, 0, tzinfo=timezone.utc)
        values = {"WVHT": 1.5, "DPD": 10.0, "PRES": 1013.0}
        h1 = measurement_sha256("51201", ts, values)
        h2 = measurement_sha256("51201", ts, values)
        assert h1 == h2

    def test_different_values_different_hash(self):
        from datetime import datetime, timezone
        from packages.noaa.hash import measurement_sha256
        ts = datetime(2023, 6, 1, 12, 0, tzinfo=timezone.utc)
        h1 = measurement_sha256("51201", ts, {"WVHT": 1.5})
        h2 = measurement_sha256("51201", ts, {"WVHT": 2.0})
        assert h1 != h2

    def test_source_kind_excluded_from_hash(self):
        """Same measurement from annual and realtime must produce same hash."""
        from datetime import datetime, timezone
        from packages.noaa.hash import measurement_sha256
        ts = datetime(2023, 6, 1, 12, 0, tzinfo=timezone.utc)
        values = {"WVHT": 1.5, "DPD": 10.0}
        h_annual = measurement_sha256("51201", ts, values)
        h_realtime = measurement_sha256("51201", ts, values)
        assert h_annual == h_realtime   # source_kind is NOT part of hash

    def test_row_sha256_stable(self):
        from packages.noaa.hash import row_sha256
        line = "2023 06 01 12 00  120  3.5 4.5 1.5   8  7.0 100 1015 22.0 24.0 18.0   8  0.1  0.3"
        assert row_sha256(line) == row_sha256(line)


# =================== GCS FAKE ===================

class TestFakeGCSClient:
    def test_upload_creates_object(self):
        from packages.gcs.fake import FakeGCSClient
        client = FakeGCSClient()
        info = client.upload_if_absent("bucket", "path/file.txt", b"data")
        assert info.generation == 1
        assert len(info.content_sha256) == 64

    def test_same_data_same_path_reuses_object(self):
        from packages.gcs.fake import FakeGCSClient
        client = FakeGCSClient()
        i1 = client.upload_if_absent("bucket", "path/file.txt", b"data")
        i2 = client.upload_if_absent("bucket", "path/file.txt", b"data")
        assert i1.generation == i2.generation == 1

    def test_different_data_same_path_raises_412_equivalent(self):
        from packages.gcs.fake import FakeGCSClient
        client = FakeGCSClient()
        client.upload_if_absent("bucket", "path/file.txt", b"original")
        with pytest.raises(ValueError, match="412-equivalent"):
            client.upload_if_absent("bucket", "path/file.txt", b"different")

    def test_download_returns_data(self):
        from packages.gcs.fake import FakeGCSClient
        client = FakeGCSClient()
        client.upload_if_absent("bucket", "path/file.txt", b"hello")
        assert client.download("bucket", "path/file.txt") == b"hello"

    def test_download_nonexistent_raises(self):
        from packages.gcs.fake import FakeGCSClient
        client = FakeGCSClient()
        with pytest.raises(FileNotFoundError):
            client.download("bucket", "nope.txt")

    def test_object_info_returns_none_for_missing(self):
        from packages.gcs.fake import FakeGCSClient
        client = FakeGCSClient()
        assert client.object_info("bucket", "missing.txt") is None
