"""
Tests for econchile.cache — the v0.3 observation-level SQLite store.

Spec: specs/v0_3_cache_architecture.md (§2 schema, §4 freshness, §6 API).
Migration from the v0.2 ``cache`` table lives in tests/test_cache_migration.py.

Every test passes an explicit ``db_path`` (pytest's ``tmp_path``) so the
real ``~/.econchile/cache.db`` is never touched.  There is NO sleep()
anywhere — age is exercised with ``ttl_seconds=0`` or by rewriting
``fetched_at`` to an explicit old timestamp.

Run with:
    pytest tests/test_cache.py -v
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

# Add project root to path so we can import econchile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.cache import Cache, make_key
from econchile.series_map import Series
from econchile.types import BcchCacheError, Observation, SeriesResult

USD = Series.USD.value
UF = Series.UF.value


@pytest.fixture
def db(tmp_path):
    return tmp_path / "test_cache.db"


@pytest.fixture
def cache(db):
    """A Cache instance on a temp DB (never touches ~/.econchile)."""
    return Cache(db_path=db)


@pytest.fixture
def sample_result():
    """A typical SeriesResult to store (two USD observations)."""
    return SeriesResult(
        series=Series.USD,
        observations=[
            Observation(date="2024-01-01", value=897.68),
            Observation(date="2024-01-02", value=901.13),
        ],
        fetched_at=datetime(2024, 1, 3, 10, 0, 0, tzinfo=timezone.utc),
        source="api",
        metadata={"series_id": USD, "descripEsp": "Tipo de cambio nominal"},
    )


# ─── Helpers ───────────────────────────────────────────────────────────

def _result(series, points, metadata=None):
    """Build a SeriesResult from ``[(date, value), ...]``."""
    return SeriesResult(
        series=series,
        observations=[Observation(d, v) for d, v in points],
        fetched_at=datetime.now(timezone.utc),
        source="api",
        metadata=metadata if metadata is not None else {},
    )


def _iso_ago(**delta):
    """Aware-UTC ISO timestamp ``delta`` in the past."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="microseconds")


def _set_age(db, series, **delta):
    """Make every stored row of ``series`` look ``delta`` old (no sleep)."""
    stamp = _iso_ago(**delta)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE observations SET fetched_at = ? WHERE series = ?", (stamp, series))
        conn.execute("UPDATE fetched_windows SET fetched_at = ? WHERE series = ?", (stamp, series))
        conn.execute("UPDATE series_meta SET updated_at = ? WHERE series = ?", (stamp, series))
        conn.commit()
    finally:
        conn.close()


def _count(db, table):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ─── put_observations / get_observations ───────────────────────────────

