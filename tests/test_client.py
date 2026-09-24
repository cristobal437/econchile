"""
Tests for econchile.client — the user-facing BcchClient.

NEVER hits the real BCCh API: every test monkeypatches
``Fetcher.get_series`` (class-level, so the client's internal instance is
affected) or the Cache methods.  Every test passes an explicit token and
a tmp_path db, so ``~/.econchile`` is never touched.

Run with:
    pytest tests/test_client.py -v
"""

import os
import sys
from datetime import datetime

import pytest

# Add project root to path so we can import econchile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.client import BcchClient
from econchile.types import SeriesResult, SeriesMeta, Observation, Frequency, Representation
from econchile.series_map import Series


@pytest.fixture
def fake_series_result():
    """A SeriesResult that a fake fetcher returns."""
    from datetime import datetime
    return SeriesResult(
        series=Series.USD,
        observations=[Observation(date="2024-01-01", value=897.68)],
        fetched_at=datetime(2024, 1, 3, 10, 0, 0),
        source="api",
        metadata={"series_id": "F073.TCO.PRE.Z.D"},
    )


@pytest.fixture
def client(tmp_path, token="test-token"):
    """A BcchClient with temp cache and explicit token (never real API)."""
    return BcchClient(token=token, db_path=tmp_path / "client_test.db")


# ─── Helpers ───────────────────────────────────────────────────────────

def make_fake_fetcher(result, calls):
    """Return a Fetcher.get_series replacement that records its calls.

    ``calls`` is a list that receives ``(series, desde, hasta)`` tuples.
    The returned function is a plain function on the class, so the first
    argument is the instance (self), exactly like a real method.
    """

    def fake_get_series(self, series, desde, hasta):
        calls.append((series, desde, hasta))
        return result

    return fake_get_series


def raise_if_called(message):
    """Return a callable that fails the test if invoked."""

    def raiser(*args, **kwargs):
        pytest.fail(message)

    return raiser


