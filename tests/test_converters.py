"""
Tests for econchile.converters — validated against real Chilean macro data.

Run with:
    pytest tests/test_converters.py -v

Every test uses values taken directly from the data files in ../data/.
A passing test means the converter handles real-world Chilean data correctly.
"""

import json
import csv
import sys
import os

# Add the project root to the import path so we can import econchile.
# This is a standard pattern for test files that live inside the project
# but outside the package itself.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from econchile.converters import (
    safe_float,
    safe_date,
    normalize_series,
    count_valid,
    NULL_SENTINELS,
)

# ─── Paths to real data files ──────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
JSON_PATH = os.path.join(DATA_DIR, "chile-macro.json")
CSV_PATH = os.path.join(DATA_DIR, "macro-chile-lean.csv")  # LEAN: 70 cols


# ═══════════════════════════════════════════════════════════════════════
# safe_float tests
# ═══════════════════════════════════════════════════════════════════════

class TestSafeFloat:
    """Tests for safe_float() using values from our real data files."""

    def test_comma_decimal_from_csv(self):
        """CSV uses European decimal commas: "47,29" → 47.29"""
        # Real value from total-macro-chile.csv, row 1 (mar.1996)
        assert safe_float("47,29") == 47.29

    def test_dot_decimal_from_csv(self):
        """CSV also uses dot decimals for some columns: "16.472" → 16.472"""
        # Real value from total-macro-chile.csv, row 1, PIB column
        assert safe_float("16.472") == 16.472

    def test_negative_from_json(self):
        """JSON has negative values: "-2.53" → -2.53"""
        # Real value from chile-macro.json, 1999-01 PIB
        assert safe_float("-2.53") == -2.53

    def test_small_decimal_from_json(self):
        """JSON IPC values are small MoM% changes: "-0.336329575" → -0.336..."""
        # Real value from chile-macro.json, 1999-01 IPC
        result = safe_float("-0.336329575")
        assert result is not None
        assert abs(result - (-0.336329575)) < 0.0000001

    def test_integer_passthrough(self):
        """Already-numeric values pass through unchanged as float."""
        # JSON UF values: {"fecha": "1977-08", "UF": 403.17}
        assert safe_float(403.17) == 403.17
        assert safe_float(0) == 0.0
        assert safe_float(-1) == -1.0

    def test_none_passthrough(self):
        """Python None returns None."""
        assert safe_float(None) is None

    def test_null_sentinel_na(self):
        """'N/A' → None"""
        assert safe_float("N/A") is None

    def test_null_sentinel_empty(self):
        """Empty string → None"""
        assert safe_float("") is None
        assert safe_float("   ") is None  # whitespace-only

    def test_null_sentinel_dots(self):
        """'...' (BCCh placeholder) → None"""
        assert safe_float("...") is None

    def test_null_sentinel_dash(self):
        """'-' (spreadsheet convention) → None"""
        assert safe_float("-") is None

    def test_zero_is_real_data(self):
        """Zero is legitimate data, NOT converted to None."""
        assert safe_float("0") == 0.0
        assert safe_float("0.0") == 0.0
        assert safe_float("0,0") == 0.0   # European comma-zero

    def test_unparseable_garbage(self):
        """Truly unparseable strings return None, never crash."""
        assert safe_float("hello_world") is None
        assert safe_float("12.34.56") is None


# ═══════════════════════════════════════════════════════════════════════
# safe_date tests
# ═══════════════════════════════════════════════════════════════════════

class TestSafeDate:
    """Tests for safe_date() using date formats from our real data files."""

    def test_spanish_month_csv_format(self):
        """CSV format 'mar.1996' → '1996-03-01'"""
        assert safe_date("mar.1996") == "1996-03-01"
        assert safe_date("ene.1996") == "1996-01-01"
        assert safe_date("dic.2023") == "2023-12-01"
        assert safe_date("ago.2015") == "2015-08-01"

    def test_spanish_month_case_insensitive(self):
        """'MAR.1996' and 'Mar.1996' both work."""
        assert safe_date("MAR.1996") == "1996-03-01"
        assert safe_date("Mar.1996") == "1996-03-01"
        assert safe_date("DIC.2023") == "2023-12-01"

    def test_iso_year_month_from_json(self):
        """JSON format '1996-03' → '1996-03-01'"""
        assert safe_date("1996-03") == "1996-03-01"
        assert safe_date("1977-08") == "1977-08-01"
        assert safe_date("2026-07") == "2026-07-01"

    def test_slash_format(self):
        """Alternate format '03/1996' → '1996-03-01'"""
        assert safe_date("03/1996") == "1996-03-01"
        assert safe_date("12/2023") == "2023-12-01"

    def test_none_and_empty(self):
        """None and empty strings return None."""
        assert safe_date(None) is None
        assert safe_date("") is None
        assert safe_date("   ") is None

    def test_unparseable_returns_none(self):
        """Unrecognised format returns None, not a crash."""
        assert safe_date("hello") is None
        assert safe_date("1996") is None       # year only, no month
        assert safe_date("xyz.2024") is None   # fake month abbreviation


