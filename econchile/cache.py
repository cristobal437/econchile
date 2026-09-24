"""
SQLite-backed cache for BCCh API responses.

Stores :class:`~econchile.types.SeriesResult` objects with a 24-hour TTL,
keyed by ``{series}|{desde}|{hasta}``.  The fetcher checks the cache
before hitting the network; ``offline`` falls back to it when the API is
down.  Second layer of the fallback chain: API → cache → error.

Stdlib only: ``sqlite3``, ``json``, ``datetime``, ``pathlib``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from econchile.series_map import Series
from econchile.types import BcchCacheError, Observation, SeriesResult

# ─── Defaults ──────────────────────────────────────────────────────────

DEFAULT_DB_PATH: Path = Path.home() / ".econchile" / "cache.db"
"""Default SQLite database location (created on first use)."""

DEFAULT_TTL_SECONDS: int = 86400
"""Default time-to-live: 24 hours (BCCh daily series change at most once/day)."""


def make_key(series: str, desde: str, hasta: str) -> str:
    """Build the cache key encoding exactly which query produced the data.

    Different date ranges of the same series are DIFFERENT entries —
    a cached full-history response must never be served when the user
    asked for last week.

    Args:
        series: BCCh series code (e.g. ``"F073.TCO.PRE.Z.D"``) or a
            :class:`~econchile.series_map.Series` enum member.
        desde: Start date, ``YYYY-MM-DD``.
        hasta: End date, ``YYYY-MM-DD``.

    Returns:
        The cache key ``f"{code}|{desde}|{hasta}"`` where ``code`` is the
        series' BCCh code (enum members normalised to their ``.value``).
    """
    return f"{_series_code(series)}|{desde}|{hasta}"


def _series_code(series: str | Series) -> str:
    """Normalise a series identifier to its BCCh code string."""
    if isinstance(series, Series):
        return series.value
    return str(series)


def _observation_to_dict(obs: Observation | dict[str, Any]) -> dict[str, Any]:
    """Serialise one observation, accepting Observation objects or dicts."""
    if isinstance(obs, Observation):
        return {"date": obs.date, "value": obs.value}
    return {"date": obs["date"], "value": obs["value"]}


class Cache:
    """SQLite-backed cache for :class:`~econchile.types.SeriesResult`.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``~/.econchile/cache.db`` (parent directory auto-created).
        ttl_seconds: How long entries stay fresh.  Default 86400 (24h).

    Raises:
        BcchCacheError: If the database cannot be opened or created.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._ttl_seconds = ttl_seconds
        self._memory_conn: sqlite3.Connection | None = None
        if str(self._db_path) == ":memory:":
            # :memory: databases live INSIDE a connection — keep one open
            # for the lifetime of the Cache so the schema survives.
            self._memory_conn = sqlite3.connect(":memory:")
        self._init_db()

    # ── Setup ─────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create the database file and the ``cache`` table if missing."""
        try:
            if self._memory_conn is None:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache (
                        key        TEXT PRIMARY KEY,
                        payload    TEXT NOT NULL,
                        fetched_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                    """
                )
            finally:
                # The old `with self._connect() as conn:` committed the
                # transaction on exit — replicate that explicitly so file
                # DBs persist writes before the connection closes.
                conn.commit()
                if self._memory_conn is None:
                    conn.close()
        except (sqlite3.Error, OSError) as exc:
            raise BcchCacheError(
                f"cannot open cache database {self._db_path}: {exc}"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        """Return a connection to the cache database.

        For ``db_path=":memory:"`` this is the one persistent connection
        held for the life of the Cache (the schema lives inside it); for
        file-backed caches a fresh connection is opened per call.
        """
        if self._memory_conn is not None:
            return self._memory_conn
        return sqlite3.connect(str(self._db_path))

    # ── Core API ──────────────────────────────────────────────────────

    def get(self, key: str) -> SeriesResult | None:
        """Return the cached result for ``key``, or None if missing/expired.

        Expired rows are treated as a miss AND deleted — stale data is
        never served.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT payload, expires_at FROM cache WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    # Not in the cache at all — a plain miss, not an error.
                    return None
                payload, expires_at_str = row
                if datetime.fromisoformat(expires_at_str) < datetime.now():
                    # Expired: never serve stale data, and delete the row
                    # so the cache sweeps itself clean on read.
                    conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                    return None
            finally:
                # The old `with self._connect() as conn:` committed the
                # transaction on exit — replicate that explicitly so file
                # DBs persist writes before the connection closes.
                conn.commit()
                if self._memory_conn is None:
                    conn.close()
        except sqlite3.Error as exc:
            raise BcchCacheError(f"cache read failed for {key!r}: {exc}") from exc
        return self._reconstruct(payload)

    def set(self, key: str, result: SeriesResult) -> None:
        """Store ``result`` under ``key`` (upsert).

        Sets ``fetched_at`` = now and ``expires_at`` = now + TTL.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        payload = self._serialize(result)
        now = datetime.now()
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        try:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache (key, payload, fetched_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key, payload, now.isoformat(), expires_at.isoformat()),
                )
            finally:
                # The old `with self._connect() as conn:` committed the
                # transaction on exit — replicate that explicitly so file
                # DBs persist writes before the connection closes.
                conn.commit()
                if self._memory_conn is None:
                    conn.close()
        except sqlite3.Error as exc:
            raise BcchCacheError(f"cache write failed for {key!r}: {exc}") from exc

    # ── Convenience: series + date range ──────────────────────────────

    def get_series(
        self, series: str | Series, desde: str, hasta: str
    ) -> SeriesResult | None:
        """Convenience: :meth:`get` with a key built from components."""
        return self.get(make_key(_series_code(series), desde, hasta))

    def set_series(
        self, series: str | Series, desde: str, hasta: str, result: SeriesResult
    ) -> None:
        """Convenience: :meth:`set` with a key built from components."""
        self.set(make_key(_series_code(series), desde, hasta), result)

    # ── Cache management ──────────────────────────────────────────────

    def clear(self) -> int:
        """Delete every row.  Returns the number of rows removed.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        try:
            conn = self._connect()
            try:
                cursor = conn.execute("DELETE FROM cache")
                return cursor.rowcount
            finally:
                # The old `with self._connect() as conn:` committed the
                # transaction on exit — replicate that explicitly so file
                # DBs persist writes before the connection closes.
                conn.commit()
                if self._memory_conn is None:
                    conn.close()
        except sqlite3.Error as exc:
            raise BcchCacheError(f"cache clear failed: {exc}") from exc

    def size(self) -> int:
        """Return the number of entries currently stored.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        try:
            conn = self._connect()
            try:
                return conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            finally:
                # The old `with self._connect() as conn:` committed the
                # transaction on exit — replicate that explicitly so file
                # DBs persist writes before the connection closes.
                conn.commit()
                if self._memory_conn is None:
                    conn.close()
        except sqlite3.Error as exc:
            raise BcchCacheError(f"cache size query failed: {exc}") from exc

    # ── Serialisation ─────────────────────────────────────────────────

    @staticmethod
    def _serialize(result: SeriesResult) -> str:
        """JSON-serialise a SeriesResult for the ``payload`` column.

        Mirrors ``SeriesResult.to_dict()`` but accepts observations as
        either ``Observation`` objects or plain dicts.
        """
        return json.dumps(
            {
                "series": (
                    result.series.value
                    if hasattr(result.series, "value")
                    else str(result.series)
                ),
                "observations": [_observation_to_dict(o) for o in result.observations],
                "fetched_at": result.fetched_at.isoformat(),
                "source": result.source,
                "metadata": result.metadata,
            }
        )

    @staticmethod
    def _reconstruct(payload: str) -> SeriesResult:
        """Rebuild a SeriesResult from a stored JSON payload."""
        data: dict[str, Any] = json.loads(payload)
        try:
            # Recover the enum member when the code is in the indexed catalog…
            series = Series.from_code(data["series"])
        except KeyError:
            # …otherwise keep the raw code string (graceful degradation).
            series = data["series"]
        observations = [
            Observation(date=obs["date"], value=obs["value"])
            for obs in data["observations"]
        ]
        return SeriesResult(
            series=series,
            observations=observations,
            fetched_at=datetime.fromisoformat(data["fetched_at"]),
            source=data.get("source", "cache"),
            metadata=data.get("metadata", {}),
        )
