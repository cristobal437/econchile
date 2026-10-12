"""
Tests for the v0.2 -> v1 cache migration (specs/v0_3_cache_architecture.md §3).

The migration is the riskiest part of v0.3: it rewrites a user's existing
``~/.econchile/cache.db``.  These tests build a legacy v0.2 ``cache`` table
BY HAND inside ``tmp_path``, open ``Cache`` on it, and assert exactly what
ended up in ``observations`` / ``series_meta`` / ``fetched_windows`` /
``meta``.  The real ``~/.econchile`` is never touched and nothing sleeps.

Run with:
    pytest tests/test_cache_migration.py -v
"""

import json
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta, timezone

import pytest

# Add project root to path so we can import econchile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.cache import Cache
from econchile.series_map import Series
from econchile.types import BcchCacheError, Observation

USD = Series.USD.value

# The exact v0.2 schema (econchile 0.2.x cache.py).
LEGACY_DDL = """
    CREATE TABLE cache (
        key        TEXT PRIMARY KEY,
        payload    TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
"""


# ─── Helpers ───────────────────────────────────────────────────────────

def _naive_ago(**delta):
    """A v0.2-style naive LOCAL timestamp string, ``delta`` in the past."""
    return (datetime.now() - timedelta(**delta)).isoformat()


def _expected_utc(naive_iso):
    """How the migration must normalise a legacy naive local timestamp."""
    return datetime.fromisoformat(naive_iso).astimezone().astimezone(timezone.utc)


def _payload(series, observations, metadata=None):
    """A v0.2-shaped payload JSON (what Cache._serialize used to write)."""
    return json.dumps(
        {
            "series": series,
            "observations": [{"date": d, "value": v} for d, v in observations],
            "fetched_at": "2024-01-03T10:00:00",
            "source": "api",
            "metadata": metadata if metadata is not None else {},
        }
    )