# ═══════════════════════════════════════════════════════════════════════
# NULL_SENTINELS structure
# ═══════════════════════════════════════════════════════════════════════

class TestNullSentinels:
    """Tests for the NULL_SENTINELS constant."""

    def test_is_a_set(self):
        """It's a set, not a list — O(1) lookups."""
        assert isinstance(NULL_SENTINELS, set)

    def test_all_lowercase(self):
        """All sentinels are lowercase for case-insensitive matching."""
        for sentinel in NULL_SENTINELS:
            assert sentinel == sentinel.lower(), f"'{sentinel}' is not lowercase"

    def test_zero_not_in_sentinels(self):
        """Zero must NOT be in NULL_SENTINELS — it's real data."""
        assert "0" not in NULL_SENTINELS
        assert "0.0" not in NULL_SENTINELS


# ═══════════════════════════════════════════════════════════════════════
# Real data integration tests
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(
    not (os.path.exists(JSON_PATH) and os.path.exists(CSV_PATH)),
    reason="legacy data/ files not shipped in the sdist",
)
class TestRealDataIntegration:
    """Validate converters against the actual data files."""

    def test_json_file_exists(self):
        """Sanity check: the JSON backup exists at the expected path."""
        assert os.path.exists(JSON_PATH), (
            f"JSON not found at {JSON_PATH}. Is chile-macro.json in ../data/?"
        )

    def test_csv_file_exists(self):
        """Sanity check: the CSV backup exists at the expected path."""
        assert os.path.exists(CSV_PATH), (
            f"CSV not found at {CSV_PATH}. Is total-macro-chile.csv in ../data/?"
        )

    def test_safe_date_parses_all_json_dates(self):
        """Every 'fecha' value in the JSON must parse successfully."""
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        failures = []
        for record in data["data"]:
            raw = record["fecha"]
            result = safe_date(raw)
            if result is None:
                failures.append(raw)

        assert failures == [], (
            f"safe_date() failed on {len(failures)} JSON dates: {failures[:10]}"
        )

    def test_safe_date_parses_all_csv_dates(self):
        """Every 'Periodo' value in the CSV must parse successfully."""
        with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)

        failures = []
        for row in rows:
            raw = row["Periodo"]
            result = safe_date(raw)
            if result is None:
                failures.append(raw)

        assert failures == [], (
            f"safe_date() failed on {len(failures)} CSV dates: {failures[:10]}"
        )

    def test_safe_float_handles_all_json_numeric_series(self):
        """Every numeric value in the JSON (UF, USD, IPC, etc.) must parse
        or legitimately return None. No crashes allowed."""
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        crash_count = 0
        total_values = 0
        for record in data["data"]:
            for key in record:
                if key == "fecha":
                    continue
                total_values += 1
                try:
                    safe_float(record[key])
                except Exception as e:
                    crash_count += 1
                    if crash_count <= 3:
                        print(f"CRASH on {record['fecha']}.{key}={record[key]}: {e}")

        assert crash_count == 0, (
            f"safe_float() crashed on {crash_count}/{total_values} values"
        )

    def test_json_data_density_report(self):
        """Reproduce the data density analysis from our earlier comparison.
        This is informational — it always passes, but prints the report."""
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        print("\n  JSON data density (via safe_float):")
        series_names = sorted(data["metadata"]["series"])
        for name in series_names:
            values = []
            for rec in data["data"]:
                if name in rec:
                    values.append(rec[name])
                else:
                    values.append(None)
            normalized = normalize_series(values)
            valid = count_valid(normalized)
            pct = (valid / len(values)) * 100 if values else 0
            print(f"    {name:14s}: {valid:4d}/{len(values)} ({pct:5.1f}%)")

        # This test always passes — it's a report, not an assertion.
        assert True
