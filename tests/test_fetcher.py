"""
Tests for econchile.fetcher — sync HTTP client for the BCCh REST API.

NEVER hits the real API: every test monkeypatches ``requests.get`` with
a scripted fake (the ``fake_get`` fixture).  Both response encodings are
covered — plain UTF-8 and UTF-16 with BOM — so the encoding-detection
logic is exercised end to end.  Every test passes an explicit token (or
monkeypatches ``BCCH_TOKEN``), so the suite runs with or without the env
var set.

Run with:
    pytest tests/test_fetcher.py -v
"""

import json
from datetime import datetime

import pytest
import requests

from econchile.fetcher import Fetcher
from econchile.series_map import Series
from econchile.types import BcchApiError, Observation, SeriesResult

DEFAULT_SERIES = "F073.TCO.PRE.Z.D"


def make_response(codigo=0, descripcion="Success", obs=None, encoding="utf-8"):
    """Helper to build a fake API response body.

    obs: list of {"indexDateString", "value", "statusCode"} dicts.
    encoding: "utf-8" or "utf-16" — controls whether a BOM is added.
    Returns bytes ready for requests.Response._content.
    """
    payload = {
        "Codigo": codigo,
        "Descripcion": descripcion,
        "Series": {
            "seriesId": DEFAULT_SERIES,
            "descripEsp": "Tipo de cambio nominal (dólar observado $CLP/USD)",
            "descripIng": "Nominal exchange rate (Observed dollar $CLP/USD)",
            "Obs": obs
            if obs is not None
            else [
                {"indexDateString": "02-01-2024", "value": "897.68", "statusCode": "OK"},
                {"indexDateString": "03-01-2024", "value": "901.13", "statusCode": "OK"},
            ],
        },
        "SeriesInfos": [],
    }
    text = json.dumps(payload)
    if encoding == "utf-16":
        # utf-16-le has NO BOM, so prepend it explicitly: FF FE (the
        # exact signature the fetcher's decoder looks for).
        return b"\xff\xfe" + text.encode("utf-16-le")
    return text.encode("utf-8")


@pytest.fixture
def fake_get(monkeypatch):
    """Monkeypatch requests.get to return a canned response.

    Set fake_get.response_body = <bytes> before calling.
    Records the URL for assertions.
    """

    def fake(url, **kwargs):
        fake.urls.append(url)
        resp = requests.Response()
        resp.status_code = 200
        resp._content = fake.response_body
        return resp

    fake.urls = []
    fake.response_body = make_response()
    monkeypatch.setattr("requests.get", fake)
    return fake


@pytest.fixture
def sample_series():
    """Series.USD — typical v0.1 series."""
    return Series.USD


# ─── URL construction and parameter mapping ────────────────────────────


class TestFetchURL:
    """URL construction and parameter mapping."""

    def test_uses_firstdate_lastdate_params(self, fake_get):
        """URL contains firstDate/lastDate, NOT desde/hasta."""
        Fetcher(token="test-token-123").fetch(Series.USD, "2024-01-01", "2024-01-31")
        url = fake_get.urls[0]
        assert "firstDate=2024-01-01" in url
        assert "lastDate=2024-01-31" in url
        assert "desde=" not in url
        assert "hasta=" not in url

    def test_contains_token(self, fake_get):
        """URL contains the token parameter."""
        Fetcher(token="test-token-123").fetch(Series.USD, "2024-01-01", "2024-01-31")
        assert "token=test-token-123" in fake_get.urls[0]

    def test_contains_timeseries_code(self, fake_get):
        """URL contains timeseries=F073.TCO.PRE.Z.D."""
        Fetcher(token="test-token-123").fetch(Series.USD, "2024-01-01", "2024-01-31")
        assert "timeseries=F073.TCO.PRE.Z.D" in fake_get.urls[0]


# ─── Response encoding detection ───────────────────────────────────────


