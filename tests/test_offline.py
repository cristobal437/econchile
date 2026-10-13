"""
Tests for econchile.offline — the resilience layer (API → cache → error).

NEVER hits the real BCCh API: every test monkeypatches
``econchile.fetcher.Fetcher.fetch`` (class-level, so the OfflineClient's
internal fetcher instance is affected) and uses a tmp_path SQLite cache.
Every test passes an explicit token, so ``~/.econchile`` is never touched.

Run with:
    pytest tests/test_offline.py -v
"""

import os
import sqlite3
import sys

import pytest
import requests

# Add project root to path so we can import econchile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.offline import OfflineClient
from econchile.series_map import Series
from econchile.types import (
    BcchApiError,
    BcchError,
    BcchOfflineError,
    Observation,
    SeriesResult,
)

# The date window used across the tests.
DESDE = "2024-01-01"
HASTA = "2024-12-31"


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """OfflineClient with tmp cache DB and a stubbed fetcher.

    Stub behavior controlled via the returned object's attributes:
    - stub_result: SeriesResult to return on success (or None to raise)
    - stub_error: exception to raise from fetch
    """
    client = OfflineClient(token="test-token", db_path=tmp_path / "offline_test.db")
    client.stub_result = None
    client.stub_error = None

    def fake_fetch(self, series, desde, hasta):
        if client.stub_error is not None:
            raise client.stub_error
        return client.stub_result

    monkeypatch.setattr("econchile.fetcher.Fetcher.fetch", fake_fetch)
    return client


@pytest.fixture
def sample_result():
    """A typical SeriesResult returned by the (stubbed) API."""
    from datetime import datetime

    return SeriesResult(
        series=Series.USD,
        observations=[
            Observation(date="2024-01-01", value=897.68),
            Observation(date="2024-01-02", value=901.13),
        ],
        fetched_at=datetime(2024, 1, 3, 10, 0, 0),
        source="api",
        metadata={"series_id": "F073.TCO.PRE.Z.D"},
    )


# ─── Helpers ───────────────────────────────────────────────────────────

def _raise_if_called(message):
    """Return a callable that fails the test if invoked."""

    def raiser(*args, **kwargs):
        pytest.fail(message)

    return raiser


def _insert_expired_row(offline, sample_result):
    """Store observations + window whose fetched_at is long past (no sleep).

    The cache always stamps fetched_at = now, so to create EXPIRED data
    deterministically we write it straight into SQLite with the v0.3
    schema (observations + fetched_windows).
    """
    old = "2024-01-03T10:00:00.000000+00:00"
    code = Series.USD.value
    conn = sqlite3.connect(str(offline._cache._db_path))
    try:
        for obs in sample_result.observations:
            conn.execute(
                "INSERT OR REPLACE INTO observations (series, date, value, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (code, obs.date, obs.value, old),
            )
        conn.execute(
            "INSERT OR REPLACE INTO fetched_windows (series, desde, hasta, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (code, DESDE, HASTA, old),
        )
        conn.commit()
    finally:
        conn.close()


def _assert_same_data(served, expected):
    """A cache-served result carries the same data as the stored one.

    ``fetched_at`` is deliberately NOT compared: since v0.3 a cache-served
    result reports when its observations were stored, not the fixture's
    fake timestamp.
    """
    assert served.series == expected.series
    assert served.observations == expected.observations
    assert served.source == expected.source
    assert served.metadata == expected.metadata


class TestApiSuccess:
    """API-first path."""

    def test_returns_fresh_result(self, offline, sample_result):
        """When API succeeds, get() returns the API result."""
        offline.stub_result = sample_result

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result == sample_result

    def test_result_source_is_api(self, offline, sample_result):
        """Fresh result has source == 'api'."""
        offline.stub_result = sample_result

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.source == "api"

    def test_writes_to_cache(self, offline, sample_result):
        """After API success, the cache contains the result (fallback is warm)."""
        offline.stub_result = sample_result

        offline.get(Series.USD, DESDE, HASTA)

        # The cache now holds the two observation rows (v0.3: size()
        # counts observations, not query entries), retrievable under the
        # same key convention the client computes.
        assert offline._cache.size() == 2
        assert offline._cache.covers(Series.USD, DESDE, HASTA) is True
        stored = offline._cache.load(Series.USD, DESDE, HASTA)
        _assert_same_data(stored, sample_result)

    def test_does_not_raise(self, offline, sample_result):
        """API success never raises."""
        offline.stub_result = sample_result

        # If get() raised anything, the test fails here.
        result = offline.get(Series.USD, DESDE, HASTA)

        assert result == sample_result


