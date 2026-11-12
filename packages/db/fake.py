"""
packages/db/fake.py

FakeDatabaseGateway — in-memory implementation of DatabaseGateway for tests.

Satisfies the full DatabaseGateway protocol without a running database.
Implements dedup via hash keys and source priority arbitration.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from packages.db.gateway import (
    DailyFeaturesDTO,
    DatabaseGateway,
    JobStatusDTO,
    RawObservationDTO,
    SilverObservationDTO,
    SourceFileDTO,
)
from packages.noaa.priority import should_replace

logger = logging.getLogger(__name__)


class FakeDatabaseGateway:
    """Thread-unsafe in-memory database gateway for unit/integration tests."""

    def __init__(self) -> None:
        self._next_file_id: int = 1
        # file_id -> SourceFileDTO
        self._source_files: dict[int, SourceFileDTO] = {}
        # (station_id, content_sha256) -> file_id  (dedup)
        self._file_hash_index: dict[tuple[str, str], int] = {}

        # (file_id, row_sha256) -> RawObservationDTO  (dedup)
        self._raw_obs: dict[tuple[int, str], RawObservationDTO] = {}

        # (station_id, observed_at) -> SilverObservationDTO  (canonical)
        self._silver: dict[tuple[str, datetime], SilverObservationDTO] = {}

        # (station_id, date_hst) -> DailyFeaturesDTO
        self._daily: dict[tuple[str, date], DailyFeaturesDTO] = {}

        # job_id -> JobStatusDTO
        self._jobs: dict[str, JobStatusDTO] = {}

        self.counters: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ #
    # Source files                                                         #
    # ------------------------------------------------------------------ #

    def register_source_file(self, dto: SourceFileDTO) -> int:
        dedup_key = (dto.station_id, dto.content_sha256)
        if dedup_key in self._file_hash_index:
            existing_id = self._file_hash_index[dedup_key]
            logger.debug("FakeDB: source file already registered, file_id=%d", existing_id)
            return existing_id
        fid = self._next_file_id
        self._next_file_id += 1
        self._source_files[fid] = dto
        self._file_hash_index[dedup_key] = fid
        self.counters["files_registered"] += 1
        return fid

    # ------------------------------------------------------------------ #
    # Raw observations                                                     #
    # ------------------------------------------------------------------ #

    def upsert_raw_batch(self, rows: list[RawObservationDTO]) -> dict[str, int]:
        inserted = duplicate = 0
        for row in rows:
            key = (row.file_id, row.row_sha256)
            if key in self._raw_obs:
                duplicate += 1
            else:
                self._raw_obs[key] = row
                inserted += 1
        self.counters["raw_inserted"] += inserted
        self.counters["raw_duplicate"] += duplicate
        return {"inserted": inserted, "duplicate": duplicate}

    # ------------------------------------------------------------------ #
    # Silver observations                                                  #
    # ------------------------------------------------------------------ #

    def upsert_silver_batch(self, rows: list[SilverObservationDTO]) -> dict[str, int]:
        inserted = revised = duplicate = 0
        for row in rows:
            key = (row.station_id, row.observed_at)
            existing = self._silver.get(key)

            if existing is None:
                self._silver[key] = row
                inserted += 1
            elif existing.measurement_sha256 == row.measurement_sha256:
                duplicate += 1
            elif should_replace(
                existing.source_kind,
                row.source_kind,
                existing.measurement_sha256,
                row.measurement_sha256,
            ):
                self._silver[key] = row
                revised += 1
            else:
                duplicate += 1

        self.counters["silver_inserted"] += inserted
        self.counters["silver_revised"] += revised
        self.counters["silver_duplicate"] += duplicate
        return {"inserted": inserted, "revised": revised, "duplicate": duplicate}

    # ------------------------------------------------------------------ #
    # Daily features                                                       #
    # ------------------------------------------------------------------ #

    def upsert_daily_features(self, rows: list[DailyFeaturesDTO]) -> dict[str, int]:
        upserted = 0
        for row in rows:
            key = (row.station_id, row.date_hst)
            self._daily[key] = row
            upserted += 1
        self.counters["daily_upserted"] += upserted
        return {"upserted": upserted}

    # ------------------------------------------------------------------ #
    # Reads                                                                #
    # ------------------------------------------------------------------ #

    def get_observations(
        self,
        station_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[SilverObservationDTO]:
        return [
            obs for (sid, ts), obs in self._silver.items()
            if sid == station_id and start_at <= ts < end_at
        ]

    def get_daily_features(
        self,
        station_id: str,
        start_hst: date,
        end_hst: date,
    ) -> list[DailyFeaturesDTO]:
        return [
            feat for (sid, d), feat in self._daily.items()
            if sid == station_id and start_hst <= d <= end_hst
        ]

    # ------------------------------------------------------------------ #
    # Job tracking                                                         #
    # ------------------------------------------------------------------ #

    def save_job_status(self, dto: JobStatusDTO) -> None:
        self._jobs[dto.job_id] = dto

    def get_job_status(self, job_id: str) -> JobStatusDTO | None:
        return self._jobs.get(job_id)

    # ------------------------------------------------------------------ #
    # Test helpers                                                         #
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """Reset all in-memory state between test cases."""
        self._source_files.clear()
        self._file_hash_index.clear()
        self._raw_obs.clear()
        self._silver.clear()
        self._daily.clear()
        self._jobs.clear()
        self.counters.clear()
