"""Repository correction counters must not treat identical measurements as revisions."""
from datetime import datetime, timezone

from services.database_api.app.repositories.silver import upsert_silver_batch
from services.database_api.app.schemas.observations import SilverBatch


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append(str(query))
        if isinstance(query, str) and query.startswith("SELECT source_kind"):
            return Result({"source_kind": "realtime", "measurement_sha256": "a" * 64,
                           "source_file_id": 1, "quality_status": "unverified",
                           "quality_flags": {}, "missing_fields": []})
        return Result()


def test_same_feed_and_measurement_new_source_file_is_duplicate():
    row = {"station_id": "51201", "observed_at": datetime(2023, 6, 1, tzinfo=timezone.utc),
           "source_kind": "realtime", "source_dataset": "noaa", "source_file_id": 2,
           "raw_id": 9, "measurement_sha256": "a" * 64}
    conn = Connection()
    result = upsert_silver_batch(conn, SilverBatch(rows=[row]))
    assert result["duplicate"] == 1 and result["revised"] == result["updated"] == 0
    assert not any(query.startswith("UPDATE") for query in conn.queries)
