"""
Tests for econchile.cache — SQLite-backed cache for BCCh API responses.

Every test passes an explicit ``db_path`` (pytest's ``tmp_path``) so the
real ``~/.econchile/cache.db`` is never touched.  There is NO sleep()
anywhere — expiry is exercised with ``ttl_seconds=0``.

Run with:
    pytest tests/test_cache.py -v
"""

import os
import sqlite3
import sys
from datetime import datetime

import pytest

# Add project root to path so we can import econchile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.cache import Cache, make_key
from econchile.series_map import Series
from econchile.types import BcchCacheError, Frequency, Observation, SeriesResult


@pytest.fixture
def cache(tmp_path):
    """A Cache instance on a temp DB (never touches ~/.econchile)."""
    return Cache(db_path=tmp_path / "test_cache.db")


@pytest.fixture
def sample_result():
    """A typical SeriesResult to store."""
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


class TestMakeKey:
    """make_key() helper."""

    def test_joins_with_pipes(self):
        """make_key('A', '2024-01-01', '2024-12-31') == 'A|2024-01-01|2024-12-31'"""
        assert make_key("A", "2024-01-01", "2024-12-31") == "A|2024-01-01|2024-12-31"

    def test_different_ranges_different_keys(self):
        """Same series, different date ranges → different keys."""
        k1 = make_key("F073.TCO.PRE.Z.D", "2024-01-01", "2024-12-31")
        k2 = make_key("F073.TCO.PRE.Z.D", "2024-02-01", "2024-12-31")
        assert k1 != k2

    def test_make_key_normalizes_enum(self):
        """make_key(Series.USD, ...) uses the enum's code, not its repr."""
        assert make_key(Series.USD, "2024-01-01", "2024-01-05") == (
            "F073.TCO.PRE.Z.D|2024-01-01|2024-01-05"
        )

    def test_make_key_enum_matches_string(self):
        """Enum and raw-code forms of the same series produce the same key."""
        assert make_key(Series.USD, "2024-01-01", "2024-01-05") == make_key(
            "F073.TCO.PRE.Z.D", "2024-01-01", "2024-01-05"
        )


class TestSetGet:
    """Basic set/get roundtrip."""

    def test_set_then_get_returns_result(self, cache, sample_result):
        """set() then get() returns an equivalent SeriesResult."""
        cache.set("k", sample_result)
        result = cache.get("k")
        assert result is not None
        assert result.series == sample_result.series
        assert result.observations == sample_result.observations
        assert result.fetched_at == sample_result.fetched_at
        assert result.metadata == sample_result.metadata

    def test_get_missing_returns_none(self, cache):
        """get() on a never-set key returns None."""
        assert cache.get("never-set") is None

    def test_observations_reconstructed_as_objects(self, cache, sample_result):
        """After get(), observations are Observation instances, not dicts."""
        cache.set("k", sample_result)
        result = cache.get("k")
        assert all(isinstance(obs, Observation) for obs in result.observations)
        assert result.observations[0].date == "2024-01-01"
        assert result.observations[0].value == 897.68

    def test_series_reconstructed_as_enum(self, cache, sample_result):
        """After get(), series is the Series enum member, not a string."""
        cache.set("k", sample_result)
        result = cache.get("k")
        assert result.series is Series.USD

    def test_source_preserved_from_stored(self, cache, sample_result):
        """After get(), source is preserved from the stored payload.

        The spec says "keep as stored" — the reconstructed result carries
        the same source as the stored payload (here "api").
        """
        cache.set("k", sample_result)
        result = cache.get("k")
        assert result.source == sample_result.source

    def test_metadata_preserved(self, cache, sample_result):
        """metadata dict survives the roundtrip unchanged."""
        cache.set("k", sample_result)
        result = cache.get("k")
        assert result.metadata == {"series_id": "F073.TCO.PRE.Z.D"}

    def test_overwrite_updates(self, cache, sample_result):
        """set() twice with same key → get() returns latest."""
        cache.set("k", sample_result)
        newer = SeriesResult(
            series=Series.USD,
            observations=[Observation(date="2024-02-01", value=999.99)],
            fetched_at=datetime(2024, 2, 1, 9, 0, 0),
            source="api",
            metadata={"series_id": "F073.TCO.PRE.Z.D"},
        )
        cache.set("k", newer)
        result = cache.get("k")
        assert len(result.observations) == 1
        assert result.observations[0].value == 999.99
        assert result.fetched_at == datetime(2024, 2, 1, 9, 0, 0)

    def test_unknown_series_code_falls_back_to_string(self, cache):
        """A stored code not in the v0.1 catalog comes back as the raw string."""
        unknown = SeriesResult(
            series="ZZZ.NOT.IN.CATALOG",
            observations=[Observation(date="2024-01-01", value=1.0)],
            fetched_at=datetime(2024, 1, 1),
            source="api",
        )
        cache.set("k", unknown)
        result = cache.get("k")
        assert result.series == "ZZZ.NOT.IN.CATALOG"
        assert not isinstance(result.series, Series)


