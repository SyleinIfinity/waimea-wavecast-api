import pytest

from services.ingestion.scripts.bounded_backfill import selected


def test_archive_plan_requires_verified_listing_and_row_budget():
    entry = selected(2025, max_rows=18000)
    assert entry["rows"] == 17219
    assert entry["nonmissing_by_variable"]["WVHT"] == 17219
    with pytest.raises(ValueError, match="row budget"):
        selected(2025, max_rows=1000)
    with pytest.raises(ValueError, match="invalid year"):
        selected(2026, max_rows=18000)