class TestGet:
    """client.get() — cache-first data flow."""

    def test_cache_hit_skips_fetcher(self, client, fake_series_result, monkeypatch):
        """Cached data returned WITHOUT calling fetcher (fetcher raises if called)."""
        # Seed the cache with the SAME key the client will compute:
        # make_key(Series.USD.value, desde, hasta).  set_series builds
        # exactly that key from the same components.
        client._cache.set_series(Series.USD, "2024-01-01", "2024-12-31", fake_series_result)

        # If the fetcher is ever called, the test fails loudly.
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            raise_if_called("fetcher must not be called on a cache hit"),
        )

        result = client.get(Series.USD, "2024-01-01", "2024-12-31")

        # The cached result round-trips through SQLite unchanged (frozen
        # dataclass equality: series enum, observations, fetched_at,
        # source, metadata all preserved by Cache._reconstruct).
        assert result == fake_series_result
        assert result.series is Series.USD
        assert result.observations == fake_series_result.observations
        assert result.metadata == {"series_id": "F073.TCO.PRE.Z.D"}

    def test_cache_miss_calls_fetcher(self, client, fake_series_result, monkeypatch):
        """Miss → fetcher called, result stored in cache."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(fake_series_result, calls),
        )

        result = client.get(Series.USD, "2024-01-01", "2024-12-31")

        assert len(calls) == 1
        assert calls[0][0] is Series.USD
        assert calls[0][1] == "2024-01-01"
        assert calls[0][2] == "2024-12-31"
        assert result == fake_series_result

        # The fresh result was stored: the cache now has exactly one
        # entry, retrievable under the client's key convention.
        assert client._cache.size() == 1
        stored = client._cache.get_series(Series.USD, "2024-01-01", "2024-12-31")
        assert stored is not None
        assert stored == fake_series_result

    def test_second_get_served_from_cache(self, client, fake_series_result, monkeypatch):
        """get() twice → fetcher called once, second served from cache."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(fake_series_result, calls),
        )

        first = client.get(Series.USD, "2024-01-01", "2024-12-31")
        second = client.get(Series.USD, "2024-01-01", "2024-12-31")

        assert len(calls) == 1  # fetcher ran exactly once
        assert first == second == fake_series_result

    def test_use_cache_false_bypasses(self, client, fake_series_result, monkeypatch):
        """use_cache=False → always calls fetcher, ignores cache."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(fake_series_result, calls),
        )
        # The cache must be completely ignored: any read or write fails.
        monkeypatch.setattr(
            client._cache, "get", raise_if_called("cache.get must not run with use_cache=False")
        )
        monkeypatch.setattr(
            client._cache, "set", raise_if_called("cache.set must not run with use_cache=False")
        )

        first = client.get(Series.USD, "2024-01-01", "2024-12-31", use_cache=False)
        second = client.get(Series.USD, "2024-01-01", "2024-12-31", use_cache=False)

        assert len(calls) == 2  # every call goes to the fetcher
        assert first == second == fake_series_result

    def test_bad_dates_raise_before_cache(self, client):
        """Invalid desde/hasta → ValueError, no cache/API interaction."""
        # Prove no cache AND no network is touched on bad input.
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(
                "econchile.fetcher.Fetcher.get_series",
                raise_if_called("fetcher must not be called for bad dates"),
            )
            monkeypatch.setattr(
                "econchile.cache.Cache.get",
                raise_if_called("cache.get must not be called for bad dates"),
            )
            monkeypatch.setattr(
                "econchile.cache.Cache.set",
                raise_if_called("cache.set must not be called for bad dates"),
            )

            with pytest.raises(ValueError):
                client.get(Series.USD, "01/2024", "2024-12-31")
            with pytest.raises(ValueError):
                client.get(Series.USD, "2024-01-01", "not-a-date")
        finally:
            monkeypatch.undo()

    def test_desde_after_hasta_raises_before_io(self, client, monkeypatch):
        """desde > hasta → ValueError, no cache/API interaction (v0.2.1)."""
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            raise_if_called("fetcher must not be called for an inverted window"),
        )
        monkeypatch.setattr(
            "econchile.cache.Cache.get",
            raise_if_called("cache.get must not be called for an inverted window"),
        )
        with pytest.raises(ValueError):
            client.get(Series.USD, "2024-12-31", "2024-01-01")


class TestSeriesResolution:
    """get()/search() accept names, enums, codes."""

    def test_accepts_enum(self, client, monkeypatch):
        """client.get(Series.USD, ...) works."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(_result_for(Series.USD), calls),
        )

        result = client.get(Series.USD, "2024-01-01", "2024-12-31")

        assert len(calls) == 1
        assert calls[0][0] is Series.USD
        assert result.series == Series.USD

    def test_accepts_lowercase_name(self, client, monkeypatch):
        """client.get('uf', ...) works (case-insensitive)."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(_result_for(Series.UF), calls),
        )

        result = client.get("uf", "2024-01-01", "2024-12-31")

        # The name resolved to the enum member BEFORE the fetcher call,
        # so the fetcher receives the canonical Series.UF.
        assert len(calls) == 1
        assert calls[0][0] is Series.UF
        assert result.series == Series.UF

    def test_accepts_code_string(self, client, monkeypatch):
        """client.get('F073.TCO.PRE.Z.D', ...) works."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(_result_for(Series.USD), calls),
        )

        result = client.get("F073.TCO.PRE.Z.D", "2024-01-01", "2024-12-31")

        assert len(calls) == 1
        assert calls[0][0] is Series.USD  # code resolved to the enum
        assert result.series == Series.USD

    def test_unknown_name_raises_key_error(self, client):
        """client.get('NONEXISTENT', ...) → KeyError with helpful message."""
        with pytest.raises(KeyError):
            client.get("NONEXISTENT", "2024-01-01", "2024-12-31")

    def test_key_error_lists_available(self, client):
        """KeyError message mentions available series (e.g. 'UF')."""
        with pytest.raises(KeyError) as excinfo:
            client.get("NONEXISTENT", "2024-01-01", "2024-12-31")

        message = str(excinfo.value)
        assert "NONEXISTENT" in message
        assert "UF" in message  # the catalog is listed, so it's actionable
        assert "IPC_VAR" in message
        assert "raw BCCh code" in message  # v0.2.1: points to the raw-code path

    def test_raw_code_outside_catalog_passes_through(self, client, monkeypatch):
        """Any BCCh code works, even outside the indexed enum (v0.2.1)."""
        code = "F072.CLP.EUR.N.O.D"  # CLP per EUR, not indexed
        raw_result = SeriesResult(
            series=code,
            observations=[Observation(date="2024-01-02", value=1000.0)],
            fetched_at=datetime(2024, 1, 3, 10, 0, 0),
            source="api",
        )
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(raw_result, calls),
        )

        result = client.get(code, "2024-01-01", "2024-01-31")

        assert len(calls) == 1
        assert calls[0][0] == code and not isinstance(calls[0][0], Series)
        assert result.series == code
        # Cached under the raw code: a second call never reaches the fetcher.
        stored = client._cache.get_series(code, "2024-01-01", "2024-01-31")
        assert stored is not None and stored.series == code
        client.get(code, "2024-01-01", "2024-01-31")
        assert len(calls) == 1

    def test_non_code_garbage_still_key_error(self, client):
        """Strings that are neither names nor code-shaped still raise KeyError."""
        for bad in ("dolar observado", "F07", "123.ABC"):
            with pytest.raises(KeyError):
                client.get(bad, "2024-01-01", "2024-12-31")


