"""
Resilience layer for econchile: **API → cache → BcchOfflineError**.

Wraps :mod:`econchile.fetcher` (network) and :mod:`econchile.cache`
(SQLite) behind a single :class:`OfflineClient` that degrades gracefully
when the BCCh API is down, slow, or rate-limiting.

Data flow for :meth:`OfflineClient.get` is **API-first** — the opposite
of :class:`~econchile.client.BcchClient`:

* :class:`~econchile.client.BcchClient.get` is *cache-first*: within the
  TTL, repeated queries are served from disk and never touch the network
  (performance).
* :meth:`OfflineClient.get` is *API-first*: it always tries the live API
  first (freshness), and only falls back to the cache when the API
  *fails*.

Use case: scheduled jobs / scripts that must not crash when the BCCh API
is unavailable.  Fresh data when possible, last-known-good from the cache
when not, and a clear, actionable error when neither is available.

Usage::

    from econchile import OfflineClient

    client = OfflineClient()                      # token from BCCH_TOKEN
    result = client.get("UF", "2024-01-01", "2024-12-31")
"""

from __future__ import annotations

from pathlib import Path

import requests

from econchile.cache import Cache
from econchile.client import _code_of, _resolve_series
from econchile.fetcher import Fetcher, _validate_dates
from econchile.series_map import Series
from econchile.types import BcchApiError, BcchOfflineError, SeriesResult


class OfflineClient:
    """API-first client with cache fallback for BCCh macroeconomic data.

    Args:
        token: BCCh API token.  When None, read from the ``BCCH_TOKEN``
            environment variable (via :class:`~econchile.fetcher.Fetcher`).
        db_path: Cache database location.  When None, defaults to
            ``~/.econchile/cache.db`` (via :class:`~econchile.cache.Cache`).
        ttl_seconds: How long cached entries stay fresh (default 24h).
        timeout: HTTP timeout in seconds (default 30).

    Raises:
        BcchApiError: If the API is unreachable because no token is
            configured (raised at fetch time by ``Fetcher``; the existing
            fallback catches it and serves the cache).
    """

    def __init__(
        self,
        token: str | None = None,
        db_path: str | Path | None = None,
        ttl_seconds: int = 86400,
        timeout: int = 30,
        strict: bool = False,
    ) -> None:
        self._fetcher = Fetcher(token=token, timeout=timeout)
        self._cache = Cache(db_path=db_path, ttl_seconds=ttl_seconds)
        self._strict = strict

    def get(
        self,
        series: str | Series,
        desde: str,
        hasta: str,
    ) -> SeriesResult:
        """Fetch one series over the window ``[desde, hasta]`` — API-first.

        Tries the live API; on any failure falls back to the cache; when
        both are unavailable raises :class:`BcchOfflineError`.

        Args:
            series: A ``Series`` member, a human name (case-insensitive),
                or any BCCh code string (indexed or not).
            desde: Start date, ``YYYY-MM-DD`` (required).
            hasta: End date, ``YYYY-MM-DD`` (required).

        Returns:
            A :class:`SeriesResult`.  ``source`` is ``"api"`` for fresh
            data; when served from the cache it is whatever was stored
            (typically ``"api"`` from when it was fetched).

        Raises:
            KeyError: If ``series`` does not resolve to a known series.
            ValueError: If ``desde``/``hasta`` are not ``YYYY-MM-DD``,
                or ``desde`` is after ``hasta``.
            BcchOfflineError: If the API fails AND the cache has no
                usable data for this key.
        """
        resolved = _resolve_series(series)
        # Validate BEFORE any cache or network I/O: a bad date must fail
        # fast and never trigger a cache lookup or an HTTP request.
        _validate_dates(desde, hasta)
        code = _code_of(resolved)

        try:
            result = self._fetcher.fetch(resolved, desde, hasta)
        except (BcchApiError, requests.RequestException, OSError) as exc:
            # API failure (BcchApiError, network error, timeout): fall
            # back to the cache before giving up.
            load = self._cache.load(code, desde, hasta, include_stale=True)
            has_coverage = self._cache.covers(code, desde, hasta, include_stale=True)
            if not load.observations and not has_coverage:
                # Both fallback layers exhausted.  Surface a helpful error
                # that carries the original failure so debugging is possible.
                raise BcchOfflineError(
                    f"BCCh API failed for series {code} ({desde}..{hasta}): {exc} "
                    "— no usable data in the cache (all fallback layers "
                    "exhausted); the API may be down or rate-limiting",
                    series=code,
                    desde=desde,
                    hasta=hasta,
                    api_error=str(exc),
                    cache_had_data=False,
                ) from exc
            if self._strict and not has_coverage:
                raise BcchOfflineError(
                    f"BCCh API failed for series {code} ({desde}..{hasta}): {exc} "
                    f"— cache is partial (missing_dates: {load.missing_dates}); "
                    "strict=True refuses partial data",
                    series=code,
                    desde=desde,
                    hasta=hasta,
                    api_error=str(exc),
                    cache_had_data=True,
                    missing_dates=load.missing_dates,
                ) from exc
            return load

        # Warm the fallback: every successful fetch refreshes the cached
        # copy so the cache is ready if the API goes down next time.
        self._cache.put_observations(result, desde, hasta)

        return result