class TestPutGetObservations:

    def test_roundtrip(self, cache, sample_result):
        """put_observations then get_observations returns the same points."""
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        got = cache.get_observations(USD, "2024-01-01", "2024-01-02")
        assert got == sample_result.observations
        assert all(isinstance(o, Observation) for o in got)

    def test_accepts_enum_or_code(self, cache, sample_result):
        """The series argument may be the enum member or its code string."""
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        assert cache.get_observations(Series.USD, "2024-01-01", "2024-01-02") == (
            cache.get_observations(USD, "2024-01-01", "2024-01-02")
        )

    def test_window_filtering(self, cache):
        """Only observations inside [desde, hasta] (inclusive) come back."""
        days = [f"2024-01-0{d}" for d in range(1, 6)]
        cache.put_observations(_result(Series.USD, [(d, 1.0) for d in days]))
        got = cache.get_observations(USD, "2024-01-02", "2024-01-04")
        assert [o.date for o in got] == ["2024-01-02", "2024-01-03", "2024-01-04"]

    def test_ordered_by_date(self, cache):
        cache.put_observations(
            _result(Series.USD, [("2024-01-03", 3.0), ("2024-01-01", 1.0), ("2024-01-02", 2.0)])
        )
        got = cache.get_observations(USD, "2024-01-01", "2024-01-03")
        assert [o.date for o in got] == ["2024-01-01", "2024-01-02", "2024-01-03"]

    def test_nd_row_distinct_from_never_fetched(self, cache):
        """ND (value None) is returned as a row; a never-fetched date is absent."""
        cache.put_observations(_result(Series.USD, [("2024-01-06", None), ("2024-01-08", 905.0)]))
        got = cache.get_observations(USD, "2024-01-06", "2024-01-08")
        assert got == [Observation("2024-01-06", None), Observation("2024-01-08", 905.0)]
        assert "2024-01-07" not in [o.date for o in got]  # never fetched

    def test_upsert_newest_value_wins(self, cache):
        cache.put_observations(_result(Series.USD, [("2024-01-02", 2.0)]))
        cache.put_observations(_result(Series.USD, [("2024-01-02", 20.0)]))
        assert cache.get_observations(USD, "2024-01-02", "2024-01-02") == [
            Observation("2024-01-02", 20.0)
        ]
        assert cache.size() == 1

    def test_dict_observations_accepted(self, cache):
        """Observations given as plain dicts (as SeriesResult.to_dict allows) work."""
        result = SeriesResult(
            series=Series.USD,
            observations=[{"date": "2024-01-01", "value": 1.5}],
            fetched_at=datetime.now(timezone.utc),
        )
        cache.put_observations(result)
        assert cache.get_observations(USD, "2024-01-01", "2024-01-01") == [
            Observation("2024-01-01", 1.5)
        ]

    def test_raw_code_series(self, cache):
        """A code outside the indexed enum is stored and read under the raw code."""
        code = "F072.CLP.EUR.N.O.D"
        cache.put_observations(_result(code, [("2024-01-02", 1000.0)]))
        assert cache.get_observations(code, "2024-01-02", "2024-01-02") == [
            Observation("2024-01-02", 1000.0)
        ]

    def test_series_do_not_mix(self, cache):
        cache.put_observations(_result(Series.USD, [("2024-01-01", 900.0)]))
        cache.put_observations(_result(Series.UF, [("2024-01-01", 36000.0)]))
        assert cache.get_observations(UF, "2024-01-01", "2024-01-01") == [
            Observation("2024-01-01", 36000.0)
        ]

    def test_fetched_at_stored_as_aware_utc(self, cache, db, sample_result):
        """New writes store now() in aware UTC, not the result's timestamp."""
        cache.put_observations(sample_result)
        conn = sqlite3.connect(str(db))
        try:
            (value,) = conn.execute("SELECT fetched_at FROM observations LIMIT 1").fetchone()
        finally:
            conn.close()
        stamp = datetime.fromisoformat(value)
        assert value.endswith("+00:00")
        assert abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=1)


# ─── Freshness (§4) ────────────────────────────────────────────────────

class TestFreshness:

    def test_fresh_rows_served(self, cache, sample_result):
        cache.put_observations(sample_result)
        assert len(cache.get_observations(USD, "2024-01-01", "2024-01-02")) == 2

    def test_stale_rows_excluded_unless_requested(self, tmp_path, sample_result):
        """ttl_seconds=0: rows are stale at once — hidden by default, shown with include_stale."""
        cache = Cache(db_path=tmp_path / "stale.db", ttl_seconds=0)
        cache.put_observations(sample_result)
        assert cache.get_observations(USD, "2024-01-01", "2024-01-02") == []
        assert cache.get_observations(
            USD, "2024-01-01", "2024-01-02", include_stale=True
        ) == sample_result.observations

    def test_two_day_old_rows_are_stale_not_expired(self, cache, db, sample_result):
        cache.put_observations(sample_result)
        _set_age(db, USD, days=2)
        assert cache.get_observations(USD, "2024-01-01", "2024-01-02") == []
        assert len(cache.get_observations(USD, "2024-01-01", "2024-01-02", include_stale=True)) == 2

    def test_expired_rows_never_served(self, cache, db, sample_result):
        """Older than max_stale_age (7 days): not served even with include_stale."""
        cache.put_observations(sample_result)
        _set_age(db, USD, days=8)
        assert cache.get_observations(USD, "2024-01-01", "2024-01-02", include_stale=True) == []
        assert cache.size() == 2  # still on disk until purge age


# ─── covers (§5.1) ─────────────────────────────────────────────────────