class TestFetchEncoding:
    """Response encoding detection."""

    def test_utf8_response_parsed(self, fake_get):
        """Plain UTF-8 response body is parsed correctly."""
        fake_get.response_body = make_response(encoding="utf-8")
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert [o.date for o in result.observations] == ["2024-01-02", "2024-01-03"]
        assert [o.value for o in result.observations] == [897.68, 901.13]

    def test_utf16_bom_response_parsed(self, fake_get):
        """UTF-16 BOM response body is parsed correctly."""
        fake_get.response_body = make_response(encoding="utf-16")
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert [o.date for o in result.observations] == ["2024-01-02", "2024-01-03"]
        assert [o.value for o in result.observations] == [897.68, 901.13]

    def test_latin1_response_parsed(self, fake_get):
        """ISO-8859-1 (latin-1) response body is parsed correctly.

        The live BCCh API sometimes serves latin-1 — raw accented
        bytes, e.g. "ó" as 0xF3 — for series with Spanish titles
        (USD "dólar", TPM "Política", IPC, IMACEC, PIB).  Without a
        latin-1 fallback, utf-8 decoding raises UnicodeDecodeError.
        """
        payload = {
            "Codigo": 0,
            "Descripcion": "Success",
            "Series": {
                "seriesId": DEFAULT_SERIES,
                "descripEsp": "Tipo de cambio nominal (dólar observado $CLP/USD)",
                "descripIng": "Nominal exchange rate (Observed dollar $CLP/USD)",
                "Obs": [
                    {"indexDateString": "02-01-2024", "value": "897.68", "statusCode": "OK"},
                ],
            },
            "SeriesInfos": [],
        }
        # ensure_ascii=False keeps the accented characters as RAW bytes
        # (0xF3 = "ó" in latin-1) — this is what breaks utf-8 decoding.
        fake_get.response_body = json.dumps(payload, ensure_ascii=False).encode("latin-1")

        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )

        assert result.metadata["descripEsp"] == payload["Series"]["descripEsp"]
        assert [o.value for o in result.observations] == [897.68]


# ─── SeriesResult construction ─────────────────────────────────────────


class TestFetchResult:
    """Fetch returns a proper SeriesResult."""

    def test_returns_series_result(self, fake_get):
        """fetch() returns a SeriesResult instance."""
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert isinstance(result, SeriesResult)

    def test_series_member_preserved(self, fake_get, sample_series):
        """result.series is the Series enum member passed in."""
        result = Fetcher(token="test-token-123").fetch(
            sample_series, "2024-01-01", "2024-01-31"
        )
        assert result.series is sample_series

    def test_observations_are_observation_objects(self, fake_get):
        """result.observations contains Observation instances."""
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert result.observations
        assert all(isinstance(o, Observation) for o in result.observations)
        first = result.observations[0]
        assert first.date == "2024-01-02"
        assert first.value == 897.68

    def test_source_is_api(self, fake_get):
        """result.source == 'api'."""
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert result.source == "api"

    def test_fetched_at_is_datetime(self, fake_get):
        """result.fetched_at is a datetime."""
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert isinstance(result.fetched_at, datetime)

    def test_metadata_present(self, fake_get):
        """result.metadata is a non-empty dict."""
        result = Fetcher(token="test-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert isinstance(result.metadata, dict)
        assert result.metadata
        assert result.metadata["series_id"] == DEFAULT_SERIES
        assert result.metadata["descripEsp"]
        assert result.metadata["descripIng"]

    def test_accepts_raw_code_string(self, fake_get):
        """fetch('F073.TCO.PRE.Z.D', ...) works without a Series enum."""
        result = Fetcher(token="test-token-123").fetch(
            DEFAULT_SERIES, "2024-01-01", "2024-01-31"
        )
        assert result.series == Series.USD
        assert len(result.observations) == 2


# ─── Input validation ──────────────────────────────────────────────────


class TestFetchValidation:
    """Input validation."""

    def test_bad_desde_raises_value_error(self, fake_get):
        """desde='not-a-date' raises ValueError."""
        with pytest.raises(ValueError):
            Fetcher(token="test-token-123").fetch(
                Series.USD, "not-a-date", "2024-01-31"
            )
        # Validation must fail BEFORE any network call.
        assert fake_get.urls == []

    def test_bad_hasta_raises_value_error(self, fake_get):
        """hasta='garbage' raises ValueError."""
        with pytest.raises(ValueError):
            Fetcher(token="test-token-123").fetch(
                Series.USD, "2024-01-01", "garbage"
            )
        assert fake_get.urls == []


# ─── Error handling ────────────────────────────────────────────────────


class TestInvertedWindow:
    """desde > hasta is a client error (v0.2.1)."""

    def test_desde_after_hasta_raises_value_error(self, fake_get):
        with pytest.raises(ValueError):
            Fetcher(token="test-token-123").fetch(
                Series.USD, "2024-12-31", "2024-01-01"
            )


class TestFetchErrors:
    """Error handling."""

    def test_api_error_codigo_raises_bcch_api_error(self, fake_get):
        """Codigo != 0 in response → BcchApiError."""
        fake_get.response_body = make_response(codigo=-1, descripcion="Serie no encontrada")
        with pytest.raises(BcchApiError) as excinfo:
            Fetcher(token="test-token-123").fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        # The API's description is preserved in the wrapped error.
        assert "Serie no encontrada" in str(excinfo.value)

    def test_network_error_wrapped(self, monkeypatch):
        """requests.get raising ConnectionError → BcchApiError."""
        def boom(url, **kwargs):
            raise requests.ConnectionError("connection refused")

        monkeypatch.setattr("requests.get", boom)
        # No retries here — error WRAPPING is what's under test
        # (retry behavior lives in TestFetchRetry).
        with pytest.raises(BcchApiError):
            Fetcher(token="test-token-123", max_retries=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )

    def test_http_500_raises_bcch_api_error(self, monkeypatch):
        """Non-200 HTTP status → BcchApiError."""
        def five_hundred(url, **kwargs):
            resp = requests.Response()
            resp.status_code = 500
            resp._content = b""
            return resp

        monkeypatch.setattr("requests.get", five_hundred)
        # No retries here — the WRAP is what's under test
        # (retry behavior lives in TestFetchRetry).
        with pytest.raises(BcchApiError):
            Fetcher(token="test-token-123", max_retries=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )

    def test_undecodable_body_wrapped_as_bcch_api_error(self, monkeypatch):
        """Truncated UTF-16 body → BcchApiError, never a raw UnicodeDecodeError.

        latin-1 can never fail, but a UTF-16-BOM body with an odd byte
        count still raises UnicodeDecodeError — it must surface wrapped
        as BcchApiError, matching the documented error contract.
        """
        def truncated_utf16(url, **kwargs):
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b"\xff\xfe\x41"  # BOM + single byte: invalid UTF-16
            return resp

        monkeypatch.setattr("requests.get", truncated_utf16)
        with pytest.raises(BcchApiError):
            Fetcher(token="test-token-123").fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )


