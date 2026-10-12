"""
SQLite-backed observation store for BCCh API responses.

v0.3 replaces the key-based cache with per-(series, date) observation rows.
Any requested window is assembled from stored points, so repeated queries
with different ``hasta`` values still hit the cache.

Schema (version 1) — four tables:

- ``observations`` — one row per (series, date); value NULL means "queried,
  BCCh said ND".  PK(series, date).
- ``series_meta`` — preserves the API metadata dict per series across
  cache hits.  PK(series).
- ``fetched_windows`` — records which (series, desde, hasta) windows were
  asked of the API.  Needed because monthly/quarterly/annual series are
  dated on the first day of the period; observations alone cannot tell
  "BCCh has nothing after X" from "never asked".  PK(series, desde, hasta).
- ``meta`` — singleton state (schema_version, last_purge_at).

Freshness classes (spec §4):

- **fresh**: ``now - fetched_at <= ttl_seconds`` (default 24h)
- **stale**: ``ttl < age <= max_stale_age`` (default 7d) — servable only in
  the offline fallback path
- **expired**: ``age > max_stale_age`` — never served, purged after
  ``max_purge_age`` (default ~5y)

Migration (spec §3): on first open of a v0.2 database, the legacy ``cache``
table is read, its rows are decomposed into observations/series_meta/
fetched_windows inside one transactional block, and the old table is
dropped.  A verification count guards against silent data loss; on
mismatch the migration rolls back and the original ``cache`` table is
left intact.

Deprecated shims (spec §6): ``make_key``, ``get``, ``set``, ``get_series``,
``set_series`` still work but emit DeprecationWarning.  They will be
removed in a future release once client.py / offline.py stop calling them.

Stdlib only: ``sqlite3``, ``json``, ``datetime``, ``warnings``,
``pathlib``, ``contextlib``.
"""

from __future__ import annotations

import json
import sqlite3
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from econchile.series_map import Series
from econchile.types import BcchCacheError, Observation, SeriesResult

# ─── Defaults ──────────────────────────────────────────────────────────

DEFAULT_DB_PATH: Path = Path.home() / ".econchile" / "cache.db"
"""Default SQLite database location (created on first use)."""

DEFAULT_TTL_SECONDS: int = 86400
"""Default time-to-live: 24 hours."""

DEFAULT_MAX_STALE_AGE: int = 604800
"""Default max stale age: 7 days."""

DEFAULT_MAX_PURGE_AGE: int = 157784630
"""Default max purge age: ~5 years."""

SCHEMA_VERSION: str = "1"


# ─── Helpers ───────────────────────────────────────────────────────────

def _series_code(series: str | Series) -> str:
    """Normalise a series identifier to its BCCh code string."""
    if isinstance(series, Series):
        return series.value
    return str(series)


def _now_utc_iso() -> str:
    """Aware-UTC ISO timestamp with microsecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalize_legacy_timestamp(raw: str) -> str:
    """Normalize a legacy (possibly naive) timestamp to aware-UTC ISO."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.astimezone().astimezone(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="microseconds")


def _parse_key(key: str) -> tuple[str, str, str] | None:
    """Parse ``code|desde|hasta`` into its three parts, or None."""
    parts = key.rsplit("|", 2)
    if len(parts) != 3:
        return None
    code, desde, hasta = parts
    try:
        datetime.strptime(desde, "%Y-%m-%d")
        datetime.strptime(hasta, "%Y-%m-%d")
    except ValueError:
        return None
    return code, desde, hasta


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """ROLLBACK without masking the original error if it fails too."""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


# ─── Deprecated module-level shim ─────────────────────────────────────

def make_key(series: str | Series, desde: str, hasta: str) -> str:
    """Build the legacy cache key ``f"{code}|{desde}|{hasta}"``.

    .. deprecated:: 0.3
    """
    warnings.warn(
        "econchile.cache.make_key is deprecated; "
        "use the observation-level API",
        DeprecationWarning,
        stacklevel=2,
    )
    return f"{_series_code(series)}|{desde}|{hasta}"


