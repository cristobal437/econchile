"""
Tests for econchile.parsers — validated against the real BCCh API
response for series F073.TCO.PRE.Z.D (USD/CLP daily exchange rate).

Run with:
    pytest tests/test_parsers.py -v
"""

import json
import os
import sys

import pytest

# Add project root to path so we can import econchile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.parsers import (
    ParsingError,
    parse_observations,
    parse_response,
)

# ─── Paths ──────────────────────────────────────────────────────────────
SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "sample_response.json")


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def single_series_response():
    """Real API response for F073.TCO.PRE.Z.D (USD), single date.

    The sample_response.json is UTF-16 LE with BOM (FF FE).
    We decode it to a string here so callers can use it directly.
    """
    if not os.path.exists(SAMPLE_PATH):
        # The fixture is excluded from the sdist (MANIFEST.in) — tests
        # must skip gracefully when it is absent, not crash.
        pytest.skip("sample_response.json not present (excluded from sdist)")
    with open(SAMPLE_PATH, "rb") as f:
        raw = f.read()
    # Decode UTF-16 (with BOM auto-handled by Python's utf-16 codec)
    return raw.decode("utf-16")


@pytest.fixture
def ok_observation():
    """A single 'OK' observation dict.

    Real data from 09-08-1982 — the very first observation in the
    F073.TCO.PRE.Z.D series.  55.65 was the observed USD/CLP rate.
    """
    return {
        "indexDateString": "09-08-1982",
        "value": "55.65",
        "statusCode": "OK",
    }


@pytest.fixture
def nd_observation():
    """A single 'ND' observation dict.

    14-08-1982 is a Saturday — BCCh marks weekends as ND (No Disponible).
    The value string is "NaN" but statusCode "ND" overrides it.
    """
    return {
        "indexDateString": "14-08-1982",
        "value": "NaN",
        "statusCode": "ND",
    }


@pytest.fixture
def error_response():
    """An API response with Codigo != 0.

    Simulates what BCCh returns when a series ID is invalid.
    Note: Codigo -1 is BCCh's standard code for "series not found".
    """
    return json.dumps({
        "Codigo": -1,
        "Descripcion": "Error",
        "Series": {},
        "SeriesInfos": [],
    })


# ============================================================================
# Test: parse_observations
# ============================================================================

class TestParseObservations:
    """Unit tests for parse_observations(obs_list)."""

    def test_ok_returns_date_and_float(self, ok_observation):
        """statusCode='OK', valid value → returns dict with correct date and float."""
        result = parse_observations([ok_observation])
        assert len(result) == 1
        obs = result[0]
        assert obs["date"] == "1982-08-09"
        assert obs["value"] == 55.65
        assert isinstance(obs["value"], float)

    def test_nd_returns_date_and_none(self, nd_observation):
        """statusCode='ND' → returns None, regardless of value string being 'NaN'."""
        result = parse_observations([nd_observation])
        assert len(result) == 1
        obs = result[0]
        assert obs["date"] == "1982-08-14"
        assert obs["value"] is None

    def test_dd_mm_yyyy_date_format(self):
        """'09-08-1982' → date is '1982-08-09' (YYYY-MM-DD, day before month).

        This is critical: BCCh uses DD-MM-YYYY (Chilean convention).
        "09-08" is August 9th (día 9, mes 8), NOT September 8th.
        """
        obs = [
            {"indexDateString": "09-08-1982", "value": "100", "statusCode": "OK"}
        ]
        result = parse_observations(obs)
        assert result[0]["date"] == "1982-08-09"

    def test_comma_decimal_value(self):
        """Value '47,29' with statusCode='OK' → float 47.29.

        BCCh uses European decimal commas in some responses.
        Delegates to safe_float() which handles this.
        """
        obs = [
            {"indexDateString": "01-01-2000", "value": "47,29", "statusCode": "OK"}
        ]
        result = parse_observations(obs)
        assert result[0]["value"] == 47.29
        assert isinstance(result[0]["value"], float)

    def test_zero_is_valid(self):
        """Value '0' with statusCode='OK' → float 0.0, NOT None.

        Zero is real data.  Some economic indicators
        (e.g. trade balance, GDP growth) can legitimately be zero.
        """
        obs = [
            {"indexDateString": "01-01-2000", "value": "0", "statusCode": "OK"}
        ]
        result = parse_observations(obs)
        assert result[0]["value"] == 0.0
        assert result[0]["value"] is not None

    def test_negative_value(self):
        """Value '-2.53' with statusCode='OK' → float -2.53.

        Negative values are common in macro data (GDP growth,
        current account balance, etc.).
        """
        obs = [
            {"indexDateString": "01-01-2000", "value": "-2.53", "statusCode": "OK"}
        ]
        result = parse_observations(obs)
        assert result[0]["value"] == -2.53

    def test_empty_list_returns_empty(self):
        """parse_observations([]) → [].

        Edge case: empty input should produce empty output,
        not crash.
        """
        result = parse_observations([])
        assert result == []
        assert isinstance(result, list)


# ============================================================================
# Test: parse_response
# ============================================================================