# ─── Retry behavior ────────────────────────────────────────────────────


class TestFetchRetry:
    """Retry logic for transient failures (network, HTTP 5xx, HTML bodies).

    Policy (see specs/v011_bugfix_spec.md): retry network errors,
    HTTP >= 500, and non-JSON/HTML bodies.  Never retry HTTP 4xx or
    business errors (Codigo != 0).
    """

    def _scripted_get(self, monkeypatch, script):
        """Install a requests.get that walks through ``script``.

        Each item is either an exception instance to raise or bytes to
        use as the response body.  Once exhausted, the LAST item keeps
        repeating (used by the exhaustion tests).
        """
        calls = []

        def fake(url, **kwargs):
            calls.append(url)
            step = script[min(len(calls) - 1, len(script) - 1)]
            if isinstance(step, Exception):
                raise step
            resp = requests.Response()
            resp.status_code = 200
            resp._content = step
            return resp

        monkeypatch.setattr("requests.get", fake)
        return calls

    def test_network_error_retries_then_succeeds(self, monkeypatch):
        """Two network errors then a valid response → success after retries."""
        calls = self._scripted_get(
            monkeypatch,
            [requests.ConnectionError("down"), requests.ConnectionError("down"), make_response()],
        )
        result = Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert len(calls) == 3  # 1 attempt + 2 retries
        assert result.source == "api"

    def test_network_error_exhausts_retries(self, monkeypatch):
        """Persistent network failure → BcchApiError after max_retries+1 attempts."""
        calls = self._scripted_get(
            monkeypatch,
            [requests.ConnectionError("down")],  # repeats forever
        )
        with pytest.raises(BcchApiError):
            Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        assert len(calls) == 3

    def test_html_body_retries_then_succeeds(self, monkeypatch):
        """HTML 'Página no encontrada' page (HTTP 200) → retried, then success."""
        html = b"<!DOCTYPE html><html><body>P\xc3\xa1gina no encontrada</body></html>"
        calls = self._scripted_get(monkeypatch, [html, make_response()])

        result = Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )

        assert len(calls) == 2
        assert [o.value for o in result.observations] == [897.68, 901.13]

    def test_html_body_exhausts_retries(self, monkeypatch):
        """Persistent HTML body → BcchApiError after max_retries+1 attempts."""
        html = b"<!DOCTYPE html><html><body>error</body></html>"
        calls = self._scripted_get(monkeypatch, [html])

        with pytest.raises(BcchApiError):
            Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        assert len(calls) == 3

    def test_http_500_retries(self, monkeypatch):
        """HTTP 500 → retried; persistent 500 → BcchApiError after 3 attempts."""
        calls = []

        def five_hundred(url, **kwargs):
            calls.append(url)
            resp = requests.Response()
            resp.status_code = 500
            resp._content = b""
            return resp

        monkeypatch.setattr("requests.get", five_hundred)
        with pytest.raises(BcchApiError):
            Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        assert len(calls) == 3

    def test_http_401_not_retried(self, monkeypatch):
        """HTTP 4xx (client errors) are NOT retried — exactly one attempt."""
        calls = []

        def unauthorized(url, **kwargs):
            calls.append(url)
            resp = requests.Response()
            resp.status_code = 401
            resp._content = b""
            return resp

        monkeypatch.setattr("requests.get", unauthorized)
        with pytest.raises(BcchApiError):
            Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        assert len(calls) == 1

    def test_business_error_not_retried(self, fake_get):
        """Codigo != 0 (business error) is NOT retried — one attempt only."""
        fake_get.response_body = make_response(codigo=-1, descripcion="Serie no encontrada")
        with pytest.raises(BcchApiError):
            Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        assert len(fake_get.urls) == 1

    def test_http_429_retried_then_succeeds(self, monkeypatch):
        """HTTP 429 is retryable — a 429 followed by success succeeds."""
        urls = []

        def first_429_then_ok(url, **kwargs):
            urls.append(url)
            if len(urls) == 1:
                resp = requests.Response()
                resp.status_code = 429
                resp._content = b""
                return resp
            resp = requests.Response()
            resp.status_code = 200
            resp._content = make_response()
            return resp

        monkeypatch.setattr("requests.get", first_429_then_ok)
        result = Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert len(urls) == 2
        assert [o.value for o in result.observations] == [897.68, 901.13]

    def test_http_429_exhausts_retries(self, monkeypatch):
        """Persistent 429 → BcchApiError after max_retries+1 attempts."""
        urls = []

        def always_429(url, **kwargs):
            urls.append(url)
            resp = requests.Response()
            resp.status_code = 429
            resp._content = b""
            return resp

        monkeypatch.setattr("requests.get", always_429)
        with pytest.raises(BcchApiError):
            Fetcher(token="t", max_retries=2, retry_backoff=0).fetch(
                Series.USD, "2024-01-01", "2024-01-31"
            )
        assert len(urls) == 3

    def test_token_with_slash_is_url_encoded(self, fake_get):
        """Tokens containing '/' are URL-encoded (%2F), never pasted raw."""
        Fetcher(token="tok/en/123").fetch(Series.USD, "2024-01-01", "2024-01-31")
        assert "token=tok%2Fen%2F123" in fake_get.urls[0]
        assert "token=tok/en/123" not in fake_get.urls[0]