class TestApiFailureCacheHit:
    """API down, cache has data."""

    def test_returns_cached_result(self, offline, sample_result):
        """When API fails but cache has the key, get() returns cached data."""
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        # The cached data round-trips through SQLite unchanged.
        _assert_same_data(result, sample_result)

    def test_no_offline_error_raised(self, offline, sample_result):
        """Cache hit on API failure does NOT raise BcchOfflineError."""
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        # Served from the stored payload, whose source was "api".
        assert result.source == "api"

    def test_result_usable(self, offline, sample_result):
        """Cached result is a valid SeriesResult with observations."""
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert isinstance(result, SeriesResult)
        assert len(result.observations) == 2
        assert result.observations[0].date == "2024-01-01"
        assert result.observations[0].value == 897.68
        assert result.metadata == {"series_id": "F073.TCO.PRE.Z.D"}


class TestApiFailureCacheMiss:
    """API down AND cache empty."""

    def test_raises_bcch_offline_error(self, offline):
        """When API fails and cache is empty, get() raises BcchOfflineError."""
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError):
            offline.get(Series.USD, DESDE, HASTA)

    def test_error_is_bcch_error_subclass(self, offline):
        """BcchOfflineError is a BcchError subclass (from types)."""
        assert issubclass(BcchOfflineError, BcchError)
        offline.stub_error = BcchApiError("BCCh API is down")

        # Catching the base BcchError family catches it too.
        with pytest.raises(BcchError):
            offline.get(Series.USD, DESDE, HASTA)

    def test_context_contains_series(self, offline):
        """error.context['series'] matches the requested series."""
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)

        # The RESOLVED code string — what was actually queried.
        assert excinfo.value.context["series"] == Series.USD.value

    def test_context_contains_date_window(self, offline):
        """error.context has desde and hasta."""
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)

        assert excinfo.value.context["desde"] == DESDE
        assert excinfo.value.context["hasta"] == HASTA

    def test_context_contains_api_error(self, offline):
        """error.context['api_error'] mentions the original failure."""
        offline.stub_error = BcchApiError("BCCh API error: serie no encontrada")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)

        # The original failure is preserved verbatim — never masked.
        assert "serie no encontrada" in excinfo.value.context["api_error"]

    def test_context_contains_cache_had_data_flag(self, offline):
        """error.context['cache_had_data'] is False on a miss."""
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)

        assert excinfo.value.context["cache_had_data"] is False

    def test_expired_cache_row_counts_as_miss(self, offline, sample_result):
        """Expired cache rows are a miss: Cache.get sweeps them, offline reports cache_had_data=False."""
        _insert_expired_row(offline, sample_result)
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)

        # Expired data (older than max_stale_age) is never served.
        assert excinfo.value.context["cache_had_data"] is False
        # v0.3: expired rows are not swept on read; purge() removes them
        # once they pass max_purge_age.
        assert offline._cache.size() == 2

    def test_error_message_is_helpful(self, offline):
        """str(error) mentions the series and suggests the cache was empty."""
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)

        message = str(excinfo.value)
        assert Series.USD.value in message  # the failing series is named
        assert "cache" in message  # the empty cache is mentioned
        assert "BCCh API is down" in message  # the original error is visible


class TestSeriesResolution:
    """Series identifier handling."""

    def test_accepts_enum(self, offline, sample_result):
        """get(Series.USD, ...) works."""
        offline.stub_result = sample_result

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.series is Series.USD

    def test_accepts_human_name(self, offline, sample_result):
        """get('usd', ...) works (case-insensitive)."""
        offline.stub_result = sample_result

        result = offline.get("usd", DESDE, HASTA)

        # The human name resolved to the enum member before the fetch.
        assert result.series is Series.USD

    def test_accepts_raw_code(self, offline, sample_result):
        """get('F073.TCO.PRE.Z.D', ...) works."""
        offline.stub_result = sample_result

        result = offline.get("F073.TCO.PRE.Z.D", DESDE, HASTA)

        # The raw code resolved to the enum member (same cache key too).
        assert result.series is Series.USD

    def test_unknown_series_raises_key_error(self, offline):
        """get('NOT_A_SERIES', ...) raises KeyError."""
        with pytest.raises(KeyError):
            offline.get("NOT_A_SERIES", DESDE, HASTA)

    def test_accepts_raw_code_outside_catalog(self, offline):
        """A BCCh code outside the indexed enum passes through (v0.2.1)."""
        from datetime import datetime

        code = "F072.CLP.EUR.N.O.D"
        offline.stub_result = SeriesResult(
            series=code,
            observations=[Observation(date="2024-01-02", value=1000.0)],
            fetched_at=datetime(2024, 1, 3, 10, 0, 0),
        )

        result = offline.get(code, DESDE, HASTA)

        assert result.series == code
        # Warm fallback stored under the raw code.
        assert offline._cache.covers(code, DESDE, HASTA) is True