def _make_legacy_db(path, rows):
    """Create a v0.2 cache DB at ``path``.

    ``rows`` is a list of ``(key, payload, fetched_at)`` tuples; expires_at
    is fetched_at + 24h like v0.2 wrote it.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(LEGACY_DDL)
        for key, payload, fetched_at in rows:
            expires = (datetime.fromisoformat(fetched_at) + timedelta(days=1)).isoformat()
            conn.execute(
                "INSERT INTO cache (key, payload, fetched_at, expires_at) VALUES (?, ?, ?, ?)",
                (key, payload, fetched_at, expires),
            )
        conn.commit()
    finally:
        conn.close()


def _query(path, sql, params=()):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _tables(path):
    return {r[0] for r in _query(path, "SELECT name FROM sqlite_master WHERE type='table'")}


def _schema_version(path):
    rows = _query(path, "SELECT value FROM meta WHERE key = 'schema_version'")
    return rows[0][0] if rows else None


# ─── State machine (§3.1) ──────────────────────────────────────────────

class TestFreshDatabase:

    def test_fresh_db_gets_v1_schema(self, tmp_path):
        """A brand-new DB gets the four v1 tables and schema_version '1'."""
        db = tmp_path / "fresh.db"
        Cache(db_path=db)
        assert {"observations", "series_meta", "fetched_windows", "meta"} <= _tables(db)
        assert "cache" not in _tables(db)
        assert _schema_version(db) == "1"

    def test_fresh_db_seeds_last_purge_at_as_aware_utc(self, tmp_path):
        db = tmp_path / "fresh.db"
        Cache(db_path=db)
        (value,) = _query(db, "SELECT value FROM meta WHERE key = 'last_purge_at'")[0]
        stamp = datetime.fromisoformat(value)
        assert stamp.tzinfo is not None
        assert stamp.utcoffset() == timedelta(0)

    def test_memory_db_gets_v1_schema(self):
        """':memory:' still works (schema lives inside one connection)."""
        cache = Cache(db_path=":memory:")
        assert cache.size() == 0


class TestLegacyMigration:

    def test_legacy_v02_db_migrates(self, tmp_path):
        """A v0.2 cache row becomes observation rows; `cache` is dropped."""
        db = tmp_path / "legacy.db"
        fetched = _naive_ago(hours=1)
        _make_legacy_db(
            db,
            [(
                f"{USD}|2024-01-01|2024-01-02",
                _payload(USD, [("2024-01-01", 897.68), ("2024-01-02", 901.13)],
                         {"series_id": USD}),
                fetched,
            )],
        )

        Cache(db_path=db)

        assert "cache" not in _tables(db)
        assert _schema_version(db) == "1"
        rows = _query(db, "SELECT series, date, value FROM observations ORDER BY date")
        assert rows == [(USD, "2024-01-01", 897.68), (USD, "2024-01-02", 901.13)]

    def test_nd_value_preserved_as_null_row(self, tmp_path):
        """value=None (BCCh 'ND') migrates as a PRESENT row with NULL value."""
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [(
                f"{USD}|2024-01-06|2024-01-08",
                _payload(USD, [("2024-01-06", None), ("2024-01-08", 905.0)]),
                _naive_ago(hours=1),
            )],
        )

        Cache(db_path=db)

        rows = _query(db, "SELECT date, value FROM observations WHERE date = '2024-01-06'")
        assert rows == [("2024-01-06", None)]  # row exists, value IS NULL

    def test_metadata_migrated_to_series_meta(self, tmp_path):
        """Payload metadata lands in series_meta and round-trips as JSON."""
        db = tmp_path / "legacy.db"
        metadata = {
            "series_id": USD,
            "descripEsp": "Tipo de cambio nominal (dólar observado $CLP/USD)",
            "descripIng": "Nominal exchange rate",
            "series_infos": {"frequency": "DAILY"},
        }
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-01-01",
              _payload(USD, [("2024-01-01", 1.0)], metadata),
              _naive_ago(hours=1))],
        )

        Cache(db_path=db)

        (stored,) = _query(db, "SELECT metadata FROM series_meta WHERE series = ?", (USD,))[0]
        assert json.loads(stored) == metadata

    def test_window_migrated_from_legacy_key(self, tmp_path):
        """The legacy key `code|desde|hasta` becomes a fetched_windows row."""
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-12-31",
              _payload(USD, [("2024-01-01", 1.0)]),
              _naive_ago(hours=1))],
        )

        Cache(db_path=db)

        rows = _query(db, "SELECT series, desde, hasta FROM fetched_windows")
        assert rows == [(USD, "2024-01-01", "2024-12-31")]

    def test_recent_legacy_query_still_hits_after_upgrade(self, tmp_path):
        """A v0.2 entry inside its TTL still serves the same query (no API call)."""
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-12-31",
              _payload(USD, [("2024-01-01", 1.0), ("2024-12-31", 2.0)], {"series_id": USD}),
              _naive_ago(hours=1))],
        )

        cache = Cache(db_path=db)

        assert cache.covers(USD, "2024-01-01", "2024-12-31") is True
        result = cache.load(USD, "2024-01-01", "2024-12-31")
        assert result.series is Series.USD
        assert result.observations == [
            Observation("2024-01-01", 1.0),
            Observation("2024-12-31", 2.0),
        ]
        assert result.metadata == {"series_id": USD}

    def test_raw_code_series_migrates(self, tmp_path):
        """A series outside the indexed enum migrates under its raw code."""
        db = tmp_path / "legacy.db"
        code = "F072.CLP.EUR.N.O.D"
        _make_legacy_db(
            db,
            [(f"{code}|2024-01-02|2024-01-02",
              _payload(code, [("2024-01-02", 1000.0)]),
              _naive_ago(hours=1))],
        )

        cache = Cache(db_path=db)

        assert cache.get_observations(code, "2024-01-02", "2024-01-02") == [
            Observation("2024-01-02", 1000.0)
        ]

    def test_overlapping_windows_newest_wins(self, tmp_path):
        """Two legacy rows overlap on a date: the NEWER fetched_at wins.

        The newer row is inserted FIRST, so this proves the migration orders
        by fetched_at, not by insertion order.
        """
        db = tmp_path / "legacy.db"
        older = _naive_ago(hours=5)
        newer = _naive_ago(hours=1)
        _make_legacy_db(
            db,
            [
                (f"{USD}|2024-01-02|2024-01-03",
                 _payload(USD, [("2024-01-02", 20.0), ("2024-01-03", 3.0)]),
                 newer),
                (f"{USD}|2024-01-01|2024-01-02",
                 _payload(USD, [("2024-01-01", 1.0), ("2024-01-02", 2.0)]),
                 older),
            ],
        )

        Cache(db_path=db)  # must NOT fail verification on the overlap

        rows = _query(db, "SELECT date, value FROM observations ORDER BY date")
        assert rows == [("2024-01-01", 1.0), ("2024-01-02", 20.0), ("2024-01-03", 3.0)]

    def test_fetched_at_normalized_to_utc(self, tmp_path):
        """Legacy naive local fetched_at becomes aware UTC in every table."""
        db = tmp_path / "legacy.db"
        legacy = _naive_ago(hours=2)
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-01-01",
              _payload(USD, [("2024-01-01", 1.0)], {"series_id": USD}),
              legacy)],
        )

        Cache(db_path=db)

        expected = _expected_utc(legacy)
        for sql in (
            "SELECT fetched_at FROM observations",
            "SELECT updated_at FROM series_meta",
            "SELECT fetched_at FROM fetched_windows",
        ):
            (value,) = _query(db, sql)[0]
            stamp = datetime.fromisoformat(value)
            assert stamp.tzinfo is not None, sql
            assert stamp.utcoffset() == timedelta(0), sql
            assert value.endswith("+00:00"), sql
            assert stamp == expected, sql


class TestMigrationSafety:

    def test_verification_count_mismatch_rolls_back(self, tmp_path, monkeypatch):
        """A failed verification raises BcchCacheError and leaves v0.2 intact."""
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-01-02",
              _payload(USD, [("2024-01-01", 1.0), ("2024-01-02", 2.0)]),
              _naive_ago(hours=1))],
        )
        before = _query(db, "SELECT key, payload, fetched_at, expires_at FROM cache ORDER BY key")

        monkeypatch.setattr(Cache, "_migrated_observation_count", lambda self, conn: -1)

        with pytest.raises(BcchCacheError, match="original cache left intact"):
            Cache(db_path=db)

        # The legacy table is byte-identical and nothing new survived.
        after = _query(db, "SELECT key, payload, fetched_at, expires_at FROM cache ORDER BY key")
        assert after == before
        assert _tables(db) == {"cache"}

    def test_bad_payload_skipped_with_warning(self, tmp_path):
        """One undecodable payload is skipped + warned; the rest migrates."""
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [
                (f"{USD}|2024-01-01|2024-01-01", "this is {not json", _naive_ago(hours=2)),
                (f"{USD}|2024-01-02|2024-01-02",
                 _payload(USD, [("2024-01-02", 2.0)]),
                 _naive_ago(hours=1)),
            ],
        )

        with pytest.warns(UserWarning, match="skipped 1"):
            Cache(db_path=db)

        assert "cache" not in _tables(db)
        assert _query(db, "SELECT date, value FROM observations") == [("2024-01-02", 2.0)]

    def test_payload_without_observations_skipped(self, tmp_path):
        """Valid JSON that is not a v0.2 payload is skipped, not fatal."""
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [
                (f"{USD}|2024-01-01|2024-01-01", json.dumps({"series": USD}), _naive_ago(hours=2)),
                (f"{USD}|2024-01-02|2024-01-02",
                 _payload(USD, [("2024-01-02", 2.0)]),
                 _naive_ago(hours=1)),
            ],
        )

        with pytest.warns(UserWarning, match="skipped 1"):
            Cache(db_path=db)

        assert _query(db, "SELECT date FROM observations") == [("2024-01-02",)]

    def test_clean_migration_emits_no_warning(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-01-01",
              _payload(USD, [("2024-01-01", 1.0)]),
              _naive_ago(hours=1))],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            Cache(db_path=db)


class TestIdempotence:

    def test_already_migrated_idempotent(self, tmp_path):
        """Reopening a v1 DB is a no-op: no error, no data loss."""
        db = tmp_path / "v1.db"
        _make_legacy_db(
            db,
            [(f"{USD}|2024-01-01|2024-01-02",
              _payload(USD, [("2024-01-01", 1.0), ("2024-01-02", 2.0)]),
              _naive_ago(hours=1))],
        )
        first = Cache(db_path=db)
        assert first.size() == 2

        second = Cache(db_path=db)

        assert second.size() == 2
        assert _schema_version(db) == "1"
        assert _query(db, "SELECT COUNT(*) FROM meta WHERE key = 'schema_version'") == [(1,)]

    def test_observations_without_meta_treated_as_v1(self, tmp_path):
        """`observations` present but no `meta`: seed meta, keep the rows."""
        db = tmp_path / "half.db"
        stamp = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "CREATE TABLE observations (series TEXT NOT NULL, date TEXT NOT NULL, "
                "value REAL, fetched_at TEXT NOT NULL, PRIMARY KEY (series, date))"
            )
            conn.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?)",
                (USD, "2024-01-01", 1.0, stamp),
            )
            conn.commit()
        finally:
            conn.close()

        cache = Cache(db_path=db)

        assert _schema_version(db) == "1"
        assert cache.size() == 1

    def test_newer_schema_version_refused(self, tmp_path):
        """A cache written by a newer econchile is not touched."""
        db = tmp_path / "future.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(BcchCacheError, match="schema"):
            Cache(db_path=db)

        assert _schema_version(db) == "2"
        assert _tables(db) == {"meta"}  # touched nothing