# ─── Cache ─────────────────────────────────────────────────────────────

class Cache:
    """SQLite-backed observation store for :class:`~econchile.types.SeriesResult`.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``~/.econchile/cache.db`` (parent directory auto-created).
            Use ``":memory:"`` for a non-persistent in-memory store.
        ttl_seconds: How long entries stay fresh.  Default 86400 (24h).
        max_stale_age: Maximum age for stale-but-servable rows.
            Default 604800 (7 days).
        max_purge_age: Rows older than this are deleted by ``purge()``.
            Default 157784630 (~5 years).

    Raises:
        BcchCacheError: If the database cannot be opened or migrated.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_stale_age: int = DEFAULT_MAX_STALE_AGE,
        max_purge_age: int = DEFAULT_MAX_PURGE_AGE,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._ttl_seconds = ttl_seconds
        self._max_stale_age = max_stale_age
        self._max_purge_age = max_purge_age
        self._memory_conn: sqlite3.Connection | None = None
        if str(self._db_path) == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", isolation_level=None)
        try:
            if self._memory_conn is None:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._migrate()
            self.purge()
        except BcchCacheError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BcchCacheError(
                f"cannot open cache database {self._db_path}: {exc}"
            ) from exc

    # ── Connection management ─────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Return a connection to the cache database.

        For ``db_path=":memory:"`` this is the one persistent connection
        held for the life of the Cache; for file-backed caches a fresh
        connection is opened per call.
        """
        if self._memory_conn is not None:
            return self._memory_conn
        return sqlite3.connect(str(self._db_path), isolation_level=None)

    def _close(self, conn: sqlite3.Connection) -> None:
        """Close ``conn`` unless it is the persistent :memory: connection."""
        if self._memory_conn is None:
            conn.close()

    # ── Migration (spec §3) ───────────────────────────────────────────

    def _migrate(self) -> None:
        """Run the migration state machine inside one transaction."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

                has_meta = "meta" in tables
                has_cache = "cache" in tables

                if has_meta:
                    self._migrate_check_version(conn)
                    conn.execute("COMMIT")
                elif has_cache:
                    self._migrate_legacy(conn)
                else:
                    self._ensure_schema(conn)
                    self._seed_meta_if_missing(conn)
                    conn.execute("COMMIT")
            except BcchCacheError:
                _rollback_quietly(conn)
                raise
            except BaseException:
                _rollback_quietly(conn)
                raise
        except BcchCacheError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BcchCacheError(
                f"cache migration failed; original cache left intact: {exc}"
            ) from exc
        finally:
            self._close(conn)

    def _migrate_check_version(self, conn: sqlite3.Connection) -> None:
        """Handle the case where ``meta`` table exists."""
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._ensure_schema(conn)
            self._seed_meta_if_missing(conn)
            return
        version = row[0]
        if version != SCHEMA_VERSION:
            raise BcchCacheError(
                f"cache schema version {version!r} is newer than supported "
                f"({SCHEMA_VERSION!r}); upgrade econchile"
            )
        self._ensure_schema(conn)

    def _migrate_legacy(self, conn: sqlite3.Connection) -> None:
        """Migrate a v0.2 ``cache`` table to v1 schema."""
        try:
            self._ensure_schema(conn)

            rows = conn.execute(
                "SELECT key, payload, fetched_at FROM cache "
                "ORDER BY fetched_at ASC"
            ).fetchall()

            expected: set[tuple[str, str]] = set()
            skipped = 0

            for key, payload_str, legacy_fetched_at in rows:
                try:
                    payload = json.loads(payload_str)
                    series = payload["series"]
                    observations = payload["observations"]
                    if not isinstance(observations, list):
                        raise TypeError("observations is not a list")
                    fetched_at = _normalize_legacy_timestamp(legacy_fetched_at)
                except (
                    json.JSONDecodeError, KeyError, TypeError,
                    ValueError, AttributeError,
                ):
                    skipped += 1
                    continue

                metadata = payload.get("metadata") or {}

                for obs in observations:
                    try:
                        obs_date = obs["date"]
                        obs_value = obs["value"]
                    except (KeyError, TypeError):
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO observations "
                        "(series, date, value, fetched_at) VALUES (?, ?, ?, ?)",
                        (series, obs_date, obs_value, fetched_at),
                    )
                    expected.add((series, obs_date))

                conn.execute(
                    "INSERT OR REPLACE INTO series_meta "
                    "(series, metadata, updated_at) VALUES (?, ?, ?)",
                    (series, json.dumps(metadata), fetched_at),
                )

                parsed = _parse_key(key)
                if parsed is not None:
                    _, desde, hasta = parsed
                    # Stored under the payload series, like its observations.
                    conn.execute(
                        "INSERT OR REPLACE INTO fetched_windows "
                        "(series, desde, hasta, fetched_at) VALUES (?, ?, ?, ?)",
                        (series, desde, hasta, fetched_at),
                    )

            actual_count = self._migrated_observation_count(conn)
            if actual_count != len(expected):
                raise ValueError(
                    f"observation count mismatch: expected {len(expected)}, "
                    f"got {actual_count}"
                )

            now = _now_utc_iso()
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) "
                "VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) "
                "VALUES ('last_purge_at', ?)",
                (now,),
            )

            conn.execute("DROP TABLE cache")
            conn.execute("COMMIT")

            if skipped > 0:
                warnings.warn(
                    f"econchile cache migration skipped {skipped} "
                    f"unreadable legacy row(s)",
                    UserWarning,
                    stacklevel=2,
                )
        except BcchCacheError:
            _rollback_quietly(conn)
            raise
        except Exception as exc:
            _rollback_quietly(conn)
            raise BcchCacheError(
                f"cache migration failed; original cache left intact: {exc}"
            ) from exc

    def _migrated_observation_count(self, conn: sqlite3.Connection) -> int:
        """Return the current observation row count."""
        return conn.execute(
            "SELECT COUNT(*) FROM observations"
        ).fetchone()[0]

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """CREATE TABLE IF NOT EXISTS for all four v1 tables."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS observations (
                series      TEXT NOT NULL,
                date        TEXT NOT NULL,
                value       REAL,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (series, date)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS series_meta (
                series      TEXT PRIMARY KEY,
                metadata    TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fetched_windows (
                series      TEXT NOT NULL,
                desde       TEXT NOT NULL,
                hasta       TEXT NOT NULL,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (series, desde, hasta)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )

    def _seed_meta_if_missing(self, conn: sqlite3.Connection) -> None:
        """Seed meta rows if they don't already exist."""
        now = _now_utc_iso()
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) "
            "VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) "
            "VALUES ('last_purge_at', ?)",
            (now,),
        )

    # ── Core observation API (spec §6) ────────────────────────────────

    def _cutoff(self, include_stale: bool) -> str:
        """Oldest ``fetched_at`` still usable: fresh only, or fresh + stale."""
        max_age = self._max_stale_age if include_stale else self._ttl_seconds
        oldest = datetime.now(timezone.utc) - timedelta(seconds=max_age)
        return oldest.isoformat(timespec="microseconds")

    @staticmethod
    def _usable_rows(
        conn: sqlite3.Connection, code: str, desde: str, hasta: str, cutoff: str
    ) -> list[tuple[str, float | None, str]]:
        """``(date, value, fetched_at)`` rows in the window, newer than ``cutoff``."""
        return conn.execute(
            "SELECT date, value, fetched_at FROM observations "
            "WHERE series = ? AND date >= ? AND date <= ? AND fetched_at >= ? "
            "ORDER BY date",
            (code, desde, hasta, cutoff),
        ).fetchall()

    def get_observations(
        self,
        series: str | Series,
        desde: str,
        hasta: str,
        include_stale: bool = False,
    ) -> list[Observation]:
        """Return observations for ``series`` in ``[desde, hasta]``.

        Args:
            series: BCCh series code or :class:`~econchile.series_map.Series`.
            desde: Start date, ``YYYY-MM-DD``.
            hasta: End date, ``YYYY-MM-DD``.
            include_stale: When False (default), only fresh rows.
                When True, fresh + stale (never expired).

        Returns:
            List of :class:`~econchile.types.Observation`, ordered by date.
            ND rows come back with ``value=None``; never-fetched dates are
            absent.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        code = _series_code(series)
        cutoff = self._cutoff(include_stale)
        try:
            conn = self._connect()
            try:
                rows = self._usable_rows(conn, code, desde, hasta, cutoff)
            finally:
                self._close(conn)
        except sqlite3.Error as exc:
            raise BcchCacheError(
                f"cache read failed for {code} [{desde}..{hasta}]: {exc}"
            ) from exc
        return [Observation(date=d, value=v) for d, v, _ in rows]

    def put_observations(
        self,
        result: SeriesResult,
        desde: str | None = None,
        hasta: str | None = None,
    ) -> None:
        """Store ``result`` in one transaction (all or nothing).

        Upserts every observation with ``fetched_at = now`` (UTC), refreshes
        the series metadata, and records the requested window when
        ``desde``/``hasta`` are given (that is what lets :meth:`covers` know
        the window was asked, even past the last published value).

        Args:
            result: The series result to store.  Observations may be
                :class:`Observation` objects or ``{"date", "value"}`` dicts.
            desde: Start date of the requested window (optional).
            hasta: End date of the requested window (optional).

        Raises:
            BcchCacheError: On SQLite I/O failure (nothing is written).
        """
        code = _series_code(result.series)
        now = _now_utc_iso()
        rows = []
        for obs in result.observations:
            if isinstance(obs, Observation):
                rows.append((code, obs.date, obs.value, now))
            else:
                rows.append((code, obs["date"], obs["value"], now))
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.executemany(
                        "INSERT OR REPLACE INTO observations "
                        "(series, date, value, fetched_at) VALUES (?, ?, ?, ?)",
                        rows,
                    )
                    if result.metadata:
                        # Only real metadata replaces what is stored, so a
                        # result without metadata never blanks it out.
                        conn.execute(
                            "INSERT OR REPLACE INTO series_meta "
                            "(series, metadata, updated_at) VALUES (?, ?, ?)",
                            (code, json.dumps(result.metadata), now),
                        )
                    if desde is not None and hasta is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO fetched_windows "
                            "(series, desde, hasta, fetched_at) VALUES (?, ?, ?, ?)",
                            (code, desde, hasta, now),
                        )
                    conn.execute("COMMIT")
                except BaseException:
                    _rollback_quietly(conn)
                    raise
            finally:
                self._close(conn)
        except sqlite3.Error as exc:
            raise BcchCacheError(f"cache write failed for {code}: {exc}") from exc

    def covers(
        self,
        series: str | Series,
        desde: str,
        hasta: str,
        include_stale: bool = False,
    ) -> bool:
        """True when usable fetched windows cover every day of the range.

        Args:
            series: BCCh series code or enum member.
            desde: Start date, ``YYYY-MM-DD``.
            hasta: End date, ``YYYY-MM-DD``.
            include_stale: When False, only fresh windows count; when True,
                fresh + stale (never expired).

        Returns:
            True if the union of usable windows covers ``[desde, hasta]``.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        code = _series_code(series)
        cutoff = self._cutoff(include_stale)
        try:
            conn = self._connect()
            try:
                windows = self._usable_windows(conn, code, cutoff)
            finally:
                self._close(conn)
        except sqlite3.Error as exc:
            raise BcchCacheError(f"cache covers check failed for {code}: {exc}") from exc
        return _windows_cover(windows, desde, hasta)

    @staticmethod
    def _usable_windows(
        conn: sqlite3.Connection, code: str, cutoff: str
    ) -> list[tuple[str, str]]:
        """``(desde, hasta)`` of the series' windows newer than ``cutoff``."""
        return conn.execute(
            "SELECT desde, hasta FROM fetched_windows "
            "WHERE series = ? AND fetched_at >= ? ORDER BY desde",
            (code, cutoff),
        ).fetchall()

    def load(
        self,
        series: str | Series,
        desde: str,
        hasta: str,
        include_stale: bool = False,
    ) -> SeriesResult:
        """Reconstruct a :class:`SeriesResult` from usable observations.

        ``series`` comes back as the enum member when indexed (raw ``str``
        otherwise), ``metadata`` from ``series_meta`` (``{}`` if none),
        ``source`` stays ``"api"``, and ``fetched_at`` is the newest
        ``fetched_at`` among the served observations (the newest usable
        overlapping window when there are none).

        Args:
            series: BCCh series code or enum member.
            desde: Start date, ``YYYY-MM-DD``.
            hasta: End date, ``YYYY-MM-DD``.
            include_stale: When False, only fresh data is served.

        Returns:
            A :class:`SeriesResult` (observations may be empty).

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        code = _series_code(series)
        cutoff = self._cutoff(include_stale)
        try:
            conn = self._connect()
            try:
                rows = self._usable_rows(conn, code, desde, hasta, cutoff)
                meta_row = conn.execute(
                    "SELECT metadata FROM series_meta WHERE series = ?", (code,)
                ).fetchone()
                newest_window = conn.execute(
                    "SELECT MAX(fetched_at) FROM fetched_windows "
                    "WHERE series = ? AND desde <= ? AND hasta >= ? AND fetched_at >= ?",
                    (code, hasta, desde, cutoff),
                ).fetchone()[0]
            finally:
                self._close(conn)
        except sqlite3.Error as exc:
            raise BcchCacheError(
                f"cache read failed for {code} [{desde}..{hasta}]: {exc}"
            ) from exc

        if rows:
            fetched_at = datetime.fromisoformat(max(r[2] for r in rows))
        elif newest_window:
            fetched_at = datetime.fromisoformat(newest_window)
        else:
            fetched_at = datetime.now(timezone.utc)

        try:
            series_obj: Series | str = Series.from_code(code)
        except KeyError:
            series_obj = code  # raw code outside the indexed catalog

        return SeriesResult(
            series=series_obj,
            observations=[Observation(date=d, value=v) for d, v, _ in rows],
            fetched_at=fetched_at,
            source="api",
            metadata=json.loads(meta_row[0]) if meta_row else {},
        )

    # ── Cache management ──────────────────────────────────────────────

    def purge(self) -> int:
        """Delete rows older than ``max_purge_age``.

        Returns:
            The number of observation rows deleted.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        now = datetime.now(timezone.utc)
        cutoff = (
            now - timedelta(seconds=self._max_purge_age)
        ).isoformat(timespec="microseconds")
        now_str = _now_utc_iso()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    obs_count = conn.execute(
                        "SELECT COUNT(*) FROM observations "
                        "WHERE fetched_at < ?",
                        (cutoff,),
                    ).fetchone()[0]
                    conn.execute(
                        "DELETE FROM observations WHERE fetched_at < ?",
                        (cutoff,),
                    )
                    conn.execute(
                        "DELETE FROM fetched_windows WHERE fetched_at < ?",
                        (cutoff,),
                    )
                    conn.execute(
                        "DELETE FROM series_meta WHERE series NOT IN "
                        "(SELECT DISTINCT series FROM observations)"
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) "
                        "VALUES ('last_purge_at', ?)",
                        (now_str,),
                    )
                    conn.execute("COMMIT")
                except BaseException:
                    _rollback_quietly(conn)
                    raise
            finally:
                self._close(conn)
        except BcchCacheError:
            raise
        except sqlite3.Error as exc:
            raise BcchCacheError(
                f"cache purge failed: {exc}"
            ) from exc
        return obs_count

    def clear(self) -> int:
        """Delete all observations, series_meta, and fetched_windows.

        Returns:
            The number of observation rows deleted.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    obs_count = conn.execute(
                        "SELECT COUNT(*) FROM observations"
                    ).fetchone()[0]
                    conn.execute("DELETE FROM observations")
                    conn.execute("DELETE FROM series_meta")
                    conn.execute("DELETE FROM fetched_windows")
                    conn.execute("COMMIT")
                except BaseException:
                    _rollback_quietly(conn)
                    raise
            finally:
                self._close(conn)
        except BcchCacheError:
            raise
        except sqlite3.Error as exc:
            raise BcchCacheError(
                f"cache clear failed: {exc}"
            ) from exc
        return obs_count

    def size(self) -> int:
        """Return the number of observation rows currently stored.

        Raises:
            BcchCacheError: On SQLite I/O failure.
        """
        try:
            conn = self._connect()
            try:
                return conn.execute(
                    "SELECT COUNT(*) FROM observations"
                ).fetchone()[0]
            finally:
                self._close(conn)
        except sqlite3.Error as exc:
            raise BcchCacheError(
                f"cache size query failed: {exc}"
            ) from exc

    # ── Deprecated key-based shims (spec §6) ──────────────────────────

    def get(self, key: str) -> SeriesResult | None:
        """Return the cached result for ``key``, or None.

        .. deprecated:: 0.3
        """
        warnings.warn(
            "econchile.cache.Cache.get is deprecated; "
            "use covers() + load() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        parsed = _parse_key(key)
        if parsed is None:
            return None
        code, desde, hasta = parsed
        if not self.covers(code, desde, hasta):
            return None
        return self.load(code, desde, hasta)

    def set(self, key: str, result: SeriesResult) -> None:
        """Store ``result`` under ``key``.

        .. deprecated:: 0.3
        """
        warnings.warn(
            "econchile.cache.Cache.set is deprecated; "
            "use put_observations() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        parsed = _parse_key(key)
        if parsed is not None:
            code, desde, hasta = parsed
            self.put_observations(result, desde, hasta)
        else:
            self.put_observations(result)

    def get_series(
        self, series: str | Series, desde: str, hasta: str,
    ) -> SeriesResult | None:
        """Convenience: :meth:`get` with a key built from components.

        .. deprecated:: 0.3
        """
        warnings.warn(
            "econchile.cache.Cache.get_series is deprecated; "
            "use covers() + load() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        code = _series_code(series)
        if not self.covers(code, desde, hasta):
            return None
        return self.load(code, desde, hasta)

    def set_series(
        self,
        series: str | Series,
        desde: str,
        hasta: str,
        result: SeriesResult,
    ) -> None:
        """Convenience: :meth:`set` with a key built from components.

        .. deprecated:: 0.3
        """
        warnings.warn(
            "econchile.cache.Cache.set_series is deprecated; "
            "use put_observations() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.put_observations(result, desde, hasta)


# ─── Module-level helpers ──────────────────────────────────────────────

def _windows_cover(
    windows: list[tuple[str, str]],
    desde: str,
    hasta: str,
) -> bool:
    """True when merged *windows* cover every day of [desde, hasta]."""
    if not windows:
        return False

    merged_desde = windows[0][0]
    merged_hasta = windows[0][1]

    if merged_desde <= desde and merged_hasta >= hasta:
        return True

    for w_desde, w_hasta in windows[1:]:
        next_day = (
            datetime.fromisoformat(merged_hasta) + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        if w_desde <= next_day:
            if w_hasta > merged_hasta:
                merged_hasta = w_hasta
        else:
            merged_desde = w_desde
            merged_hasta = w_hasta

        if merged_desde <= desde and merged_hasta >= hasta:
            return True

    return False