class TestValidation:
    """Date validation happens before any I/O."""

    def test_bad_desde_raises_value_error(self, offline):
        """desde='garbage' raises ValueError without touching fetcher or cache."""
        # Prove no cache AND no network is touched on bad input.
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(
                "econchile.fetcher.Fetcher.fetch",
                _raise_if_called("fetcher must not be called for bad dates"),
            )
            monkeypatch.setattr(
                "econchile.cache.Cache.get",
                _raise_if_called("cache.get must not be called for bad dates"),
            )
            monkeypatch.setattr(
                "econchile.cache.Cache.set",
                _raise_if_called("cache.set must not be called for bad dates"),
            )

            with pytest.raises(ValueError):
                offline.get(Series.USD, "garbage", HASTA)
        finally:
            monkeypatch.undo()


class TestNonApiExceptions:
    """Only API/network failures may trigger the cache fallback."""

    def test_non_api_exception_propagates(self, offline, sample_result):
        """A programming error (ValueError) must NOT be masked as 'API down'.

        The fallback exists for API failures only — real bugs must
        propagate so they are never hidden by stale-cache serving.
        """
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        offline.stub_error = ValueError("programming bug — must not be masked")

        with pytest.raises(ValueError):
            offline.get(Series.USD, DESDE, HASTA)

    def test_network_error_still_falls_back(self, offline, sample_result):
        """requests network errors still trigger the cache fallback."""
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        offline.stub_error = requests.ConnectionError("connection refused")

        result = offline.get(Series.USD, DESDE, HASTA)

        _assert_same_data(result, sample_result)


class TestNoToken:
    """OfflineClient works without a token (cache-only) — v0.1.2 fix.

    Construction must not require a token; a missing token is treated as
    an API failure at fetch time, so the cache fallback applies.
    """

    def test_constructs_without_token(self, tmp_path, monkeypatch):
        """OfflineClient(token=None) with no env token constructs fine."""
        monkeypatch.delenv("BCCH_TOKEN", raising=False)
        client = OfflineClient(token=None, db_path=tmp_path / "no_token.db")
        assert client is not None

    def test_cache_served_without_token(self, tmp_path, monkeypatch, sample_result):
        """Cached data is served with no token configured."""
        monkeypatch.delenv("BCCH_TOKEN", raising=False)
        client = OfflineClient(token=None, db_path=tmp_path / "no_token.db")
        client._cache.put_observations(sample_result, DESDE, HASTA)

        result = client.get(Series.USD, DESDE, HASTA)

        _assert_same_data(result, sample_result)

    def test_offline_error_without_token(self, tmp_path, monkeypatch):
        """No token AND no cache → BcchOfflineError (not ValueError)."""
        monkeypatch.delenv("BCCH_TOKEN", raising=False)
        client = OfflineClient(token=None, db_path=tmp_path / "no_token.db")

        with pytest.raises(BcchOfflineError):
            client.get(Series.USD, DESDE, HASTA)


# ─── v0.3: stale / partial serving (specs/v0_3_cache_architecture.md §5.2) ───

def _set_age(offline, series_code, **delta):
    """Make every stored row of ``series_code`` look ``delta`` old (no sleep)."""
    from datetime import datetime, timedelta, timezone

    stamp = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="microseconds")
    conn = sqlite3.connect(str(offline._cache._db_path))
    try:
        conn.execute(
            "UPDATE observations SET fetched_at = ? WHERE series = ?", (stamp, series_code)
        )
        conn.execute(
            "UPDATE fetched_windows SET fetched_at = ? WHERE series = ?", (stamp, series_code)
        )
        conn.commit()
    finally:
        conn.close()