class TestExpiry:
    """TTL logic."""

    def test_fresh_entry_not_expired(self, cache, sample_result):
        """get() within TTL returns data."""
        cache.set("k", sample_result)
        assert cache.get("k") is not None

    def test_expired_entry_returns_none(self, tmp_path, sample_result):
        """get() after TTL passed returns None."""
        # ttl_seconds=0 → expires_at == fetched_at (the set moment), so the
        # row is already expired by the time get() runs.  No sleep() needed.
        cache = Cache(db_path=tmp_path / "expired.db", ttl_seconds=0)
        cache.set("k", sample_result)
        assert cache.get("k") is None

    def test_expired_entry_deleted(self, tmp_path, sample_result):
        """Expired row is deleted from DB, not just hidden."""
        cache = Cache(db_path=tmp_path / "expired_delete.db", ttl_seconds=0)
        cache.set("k", sample_result)
        assert cache.size() == 1
        assert cache.get("k") is None
        assert cache.size() == 0
        # Verify at the SQL level that the row is really gone.
        conn = sqlite3.connect(tmp_path / "expired_delete.db")
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE key = 'k'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0


class TestKeyHelpers:
    """get_series / set_series convenience methods."""

    def test_set_series_then_get_series(self, cache, sample_result):
        """set_series(Series.USD, ...) then get_series(Series.USD, ...) roundtrips."""
        cache.set_series(Series.USD, "2024-01-01", "2024-01-31", sample_result)
        result = cache.get_series(Series.USD, "2024-01-01", "2024-01-31")
        assert result is not None
        assert result.series is Series.USD

    def test_set_series_accepts_enum_or_string(self, cache, sample_result):
        """set_series accepts both Series enum and raw code string."""
        cache.set_series(Series.USD, "2024-01-01", "2024-01-31", sample_result)
        # The raw code string resolves to the SAME key as the enum member.
        result = cache.get_series("F073.TCO.PRE.Z.D", "2024-01-01", "2024-01-31")
        assert result is not None
        assert result.series is Series.USD


class TestClearAndSize:
    """Cache management."""

    def test_size_starts_zero(self, cache):
        """Fresh cache has size 0."""
        assert cache.size() == 0

    def test_size_after_set(self, cache, sample_result):
        """size() == 1 after one set."""
        cache.set("k", sample_result)
        assert cache.size() == 1

    def test_clear_empties_cache(self, cache, sample_result):
        """clear() deletes all rows and size() == 0."""
        cache.set("k1", sample_result)
        cache.set("k2", sample_result)
        assert cache.size() == 2
        cache.clear()
        assert cache.size() == 0

    def test_clear_returns_count(self, cache, sample_result):
        """clear() returns number of deleted rows."""
        cache.set("k1", sample_result)
        cache.set("k2", sample_result)
        cache.set("k3", sample_result)
        assert cache.clear() == 3
        assert cache.clear() == 0


class TestMemoryCache:
    """Cache(db_path=\":memory:\") — schema lives inside one connection."""

    def test_memory_cache_set_get_roundtrip(self, sample_result):
        """set() then get() on an in-memory cache returns the result."""
        cache = Cache(db_path=":memory:")
        cache.set("k", sample_result)
        result = cache.get("k")
        assert result is not None
        assert result.series is Series.USD
        assert result.observations == sample_result.observations

    def test_memory_cache_size(self, sample_result):
        """size() reflects entries stored in an in-memory cache."""
        cache = Cache(db_path=":memory:")
        assert cache.size() == 0
        cache.set("k", sample_result)
        assert cache.size() == 1

    def test_memory_cache_get_missing_returns_none(self):
        """get() on an unknown key in an in-memory cache returns None."""
        cache = Cache(db_path=":memory:")
        assert cache.get("never-set") is None

    def test_memory_cache_clear(self, sample_result):
        """clear() empties an in-memory cache and returns the row count."""
        cache = Cache(db_path=":memory:")
        cache.set("k1", sample_result)
        cache.set("k2", sample_result)
        assert cache.size() == 2
        assert cache.clear() == 2
        assert cache.size() == 0
        assert cache.get("k1") is None


class TestErrors:
    """BcchCacheError raised on real failures."""

    def test_invalid_db_path_raises(self, tmp_path):
        """Opening a cache on a path that can't be a DB raises BcchCacheError."""
        # A regular file pretending to be a directory: the parent mkdir()
        # (or sqlite connect) must fail, and __init__ must translate that
        # into BcchCacheError instead of letting the raw error escape.
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")
        bad_path = blocker / "cache.db"
        with pytest.raises(BcchCacheError):
            Cache(db_path=bad_path)