class TestCovers:

    def test_same_window_covered(self, cache, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-31")
        assert cache.covers(USD, "2024-01-01", "2024-01-31") is True

    def test_sub_window_covered(self, cache, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-31")
        assert cache.covers(USD, "2024-01-10", "2024-01-20") is True

    def test_wider_window_not_covered(self, cache, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-31")
        assert cache.covers(USD, "2023-12-31", "2024-01-31") is False
        assert cache.covers(USD, "2024-01-01", "2024-02-01") is False

    def test_adjacent_windows_merge(self, cache):
        cache.put_observations(_result(Series.USD, [("2023-06-01", 1.0)]), "2023-01-01", "2023-12-31")
        cache.put_observations(_result(Series.USD, [("2024-06-01", 2.0)]), "2024-01-01", "2024-12-31")
        assert cache.covers(USD, "2023-01-01", "2024-12-31") is True

    def test_gap_between_windows_not_covered(self, cache):
        """Fetched 2020 and 2024 separately: 2020→2024 is NOT covered (no silent hole)."""
        cache.put_observations(_result(Series.USD, [("2020-01-02", 1.0)]), "2020-01-01", "2020-12-31")
        cache.put_observations(_result(Series.USD, [("2024-12-31", 2.0)]), "2024-01-01", "2024-12-31")
        assert cache.covers(USD, "2020-01-01", "2024-12-31") is False

    def test_monthly_window_ending_month_end_covered(self, cache):
        """IPC dated on the 1st: a fetched 2024-01-01→2024-12-31 window is covered."""
        code = Series.IPC_VAR.value
        points = [(f"2024-{m:02d}-01", 0.3) for m in range(1, 13)]
        cache.put_observations(_result(Series.IPC_VAR, points), "2024-01-01", "2024-12-31")
        assert cache.covers(code, "2024-01-01", "2024-12-31") is True

    def test_observations_without_window_do_not_cover(self, cache, sample_result):
        cache.put_observations(sample_result)  # no window recorded
        assert cache.covers(USD, "2024-01-01", "2024-01-02") is False

    def test_stale_window_needs_include_stale(self, tmp_path, sample_result):
        cache = Cache(db_path=tmp_path / "stale.db", ttl_seconds=0)
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        assert cache.covers(USD, "2024-01-01", "2024-01-02") is False
        assert cache.covers(USD, "2024-01-01", "2024-01-02", include_stale=True) is True

    def test_expired_window_never_covers(self, cache, db, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        _set_age(db, USD, days=8)
        assert cache.covers(USD, "2024-01-01", "2024-01-02", include_stale=True) is False

    def test_window_of_other_series_does_not_cover(self, cache, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        assert cache.covers(UF, "2024-01-01", "2024-01-02") is False


# ─── load ──────────────────────────────────────────────────────────────

class TestLoad:

    def test_load_reconstructs_result(self, cache, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        result = cache.load(USD, "2024-01-01", "2024-01-02")
        assert isinstance(result, SeriesResult)
        assert result.series is Series.USD
        assert result.observations == sample_result.observations
        assert result.metadata == sample_result.metadata
        assert result.source == "api"

    def test_load_fetched_at_is_newest_served_observation(self, cache, db):
        cache.put_observations(_result(Series.USD, [("2024-01-01", 1.0), ("2024-01-02", 2.0)]))
        old = _iso_ago(hours=3)
        new = _iso_ago(hours=1)
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE observations SET fetched_at = ? WHERE date = '2024-01-01'", (old,))
            conn.execute("UPDATE observations SET fetched_at = ? WHERE date = '2024-01-02'", (new,))
            conn.commit()
        finally:
            conn.close()
        result = cache.load(USD, "2024-01-01", "2024-01-02")
        assert result.fetched_at == datetime.fromisoformat(new)
        assert result.fetched_at.tzinfo is not None

    def test_load_raw_code_returns_string_series(self, cache):
        code = "F072.CLP.EUR.N.O.D"
        cache.put_observations(_result(code, [("2024-01-02", 1000.0)]))
        result = cache.load(code, "2024-01-02", "2024-01-02")
        assert result.series == code
        assert not isinstance(result.series, Series)

    def test_load_without_metadata_gives_empty_dict(self, cache):
        cache.put_observations(_result(Series.USD, [("2024-01-01", 1.0)]))
        assert cache.load(USD, "2024-01-01", "2024-01-01").metadata == {}

    def test_load_metadata_updated_by_newer_fetch(self, cache):
        cache.put_observations(_result(Series.USD, [("2024-01-01", 1.0)], {"descripEsp": "old"}))
        cache.put_observations(_result(Series.USD, [("2024-01-02", 2.0)], {"descripEsp": "new"}))
        assert cache.load(USD, "2024-01-01", "2024-01-02").metadata == {"descripEsp": "new"}

    def test_load_empty_window_returns_empty_observations(self, cache):
        result = cache.load(USD, "2024-01-01", "2024-01-31")
        assert result.observations == []

    def test_load_fresh_only_by_default(self, tmp_path, sample_result):
        cache = Cache(db_path=tmp_path / "stale.db", ttl_seconds=0)
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        assert cache.load(USD, "2024-01-01", "2024-01-02").observations == []
        assert cache.load(
            USD, "2024-01-01", "2024-01-02", include_stale=True
        ).observations == sample_result.observations


# ─── purge (§6) ────────────────────────────────────────────────────────

class TestPurge:

    def test_purge_deletes_only_rows_past_purge_age(self, cache, db):
        cache.put_observations(_result(Series.USD, [("2019-01-02", 1.0), ("2019-01-03", 2.0)],
                                       {"series_id": USD}), "2019-01-01", "2019-01-31")
        cache.put_observations(_result(Series.UF, [("2024-01-01", 36000.0)],
                                       {"series_id": UF}), "2024-01-01", "2024-01-01")
        _set_age(db, USD, days=6 * 365)

        removed = cache.purge()

        assert removed == 2
        assert cache.get_observations(UF, "2024-01-01", "2024-01-01") == [
            Observation("2024-01-01", 36000.0)
        ]
        conn = sqlite3.connect(str(db))
        try:
            meta_series = {r[0] for r in conn.execute("SELECT series FROM series_meta")}
            window_series = {r[0] for r in conn.execute("SELECT series FROM fetched_windows")}
        finally:
            conn.close()
        assert meta_series == {UF}  # orphaned USD metadata removed
        assert window_series == {UF}  # purged windows removed too

    def test_purge_keeps_stale_but_young_rows(self, cache, db, sample_result):
        cache.put_observations(sample_result)
        _set_age(db, USD, days=30)
        assert cache.purge() == 0
        assert cache.size() == 2

    def test_purge_keeps_recent_nd_rows(self, cache):
        cache.put_observations(_result(Series.USD, [("2024-01-06", None)]))
        cache.purge()
        assert cache.get_observations(USD, "2024-01-06", "2024-01-06") == [
            Observation("2024-01-06", None)
        ]

    def test_purge_updates_last_purge_at(self, cache, db):
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'last_purge_at'", (_iso_ago(days=3),)
            )
            conn.commit()
        finally:
            conn.close()
        cache.purge()
        conn = sqlite3.connect(str(db))
        try:
            (value,) = conn.execute("SELECT value FROM meta WHERE key = 'last_purge_at'").fetchone()
        finally:
            conn.close()
        assert abs(datetime.now(timezone.utc) - datetime.fromisoformat(value)) < timedelta(minutes=1)

    def test_purge_runs_on_init(self, db, sample_result):
        Cache(db_path=db).put_observations(sample_result)
        _set_age(db, USD, days=6 * 365)
        reopened = Cache(db_path=db)
        assert reopened.size() == 0


# ─── clear / size ──────────────────────────────────────────────────────

class TestClearAndSize:

    def test_size_starts_zero(self, cache):
        assert cache.size() == 0

    def test_size_counts_observation_rows(self, cache, sample_result):
        cache.put_observations(sample_result)
        assert cache.size() == 2

    def test_clear_returns_observation_count(self, cache, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        cache.put_observations(_result(Series.UF, [("2024-01-01", 36000.0)]))
        assert cache.size() == 3
        assert cache.clear() == 3
        assert cache.clear() == 0

    def test_clear_wipes_everything(self, cache, db, sample_result):
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        cache.clear()
        assert cache.size() == 0
        assert _count(db, "series_meta") == 0
        assert _count(db, "fetched_windows") == 0
        assert cache.covers(USD, "2024-01-01", "2024-01-02") is False
        assert _count(db, "meta") >= 1  # schema bookkeeping survives


# ─── :memory: ──────────────────────────────────────────────────────────

class TestMemoryCache:

    def test_memory_cache_roundtrip(self, sample_result):
        cache = Cache(db_path=":memory:")
        cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        assert cache.get_observations(USD, "2024-01-01", "2024-01-02") == sample_result.observations
        assert cache.covers(USD, "2024-01-01", "2024-01-02") is True
        assert cache.load(USD, "2024-01-01", "2024-01-02").series is Series.USD

    def test_memory_cache_size_and_clear(self, sample_result):
        cache = Cache(db_path=":memory:")
        assert cache.size() == 0
        cache.put_observations(sample_result)
        assert cache.size() == 2
        assert cache.clear() == 2
        assert cache.size() == 0


# ─── Deprecated key-based shims (§6) ───────────────────────────────────

class TestDeprecatedShims:

    def test_make_key_warns_and_still_builds_key(self):
        with pytest.deprecated_call():
            key = make_key(Series.USD, "2024-01-01", "2024-01-05")
        assert key == f"{USD}|2024-01-01|2024-01-05"

    def test_set_and_get_warn_and_still_work(self, cache, sample_result):
        key = f"{USD}|2024-01-01|2024-01-02"
        with pytest.deprecated_call():
            cache.set(key, sample_result)
        with pytest.deprecated_call():
            got = cache.get(key)
        assert got is not None
        assert got.series is Series.USD
        assert got.observations == sample_result.observations
        assert got.metadata == sample_result.metadata

    def test_series_shims_warn_and_accept_enum_or_code(self, cache, sample_result):
        with pytest.deprecated_call():
            cache.set_series(Series.USD, "2024-01-01", "2024-01-02", sample_result)
        with pytest.deprecated_call():
            got = cache.get_series(USD, "2024-01-01", "2024-01-02")
        assert got is not None
        assert got.series is Series.USD

    def test_get_unknown_key_returns_none(self, cache):
        with pytest.deprecated_call():
            assert cache.get(f"{USD}|2024-01-01|2024-01-02") is None

    def test_get_malformed_key_returns_none(self, cache):
        with pytest.deprecated_call():
            assert cache.get("not-a-key") is None

    def test_get_does_not_serve_stale(self, tmp_path, sample_result):
        cache = Cache(db_path=tmp_path / "stale.db", ttl_seconds=0)
        with pytest.deprecated_call():
            cache.set_series(Series.USD, "2024-01-01", "2024-01-02", sample_result)
        with pytest.deprecated_call():
            assert cache.get_series(Series.USD, "2024-01-01", "2024-01-02") is None


# ─── Errors (§9) ───────────────────────────────────────────────────────

class TestErrors:

    def test_invalid_db_path_raises(self, tmp_path):
        """Opening a cache on a path that can't be a DB raises BcchCacheError."""
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")
        with pytest.raises(BcchCacheError):
            Cache(db_path=blocker / "cache.db")

    def test_sqlite_failure_on_read_raises_cache_error(self, cache, db):
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DROP TABLE observations")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(BcchCacheError):
            cache.get_observations(USD, "2024-01-01", "2024-01-02")
        with pytest.raises(BcchCacheError):
            cache.size()

    def test_sqlite_failure_on_write_raises_cache_error(self, cache, db, sample_result):
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DROP TABLE observations")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(BcchCacheError):
            cache.put_observations(sample_result, "2024-01-01", "2024-01-02")

    def test_failed_put_writes_nothing(self, cache, db, sample_result):
        """put_observations is one transaction: a failure leaves no partial rows."""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DROP TABLE fetched_windows")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(BcchCacheError):
            cache.put_observations(sample_result, "2024-01-01", "2024-01-02")
        assert _count(db, "observations") == 0
        assert _count(db, "series_meta") == 0