class TestSearch:
    """client.search() — catalog keyword search."""

    def test_search_ipc_returns_all_ipc_series(self, client):
        """search('ipc') → every IPC member of the indexed catalog."""
        results = client.search("ipc")

        ids = {m.series_id for m in results}
        assert ids == {
            Series.IPC_VAR.value,
            Series.IPC_INDEX.value,
            Series.IPC_ANUAL.value,
            Series.IPC_SAE.value,
            Series.IPC_EXPECTED.value,
        }

    def test_search_case_insensitive(self, client):
        """search('UF') == search('uf')."""
        upper = [m.series_id for m in client.search("UF")]
        lower = [m.series_id for m in client.search("uf")]

        assert upper == lower
        assert len(upper) > 0

    def test_search_dolar_matches_usd(self, client):
        """search('dolar') → includes USD."""
        results = client.search("dolar")

        assert any(m.series_id == Series.USD.value for m in results)
        # The accented spelling finds it too (same folded needle).
        assert any(m.series_id == Series.USD.value for m in client.search("dólar"))

    def test_search_no_match_empty(self, client):
        """search('xyzzy') → empty list."""
        assert client.search("xyzzy") == []

    def test_search_returns_series_meta_objects(self, client):
        """Every result is a SeriesMeta instance."""
        results = client.search("ipc")

        assert len(results) > 0
        assert all(isinstance(m, SeriesMeta) for m in results)


class TestListSeries:
    """client.list_series()."""

    def test_returns_all_indexed(self, client):
        """list_series() → all indexed SeriesMeta objects."""
        assert len(client.list_series()) == len(list(Series))

    def test_all_are_series_meta(self, client):
        """Every item is a SeriesMeta."""
        assert all(isinstance(m, SeriesMeta) for m in client.list_series())

    def test_ordered_like_enum(self, client):
        """Order matches Series enum iteration order (UF first)."""
        ids = [m.series_id for m in client.list_series()]

        assert ids == [s.value for s in Series]
        assert ids[0] == Series.UF.value


class TestClearCache:
    """client.clear_cache()."""

    def test_clear_returns_count(self, client):
        """clear_cache() → 0 on empty cache."""
        assert client.clear_cache() == 0

    def test_clear_after_get(self, client, fake_series_result, monkeypatch):
        """After get() stores data, clear_cache() removes it."""
        calls = []
        monkeypatch.setattr(
            "econchile.fetcher.Fetcher.get_series",
            make_fake_fetcher(fake_series_result, calls),
        )

        client.get(Series.USD, "2024-01-01", "2024-12-31")
        assert client._cache.size() == 1  # stored by the miss path

        assert client.clear_cache() == 1  # one row removed
        assert client._cache.size() == 0

        # End-to-end proof the data is really gone: a fresh get() must
        # go back to the fetcher (a cache hit would skip it).
        client.get(Series.USD, "2024-01-01", "2024-12-31")
        assert len(calls) == 2


# ─── Test helper: a result whose series matches the request ────────────

def _result_for(series):
    """Build a minimal SeriesResult tagged with ``series``."""
    return SeriesResult(
        series=series,
        observations=[Observation(date="2024-01-01", value=1.0)],
        fetched_at=datetime(2024, 1, 3, 10, 0, 0),
        source="api",
        metadata={"series_id": series.value},
    )
