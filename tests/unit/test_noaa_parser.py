"""
tests/unit/test_noaa_parser.py

Unit tests for packages/noaa/parser.py

Covers:
  - Old #YY header (2-digit year)
  - New #YYYY header (4-digit year)
  - BAR alias for PRES
  - Corrupt/short row
  - File with missing PTDY/TIDE columns
  - Gzip-compressed content
"""
from __future__ import annotations

import gzip
from datetime import datetime, timezone

import pytest

from packages.noaa.parser import CANONICAL_FIELDS, detect_header, parse_stdmet


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HEADER_OLD = b"#YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD BAR ATMP WTMP DEWP VIS TIDE\n"
UNITS_OLD  = b"#yr mo dy hr mn degT m/s m/s  m   sec sec degT  hPa degC degC degC  nmi  ft\n"
DATA_OLD   = b"05 01 15 12 00  310  5.0 7.0 1.5  10  8.0 290 1013 24.0 25.0 20.0  10  0.5\n"

HEADER_NEW = b"#YYYY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE\n"
UNITS_NEW  = b"#yr   mo dy hr mn degT m/s m/s  m   sec sec degT  hPa degC degC degC  nmi hPa  ft\n"
DATA_NEW   = b"2023 06 01 00 00  120  3.5 4.5 1.2   8  7.0 100 1015 22.0 24.0 18.0   8  0.1  0.3\n"

MM_ROW_NEW = b"2023 06 02 06 00   MM   MM  MM  MM  MM  MM  MM   MM   MM   MM   MM  MM   MM   MM\n"

SHORT_ROW  = b"2023 06 03 12 00  180  2.0\n"   # Too few columns


def _make_file(*lines: bytes) -> bytes:
    return b"".join(lines)


# ---------------------------------------------------------------------------
# detect_header
# ---------------------------------------------------------------------------

class TestDetectHeader:
    def test_old_header_detected(self):
        lines = [l.decode() for l in [HEADER_OLD, UNITS_OLD, DATA_OLD]]
        cols, idx = detect_header(lines)
        assert idx == 0
        assert "YY" in cols
        assert "PRES" in cols   # BAR alias resolved

    def test_new_header_detected(self):
        lines = [l.decode() for l in [HEADER_NEW, UNITS_NEW, DATA_NEW]]
        cols, idx = detect_header(lines)
        assert "YYYY" in cols
        assert idx == 0

    def test_no_header_raises(self):
        from packages.common.errors import ParseError
        with pytest.raises(ParseError):
            detect_header(["2023 01 01 00 00 1.0"])


# ---------------------------------------------------------------------------
# parse_stdmet — old 2-digit year / BAR alias
# ---------------------------------------------------------------------------

class TestParseOldFormat:
    def test_parses_2digit_year(self):
        raw = _make_file(HEADER_OLD, UNITS_OLD, DATA_OLD)
        result = parse_stdmet(raw, "test_old.txt")
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.observed_at == datetime(2005, 1, 15, 12, 0, tzinfo=timezone.utc)

    def test_bar_mapped_to_pres(self):
        raw = _make_file(HEADER_OLD, UNITS_OLD, DATA_OLD)
        result = parse_stdmet(raw, "test_old.txt")
        assert "PRES" in result.rows[0].raw_values

    def test_no_ptdy_in_old_header(self):
        raw = _make_file(HEADER_OLD, UNITS_OLD, DATA_OLD)
        result = parse_stdmet(raw)
        assert "PTDY" not in result.rows[0].raw_values


# ---------------------------------------------------------------------------
# parse_stdmet — new 4-digit year
# ---------------------------------------------------------------------------

class TestParseNewFormat:
    def test_parses_4digit_year(self):
        raw = _make_file(HEADER_NEW, UNITS_NEW, DATA_NEW)
        result = parse_stdmet(raw, "test_new.txt")
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.observed_at == datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc)
        assert row.raw_values["WVHT"] == "1.2"

    def test_all_mm_row_preserved(self):
        raw = _make_file(HEADER_NEW, UNITS_NEW, MM_ROW_NEW)
        result = parse_stdmet(raw)
        assert len(result.rows) == 1
        # Raw values still contain "MM" strings (normalize.py handles conversion)
        assert result.rows[0].raw_values["WVHT"] == "MM"

    def test_short_row_counted_as_error(self):
        raw = _make_file(HEADER_NEW, UNITS_NEW, SHORT_ROW)
        result = parse_stdmet(raw, "short.txt")
        assert result.parse_error_count == 1
        assert len(result.rows) == 0

    def test_multiple_rows_with_errors(self):
        raw = _make_file(HEADER_NEW, UNITS_NEW, DATA_NEW, SHORT_ROW, MM_ROW_NEW)
        result = parse_stdmet(raw)
        assert result.parse_error_count == 1
        assert len(result.rows) == 2


# ---------------------------------------------------------------------------
# parse_stdmet — gzip
# ---------------------------------------------------------------------------

class TestParseGzip:
    def test_gzip_file_decompressed(self):
        raw_text = HEADER_NEW + UNITS_NEW + DATA_NEW
        compressed = gzip.compress(raw_text)
        result = parse_stdmet(compressed, "test.txt.gz")
        assert len(result.rows) == 1
        assert result.rows[0].observed_at.year == 2023

    def test_bad_gzip_raises_parse_error(self):
        from packages.common.errors import ParseError
        with pytest.raises(ParseError):
            parse_stdmet(b"\x1f\x8b" + b"\x00" * 20, "corrupt.txt.gz")


# ---------------------------------------------------------------------------
# parse_stdmet — columns_in_source
# ---------------------------------------------------------------------------

class TestColumnsInSource:
    def test_new_file_has_ptdy_tide(self):
        raw = _make_file(HEADER_NEW, UNITS_NEW, DATA_NEW)
        result = parse_stdmet(raw)
        assert "PTDY" in result.columns_in_source
        assert "TIDE" in result.columns_in_source

    def test_old_file_missing_ptdy(self):
        raw = _make_file(HEADER_OLD, UNITS_OLD, DATA_OLD)
        result = parse_stdmet(raw)
        assert "PTDY" not in result.columns_in_source


def test_early_annual_archive_without_hash_header():
    payload = ("YYYY MM DD hh WD WSPD GST WVHT DPD APD MWD BAR ATMP WTMP DEWP VIS TIDE\n"
               "2004 09 08 16 999 99.0 99.0 0.90 8.00 99.00 999 9999.0 999.0 27.2 999.0 99.0 99.00\n")
    result = parse_stdmet(payload.encode(), "51201h2004.txt.gz")
    assert len(result.rows) == 1
    assert result.rows[0].raw_values["WVHT"] == "0.90"
    assert "WDIR" in result.columns_in_source