def _usd(points):
    """A USD SeriesResult from ``[(date, value), ...]``."""
    from datetime import datetime, timezone

    return SeriesResult(
        series=Series.USD,
        observations=[Observation(d, v) for d, v in points],
        fetched_at=datetime.now(timezone.utc),
        source="api",
        metadata={"series_id": Series.USD.value},
    )


@pytest.fixture
def strict_offline(tmp_path, monkeypatch):
    """OfflineClient(strict=True) with a stubbed, failing fetcher."""
    client = OfflineClient(
        token="test-token", db_path=tmp_path / "strict_test.db", strict=True
    )
    client.stub_error = BcchApiError("BCCh API is down")

    def fake_fetch(self, series, desde, hasta):
        raise client.stub_error

    monkeypatch.setattr("econchile.fetcher.Fetcher.fetch", fake_fetch)
    return client


class TestFreshResultFlags:
    """A live API result is never stale and never partial."""

    def test_api_result_flags(self, offline, sample_result):
        offline.stub_result = sample_result
        result = offline.get(Series.USD, DESDE, HASTA)
        assert result.stale is False
        assert result.missing_dates == []

    def test_api_success_records_window(self, offline, sample_result):
        """The requested window is stored, so the fallback knows it was asked."""
        offline.stub_result = sample_result
        offline.get(Series.USD, DESDE, HASTA)
        assert offline._cache.covers(Series.USD.value, DESDE, HASTA) is True


class TestStaleServing:
    """API down: fresh, else stale (≤ max_stale_age) observations are served."""

    def test_fresh_cache_served_not_stale(self, offline, sample_result):
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        _assert_same_data(result, sample_result)
        assert result.stale is False
        assert result.missing_dates == []

    def test_data_older_than_ttl_served_as_stale(self, offline, sample_result):
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        _set_age(offline, Series.USD.value, days=2)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.observations == sample_result.observations
        assert result.stale is True
        assert result.missing_dates == []
        assert result.source == "api"  # data still originates from the API

    def test_mixed_fresh_and_stale_is_stale(self, offline):
        offline._cache.put_observations(_usd([("2024-01-02", 1.0)]), DESDE, "2024-06-30")
        _set_age(offline, Series.USD.value, days=2)
        offline._cache.put_observations(_usd([("2024-07-01", 2.0)]), "2024-07-01", HASTA)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert [o.date for o in result.observations] == ["2024-01-02", "2024-07-01"]
        assert result.stale is True

    def test_expired_data_not_served(self, offline, sample_result):
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        _set_age(offline, Series.USD.value, days=8)
        offline.stub_error = BcchApiError("BCCh API is down")

        with pytest.raises(BcchOfflineError) as excinfo:
            offline.get(Series.USD, DESDE, HASTA)
        assert excinfo.value.context["cache_had_data"] is False

    def test_covered_window_without_observations_served_empty(self, offline):
        """A fetched window with nothing published (a weekend) is an answer, not a miss."""
        offline._cache.put_observations(_usd([]), "2024-01-06", "2024-01-07")
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, "2024-01-06", "2024-01-07")

        assert result.observations == []
        assert result.missing_dates == []

    def test_metadata_preserved_on_fallback(self, offline, sample_result):
        offline._cache.put_observations(sample_result, DESDE, HASTA)
        _set_age(offline, Series.USD.value, days=2)
        offline.stub_error = BcchApiError("BCCh API is down")

        assert offline.get(Series.USD, DESDE, HASTA).metadata == sample_result.metadata


