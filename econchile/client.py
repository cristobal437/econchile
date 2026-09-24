"""
User-facing API for econchile.

Wraps :mod:`econchile.fetcher` (network), :mod:`econchile.cache` (SQLite),
:mod:`econchile.series_map` (catalog), and :mod:`econchile.types` (data
structures) behind a single :class:`BcchClient`.

Data flow for :meth:`BcchClient.get` is **cache-first**: the SQLite cache
is consulted before the network, so repeated queries within the 24h TTL
are served from disk and never hit the BCCh API.  On a miss the fetcher
runs and the fresh result is stored for next time.

Usage::

    from econchile import BcchClient

    client = BcchClient()                      # token from BCCH_TOKEN env
    result = client.get("UF", "2024-01-01", "2024-12-31")
    hits = client.search("ipc")                # catalog search
    catalog = client.list_series()             # all indexed series

Design highlights:

* **Series identifiers** — a ``Series`` enum member, a human name
  (``"uf"``, ``"IPC_VAR"``), or its BCCh code (``"F073.TCO.PRE.Z.D"``)
  all resolve to the same enum member, so every spelling hits the SAME
  cache entry.  Any other BCCh code (``"F072.CLP.EUR.N.O.D"``) passes
  through as a plain string.
* **Validation first** — ``desde``/``hasta`` are validated *before* the
  cache is touched, so bad or inverted dates never trigger I/O.
* **Errors** — unknown names raise ``KeyError`` (with the catalog
  listed so the message is actionable), bad dates raise ``ValueError``,
  and API failures propagate as :exc:`~econchile.types.BcchApiError`.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from econchile.cache import Cache, make_key
from econchile.fetcher import Fetcher, _validate_dates
from econchile.series_map import Series
from econchile.types import SeriesMeta, SeriesResult


def _fold(text: str) -> str:
    """Lowercase ``text`` and strip diacritics.

    Makes searches accent-insensitive: ``"Dólar"`` folds to ``"dolar"``
    so a user typing ``"dolar"`` (or ``"DOLAR"``) still finds the USD
    series.  NFD decomposition separates each accented letter into a
    base letter plus a combining mark, which we then drop.
    """
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_BCCH_CODE_RE = re.compile(r"^[A-Za-z]\d{3}(\.[A-Za-z0-9_]+)+$")
"""Shape of a BCCh series code (``F073.TCO.PRE.Z.D``, ``G073.IPC.V12.2023.M``)."""


def _resolve_series(series: str | Series) -> Series | str:
    """Resolve a series identifier to a :class:`Series` member or raw code.

    Accepts, in order of preference:

    1. a ``Series`` enum member (used as-is),
    2. an indexed BCCh code string (``"F073.TCO.PRE.Z.D"`` → ``Series.USD``),
    3. a human name, matched case-insensitively against the enum member
       name (``"uf"`` → ``Series.UF``, ``"IPC_VAR"`` → ``Series.IPC_VAR``),
    4. any other BCCh-code-shaped string (``"F072.CLP.EUR.N.O.D"``), which
       is returned unchanged as a plain ``str`` — the full BCCh catalog
       (~30k series) works, it just isn't indexed.

    Resolving indexed identifiers to the enum member guarantees that
    ``"uf"``, ``Series.UF`` and the code all share one cache key.

    Raises:
        KeyError: If the identifier is neither a known name nor shaped
            like a BCCh code, with the available series listed.
    """
    if isinstance(series, Series):
        return series
    name = str(series)
    try:
        return Series.from_code(name)  # indexed BCCh code?
    except KeyError:
        pass  # not an indexed code — fall through to the name lookup
    folded = _fold(name)
    for member in Series:
        if _fold(member.name) == folded:  # human name, case-insensitive
            return member
    if _BCCH_CODE_RE.match(name):
        return name  # raw BCCh code outside the indexed catalog
    available = ", ".join(member.name for member in Series)
    raise KeyError(
        f"Unknown series {name!r}. Available series: {available} "
        "— or pass a raw BCCh code such as 'F072.CLP.EUR.N.O.D'"
    )


def _code_of(resolved: Series | str) -> str:
    """BCCh code string for a resolved identifier (enum member or raw code)."""
    return resolved.value if isinstance(resolved, Series) else resolved


class BcchClient:
    """User-facing client for BCCh macroeconomic data.

    Args:
        token: BCCh API token.  When None, read from the ``BCCH_TOKEN``
            environment variable (via :class:`~econchile.fetcher.Fetcher`).
        db_path: Cache database location.  When None, defaults to
            ``~/.econchile/cache.db`` (via :class:`~econchile.cache.Cache`).
        ttl_seconds: How long cached entries stay fresh (default 24h).
        timeout: HTTP timeout in seconds (default 30).

    Raises:
        BcchApiError: If no token is configured when the API is called
            on a cache miss (raised at fetch time by ``Fetcher``; cache
            hits work without a token).
    """

    def __init__(
        self,
        token: str | None = None,
        db_path: str | Path | None = None,
        ttl_seconds: int = 86400,
        timeout: int = 30,
    ) -> None:
        self._fetcher = Fetcher(token=token, timeout=timeout)
        self._cache = Cache(db_path=db_path, ttl_seconds=ttl_seconds)

    def get(
        self,
        series: str | Series,
        desde: str,
        hasta: str,
        use_cache: bool = True,
    ) -> SeriesResult:
        """Fetch one series over the window ``[desde, hasta]`` — cache-first.

        Args:
            series: A ``Series`` member, a human name (case-insensitive),
                or any BCCh code string (indexed or not).
            desde: Start date, ``YYYY-MM-DD`` (required — no defaults,
                ever; a missing window must never silently download the
                full history).
            hasta: End date, ``YYYY-MM-DD`` (required).
            use_cache: When True (default), serve from the SQLite cache
                within its TTL and store fresh results.  When False,
                bypass the cache entirely (read AND write) and always
                hit the API.

        Returns:
            A :class:`SeriesResult`.  Its ``source`` field reflects the
            origin of the data (``"api"`` when freshly fetched; the
            value stored in the payload when served from cache).

        Raises:
            KeyError: If ``series`` does not resolve to a known series.
            ValueError: If ``desde``/``hasta`` are not ``YYYY-MM-DD``,
                or ``desde`` is after ``hasta``.
            BcchApiError: If the API fails on a cache miss.
        """
        resolved = _resolve_series(series)
        # Validate BEFORE any cache or network I/O: a bad date must fail
        # fast and never trigger a cache lookup or an HTTP request.
        _validate_dates(desde, hasta)
        code = _code_of(resolved)
        # The key encodes exactly which query produced the data, so
        # different date ranges of the same series stay separate entries.
        key = make_key(code, desde, hasta)

        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                return cached  # cache-first: within TTL, no network call

        result = self._fetcher.get_series(resolved, desde, hasta)

        if use_cache:
            self._cache.set(key, result)  # store for next time

        return result

    def search(self, keyword: str) -> list[SeriesMeta]:
        """Search the indexed catalog (the ``Series`` enum) by keyword.

        Case- and accent-insensitive substring match over the series
        name, BCCh code, Spanish title, and English title.  Each field
        is checked separately so a match can never span a field boundary.

        Args:
            keyword: Search term (e.g. ``"ipc"``, ``"dolar"``).

        Returns:
            Matching :class:`SeriesMeta` objects, in enum order.
        """
        needle = _fold(keyword)
        matches: list[SeriesMeta] = []
        for member in Series:
            meta = member.meta()
            fields = (
                member.name,
                member.value,
                meta.spanish_title,
                meta.english_title,
            )
            if any(needle in _fold(field) for field in fields):
                matches.append(meta)
        return matches

    def list_series(self) -> list[SeriesMeta]:
        """Return metadata for every indexed series, in enum order."""
        return [member.meta() for member in Series]

    def clear_cache(self) -> int:
        """Delete every cached entry.

        Returns:
            The number of rows removed.
        """
        return self._cache.clear()