# ─── Token resolution ──────────────────────────────────────────────────


class TestTokenSource:
    """Token resolution."""

    def test_default_reads_env_var(self, monkeypatch):
        """No token arg → reads BCCH_TOKEN from env."""
        monkeypatch.setenv("BCCH_TOKEN", "env-token-999")
        urls = []

        def fake(url, **kwargs):
            urls.append(url)
            resp = requests.Response()
            resp.status_code = 200
            resp._content = make_response()
            return resp

        monkeypatch.setattr("requests.get", fake)
        Fetcher().fetch(Series.USD, "2024-01-01", "2024-01-31")
        assert "token=env-token-999" in urls[0]

    def test_explicit_token_wins(self, monkeypatch):
        """Explicit token arg overrides env var."""
        monkeypatch.setenv("BCCH_TOKEN", "env-token-999")
        urls = []

        def fake(url, **kwargs):
            urls.append(url)
            resp = requests.Response()
            resp.status_code = 200
            resp._content = make_response()
            return resp

        monkeypatch.setattr("requests.get", fake)
        Fetcher(token="explicit-token-123").fetch(
            Series.USD, "2024-01-01", "2024-01-31"
        )
        assert "token=explicit-token-123" in urls[0]
        assert "env-token-999" not in urls[0]


class TestNoToken:
    """Token-less construction + fetch-time BcchApiError (v0.1.2 fix).

    The token must be optional at construction; a missing token is an
    API-side failure at fetch time (so OfflineClient's fallback works).
    """

    def test_constructor_without_token_ok(self, monkeypatch):
        """Fetcher(token=None) with no env token constructs without raising."""
        monkeypatch.delenv("BCCH_TOKEN", raising=False)
        Fetcher(token=None)  # must NOT raise at construction

    def test_fetch_without_token_raises_bcch_api_error(self, monkeypatch):
        """fetch() without a token raises BcchApiError before any I/O."""
        monkeypatch.delenv("BCCH_TOKEN", raising=False)
        fetcher = Fetcher(token=None)
        with pytest.raises(BcchApiError):
            fetcher.fetch(Series.USD, "2024-01-01", "2024-01-31")
