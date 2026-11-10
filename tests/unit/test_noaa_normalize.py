"""
tests/unit/test_noaa_normalize.py

Unit tests for packages/noaa/normalize.py

Covers:
  - Universal "MM" sentinel -> None for all fields
  - Per-field numeric sentinels (99, 999, 9999)
  - Domain validators (wave >= 0, direction 0–360, pressure > 0)
  - PTDY/TIDE negative values are allowed
  - VIS visibility unit conversion (mi -> nmi)
  - Non-numeric raw value -> None
"""
from __future__ import annotations

import pytest

from packages.noaa.normalize import normalize_field, normalize_row, normalize_visibility


class TestMMSentinel:
    @pytest.mark.parametrize("field", [
        "WDIR", "WSPD", "GST", "WVHT", "DPD", "APD", "MWD",
        "PRES", "ATMP", "WTMP", "DEWP", "VIS", "PTDY", "TIDE",
    ])
    def test_mm_always_none(self, field: str):
        nm = normalize_field(field, "MM")
        assert nm.value is None
        assert nm.is_sentinel is True


class TestPerFieldSentinels:
    def test_wvht_99_is_none(self):
        assert normalize_field("WVHT", "99").value is None

    def test_wvht_99_0_is_none(self):
        assert normalize_field("WVHT", "99.0").value is None

    def test_pres_9999_is_none(self):
        assert normalize_field("PRES", "9999").value is None

    def test_wdir_999_is_none(self):
        assert normalize_field("WDIR", "999").value is None

    def test_mwd_999_is_none(self):
        assert normalize_field("MWD", "999").value is None

    def test_atmp_999_is_none(self):
        assert normalize_field("ATMP", "999").value is None

    def test_wtmp_99_is_none(self):
        assert normalize_field("WTMP", "99").value is None

    def test_wtmp_999_is_none(self):
        assert normalize_field("WTMP", "999").value is None

    def test_wspd_99_is_none(self):
        assert normalize_field("WSPD", "99").value is None


class TestValidValues:
    def test_wvht_valid(self):
        nm = normalize_field("WVHT", "1.5")
        assert nm.value == pytest.approx(1.5)
        assert nm.is_sentinel is False

    def test_pres_valid(self):
        assert normalize_field("PRES", "1013.5").value == pytest.approx(1013.5)

    def test_wdir_zero(self):
        assert normalize_field("WDIR", "0").value == pytest.approx(0.0)

    def test_wdir_360(self):
        assert normalize_field("WDIR", "360").value == pytest.approx(360.0)


class TestDomainViolations:
    def test_wvht_negative_domain_violation(self):
        nm = normalize_field("WVHT", "-1.0")
        assert nm.value is None
        assert nm.validation_issue is not None

    def test_wdir_361_domain_violation(self):
        nm = normalize_field("WDIR", "361")
        assert nm.value is None

    def test_wspd_negative_domain_violation(self):
        nm = normalize_field("WSPD", "-0.5")
        assert nm.value is None

    def test_dpd_zero_domain_violation(self):
        nm = normalize_field("DPD", "0.0")
        assert nm.value is None   # > 0 required


class TestAllowedNegatives:
    def test_ptdy_negative_allowed(self):
        nm = normalize_field("PTDY", "-1.5")
        assert nm.value == pytest.approx(-1.5)

    def test_tide_negative_allowed(self):
        nm = normalize_field("TIDE", "-0.3")
        assert nm.value == pytest.approx(-0.3)

    def test_atmp_negative_allowed(self):
        nm = normalize_field("ATMP", "-5.0")
        assert nm.value == pytest.approx(-5.0)


class TestNonNumeric:
    def test_garbage_string(self):
        nm = normalize_field("WVHT", "N/A")
        assert nm.value is None
        assert nm.validation_issue is not None


class TestNormalizeRow:
    def test_full_row_normalized(self):
        raw = {
            "WVHT": "1.5", "DPD": "10", "WTMP": "25.0",
            "WDIR": "999", "PRES": "1013",
        }
        result = normalize_row(raw)
        assert result["WVHT"] == pytest.approx(1.5)
        assert result["WDIR"] is None     # sentinel 999
        assert result["PRES"] == pytest.approx(1013.0)


class TestVisibilityConversion:
    def test_nmi_unchanged(self):
        v = normalize_visibility("10.0", header_unit="nmi")
        assert v == pytest.approx(10.0)

    def test_mi_converted_to_nmi(self):
        v = normalize_visibility("10.0", header_unit="mi")
        assert v == pytest.approx(8.68976, rel=1e-3)

    def test_mm_vis_returns_none(self):
        assert normalize_visibility("MM") is None