class TestParseResponse:
    """Integration tests for parse_response(raw_text)."""

    def test_success_response_returns_dict(self, single_series_response):
        """Codigo=0 → returns dict with series_id, observations, metadata."""
        result = parse_response(single_series_response)
        assert isinstance(result, dict)
        assert "series_id" in result
        assert "observations" in result
        assert "metadata" in result

    def test_success_response_has_series_id(self, single_series_response):
        """series_id matches F073.TCO.PRE.Z.D."""
        result = parse_response(single_series_response)
        assert result["series_id"] == "F073.TCO.PRE.Z.D"

    def test_success_response_observations_count(self, single_series_response):
        """Observations list has the expected number of elements.

        The sample_response.json contains the full daily USD/CLP
        history from 1982-08-09 through 2026-08-04.
        """
        result = parse_response(single_series_response)
        assert len(result["observations"]) == 16067

    def test_error_codigo_raises_parsing_error(self, error_response):
        """Codigo != 0 → raises ParsingError."""
        with pytest.raises(ParsingError):
            parse_response(error_response)

    def test_utf16_bom_handling(self):
        """Response starting with UTF-16 BOM (FF FE) is parsed correctly.

        The BCCh API returns UTF-16 LE with BOM.  parse_response
        must detect and decode this automatically.
        """
        # Build a minimal valid response and encode to UTF-16.
        raw_json = json.dumps({
            "Codigo": 0,
            "Descripcion": "Success",
            "Series": {
                "seriesId": "F073.TCO.PRE.Z.D",
                "descripEsp": "test",
                "descripIng": "test",
                "Obs": [
                    {
                        "indexDateString": "09-08-1982",
                        "value": "55.65",
                        "statusCode": "OK",
                    }
                ],
            },
            "SeriesInfos": [],
        })
        utf16_bytes = raw_json.encode("utf-16")

        # Verify the BOM is present.
        assert utf16_bytes[:2] == b"\xff\xfe"

        # parse_response should handle raw bytes with BOM.
        result = parse_response(utf16_bytes)
        assert result["series_id"] == "F073.TCO.PRE.Z.D"
        assert len(result["observations"]) == 1

    def test_utf8_handling(self):
        """Plain UTF-8 JSON (no BOM) is also parsed — robustness.

        Some callers may have already decoded the response or
        be using a different HTTP library that handles encoding.
        parse_response should handle plain UTF-8 strings gracefully.
        """
        raw_json = json.dumps({
            "Codigo": 0,
            "Descripcion": "Success",
            "Series": {
                "seriesId": "F073.TCO.PRE.Z.D",
                "descripEsp": "test",
                "descripIng": "test",
                "Obs": [
                    {
                        "indexDateString": "09-08-1982",
                        "value": "55.65",
                        "statusCode": "OK",
                    }
                ],
            },
            "SeriesInfos": [],
        })
        result = parse_response(raw_json)
        assert result["series_id"] == "F073.TCO.PRE.Z.D"
        # Date should be converted from DD-MM-YYYY to YYYY-MM-DD.
        assert result["observations"][0]["date"] == "1982-08-09"
        assert result["observations"][0]["value"] == 55.65

    def test_latin1_bytes_handled(self):
        """Raw latin-1 bytes (accented Spanish titles) are parsed correctly.

        The live API sometimes serves ISO-8859-1 with raw accented
        bytes (e.g. 'ó' as 0xF3) for series whose titles contain
        Spanish accents.  utf-8 decoding of those bytes raises
        UnicodeDecodeError, so a latin-1 fallback is required.
        """
        raw_json = json.dumps({
            "Codigo": 0,
            "Descripcion": "Success",
            "Series": {
                "seriesId": "F073.TCO.PRE.Z.D",
                "descripEsp": "Tipo de cambio nominal (dólar observado $CLP/USD)",
                "descripIng": "Nominal exchange rate (Observed dollar $CLP/USD)",
                "Obs": [
                    {"indexDateString": "09-08-1982", "value": "55.65", "statusCode": "OK"},
                ],
            },
            "SeriesInfos": [],
        }, ensure_ascii=False).encode("latin-1")

        result = parse_response(raw_json)

        assert result["series_id"] == "F073.TCO.PRE.Z.D"
        assert result["metadata"]["descripEsp"] == (
            "Tipo de cambio nominal (dólar observado $CLP/USD)"
        )
        assert result["observations"][0]["value"] == 55.65


# ============================================================================
# Test: Error classes
# ============================================================================

class TestErrors:
    """ParsingError and subclasses."""

    def test_parsing_error_is_exception(self):
        """ParsingError is a subclass of Exception."""
        assert issubclass(ParsingError, Exception)

    def test_parsing_error_message(self):
        """ParsingError includes Codigo and Descripcion in message."""
        exc = ParsingError(-1, "Series not found")
        msg = str(exc)
        assert "-1" in msg
        assert "Series not found" in msg
        # The message follows a consistent format.
        assert msg == "BCCh API error -1: Series not found"


class TestMalformedResponses:
    """Weird-but-parseable JSON must raise ParsingError, never raw exceptions.

    The live API is trusted, but a proxy, cache, or future API change can
    serve structurally broken JSON. parse_response must convert every
    malformed shape into the package error type — no AttributeError,
    KeyError, or TypeError leaking to callers.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "null",
            "[]",
            "{}",
            '{"Codigo": 0, "Series": null}',
            '{"Codigo": 0, "Series": []}',
            '{"Codigo": 0, "Series": {}}',  # missing seriesId
            '{"Codigo": 0, "Series": {"seriesId": "", "Obs": []}}',  # empty seriesId
            '{"Codigo": 0, "Series": {"seriesId": "X", "Obs": {"a": 1}}}',  # Obs not a list
            '{"Codigo": 0, "Series": {"seriesId": "X", "Obs": [{}]}}',  # obs missing fields
        ],
    )
    def test_malformed_response_raises_parsing_error(self, payload):
        """Malformed shapes raise ParsingError (never AttributeError etc.)."""
        with pytest.raises(ParsingError):
            parse_response(payload)

    def test_empty_obs_list_is_valid(self):
        """A well-formed response with zero observations is NOT an error."""
        result = parse_response(
            '{"Codigo": 0, "Series": {"seriesId": "X.Y", "Obs": []}}'
        )
        assert result["series_id"] == "X.Y"
        assert result["observations"] == []