class TestPartialServing:
    """API down: a partially covered window is served with missing_dates."""

    def test_leading_gap(self, offline):
        offline._cache.put_observations(
            _usd([("2024-03-01", 1.0), ("2024-12-31", 2.0)]), "2024-03-01", HASTA
        )
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.missing_dates == [DESDE]
        assert [o.date for o in result.observations] == ["2024-03-01", "2024-12-31"]

    def test_trailing_gap(self, offline):
        offline._cache.put_observations(
            _usd([("2024-01-01", 1.0), ("2024-06-28", 2.0)]), DESDE, "2024-06-30"
        )
        offline.stub_error = BcchApiError("BCCh API is down")

        assert offline.get(Series.USD, DESDE, HASTA).missing_dates == [HASTA]

    def test_both_gaps(self, offline):
        offline._cache.put_observations(
            _usd([("2024-03-01", 1.0), ("2024-06-28", 2.0)]), "2024-03-01", "2024-06-30"
        )
        offline.stub_error = BcchApiError("BCCh API is down")

        assert offline.get(Series.USD, DESDE, HASTA).missing_dates == [DESDE, HASTA]

    def test_nd_rows_served_and_not_missing(self, offline):
        """An ND (None) observation is coverage: served, never in missing_dates."""
        offline._cache.put_observations(
            _usd([(DESDE, None), ("2024-01-02", 901.13)]), DESDE, HASTA
        )
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.observations[0] == Observation(DESDE, None)
        assert result.missing_dates == []

    def test_bound_covered_by_observation_beyond_it(self, offline):
        """Observations on/beyond both bounds cover them even without a window."""
        offline._cache.put_observations(
            _usd([("2023-12-29", 1.0), ("2024-06-28", 2.0), ("2025-01-02", 3.0)])
        )
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.missing_dates == []
        assert [o.date for o in result.observations] == ["2024-06-28"]

    def test_cron_with_new_hasta_still_served(self, offline):
        """The headline v0.3 case: yesterday's cron data serves today's new window."""
        offline._cache.put_observations(
            _usd([("2020-01-02", 1.0), ("2024-06-28", 2.0)]), "2020-01-01", "2024-06-30"
        )
        offline.stub_error = requests.ConnectionError("connection refused")

        result = offline.get(Series.USD, "2020-01-01", "2024-07-01")

        assert [o.date for o in result.observations] == ["2020-01-02", "2024-06-28"]
        assert result.missing_dates == ["2024-07-01"]

    def test_interior_gap_not_listed(self, offline):
        """Interior holes are not enumerated (documented limitation); strict=True refuses them."""
        offline._cache.put_observations(_usd([("2024-01-02", 1.0)]), DESDE, "2024-03-31")
        offline._cache.put_observations(_usd([("2024-12-30", 2.0)]), "2024-10-01", HASTA)
        offline.stub_error = BcchApiError("BCCh API is down")

        result = offline.get(Series.USD, DESDE, HASTA)

        assert result.missing_dates == []
        assert [o.date for o in result.observations] == ["2024-01-02", "2024-12-30"]


class TestStrictMode:
    """strict=True refuses partial windows instead of serving them."""

    def test_strict_defaults_to_false(self):
        import inspect

        param = inspect.signature(OfflineClient.__init__).parameters["strict"]
        assert param.default is False

    def test_strict_partial_raises(self, strict_offline):
        strict_offline._cache.put_observations(
            _usd([("2024-03-01", 1.0)]), "2024-03-01", HASTA
        )

        with pytest.raises(BcchOfflineError) as excinfo:
            strict_offline.get(Series.USD, DESDE, HASTA)

        assert excinfo.value.context["cache_had_data"] is True
        assert excinfo.value.context["missing_dates"] == [DESDE]

    def test_strict_interior_gap_raises(self, strict_offline):
        strict_offline._cache.put_observations(_usd([("2024-01-02", 1.0)]), DESDE, "2024-03-31")
        strict_offline._cache.put_observations(_usd([("2024-12-30", 2.0)]), "2024-10-01", HASTA)

        with pytest.raises(BcchOfflineError):
            strict_offline.get(Series.USD, DESDE, HASTA)

    def test_strict_full_coverage_served(self, strict_offline, sample_result):
        strict_offline._cache.put_observations(sample_result, DESDE, HASTA)
        _set_age(strict_offline, Series.USD.value, days=2)

        result = strict_offline.get(Series.USD, DESDE, HASTA)

        assert result.observations == sample_result.observations
        assert result.stale is True
        assert result.missing_dates == []

    def test_strict_empty_cache_still_cache_had_data_false(self, strict_offline):
        with pytest.raises(BcchOfflineError) as excinfo:
            strict_offline.get(Series.USD, DESDE, HASTA)
        assert excinfo.value.context["cache_had_data"] is False


class TestNoDeprecatedCalls:
    """Phase 2: the client no longer goes through the deprecated key shims."""

    def test_no_deprecation_warning(self, offline, sample_result):
        import warnings

        offline.stub_result = sample_result
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            offline.get(Series.USD, DESDE, HASTA)  # success path
            offline.stub_result = None
            offline.stub_error = BcchApiError("BCCh API is down")
            offline.get(Series.USD, DESDE, HASTA)  # fallback path
