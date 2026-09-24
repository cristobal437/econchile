"""
Synchronous HTTP client for the BCCh REST API.

Builds the request URL for a single series plus a mandatory date window,
GETs it, decodes the response body (the BCCh API's encoding is unstable),
parses it via :func:`econchile.parsers.parse_response`, and returns a clean
:class:`~econchile.types.SeriesResult`.

This is the FIRST layer of the fallback chain: **API** → cache → error.

Live-tested API facts baked into this module:

* The REST range parameters are ``firstDate``/``lastDate``.  The Python
  library docs say ``desde``/``hasta``, but the endpoint IGNORES those and
  returns the FULL history (~16k rows) when they are used.  This module's
  ``desde``/``hasta`` arguments are mapped to ``firstDate``/``lastDate``
  in the URL — never passed through verbatim.
* The response encoding is UNSTABLE: sometimes UTF-16 with a BOM
  (``FF FE``), sometimes plain UTF-8, and occasionally latin-1
  (ISO-8859-1) with raw accented bytes.  The decoder checks for the BOM
  first, then tries UTF-8, and falls back to latin-1 (which never fails),
  exactly like :func:`econchile.parsers.parse_response`.
* Multi-series requests (repeated ``timeseries`` params) return error
  ``Codigo: -50``.  One series per request — batching is the caller's job.
* API errors come back as HTTP 200 with ``Codigo != 0`` in the JSON body,
  so a non-zero ``Codigo`` is treated as an error even on a 200 response.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from econchile.parsers import ParsingError, parse_response
from econchile.series_map import Series
from econchile.types import BcchApiError, Observation, SeriesResult

# ─── Constants ─────────────────────────────────────────────────────────

BASE_URL: str = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
"""BCCh REST endpoint for series queries (token passed as query param)."""

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
"""Strict ``YYYY-MM-DD`` shape check (rejects ``2024-1-1`` and ``01/2024``)."""


# ─── Helpers ───────────────────────────────────────────────────────────

def _series_code(series: str | Series) -> str:
    """Normalise a series identifier to its BCCh code string."""
    if isinstance(series, Series):
        return series.value
    return str(series)


def _resolve_series(series: str | Series) -> tuple[Series | str, str]:
    """Normalise to ``(identity, code)``.

    ``identity`` is the ``Series`` enum member when the code is in the
    indexed catalog (so ``result.series`` is always an enum for known
    series), otherwise the raw code string (graceful degradation).
    """
    if isinstance(series, Series):
        return series, series.value
    code = str(series)
    try:
        return Series.from_code(code), code
    except KeyError:
        return code, code


def _validate_dates(desde: str, hasta: str) -> None:
    """Enforce ``YYYY-MM-DD`` on both bounds of the query window.

    Raises:
        ValueError: If either value is not a valid ``YYYY-MM-DD`` date,
            or if ``desde`` is after ``hasta``.
    """
    for name, value in (("desde", desde), ("hasta", hasta)):
        if not isinstance(value, str) or not _DATE_RE.match(value):
            raise ValueError(
                f"{name} must be a date in YYYY-MM-DD format, got {value!r}"
            )
        datetime.strptime(value, "%Y-%m-%d")  # rejects impossible dates
    # ISO dates compare correctly as strings.
    if desde > hasta:
        raise ValueError(f"desde ({desde}) must not be after hasta ({hasta})")


def _decode(content: bytes) -> str:
    """Decode a BCCh response body.

    The real API returns UTF-16 LE with a BOM (``FF FE``), sometimes
    plain UTF-8, and occasionally latin-1 (raw accented bytes).
    Decode order: UTF-16 BOM → UTF-8 → latin-1 (latin-1 never fails).
    """
    if content[:2] == b"\xff\xfe":
        return content.decode("utf-16")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


# ─── Public API ────────────────────────────────────────────────────────

class Fetcher:
    """Sync HTTP client for the BCCh REST API.

    Args:
        token: BCCh API token.  If None, read from the ``BCCH_TOKEN``
            environment variable.
        timeout_seconds: Request timeout in seconds (default 30).
        timeout: Deprecated alias for ``timeout_seconds``, kept so
            :class:`~econchile.client.BcchClient` can pass ``timeout=``.
        max_retries: Number of retries AFTER the first attempt for
            transient failures (default 2 → 3 total attempts).
        retry_backoff: Base seconds for exponential backoff between
            retries.  Sleep is ``retry_backoff * 2**attempt``.
            Set to 0 to disable sleeping (tests).

    Raises:
        BcchApiError: If no token is configured when :meth:`fetch` is
            called (not at construction time, so cache-only use works
            without a token).
    """

    def __init__(
        self,
        token: str | None = None,
        timeout_seconds: int = 30,
        timeout: int | None = None,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
    ) -> None:
        # Explicit token wins; otherwise fall back to the environment.
        # Token may be None — validated at fetch time, not construction,
        # so OfflineClient/BcchClient can be built for cache-only use.
        self._token = token if token is not None else os.environ.get("BCCH_TOKEN")
        if timeout is not None:
            timeout_seconds = timeout
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    # ── Internals ──────────────────────────────────────────────────

    def _build_url(self, code: str, desde: str, hasta: str) -> str:
        """Build the GetSeries query URL for one series + date window.

        ``desde``/``hasta`` are mapped to ``firstDate``/``lastDate`` —
        the params the REST endpoint actually honours.
        """
        params = {
            "token": self._token,
            "function": "GetSeries",
            "timeseries": code,
            "firstDate": desde,
            "lastDate": hasta,
        }
        return f"{BASE_URL}?{urlencode(params)}"

    def _get(self, url: str, code: str, desde: str, hasta: str) -> requests.Response:
        """GET one URL and raise on non-2xx/3xx HTTP statuses.

        Raises:
            BcchApiError: If the HTTP status is >= 400.
        """
        resp = requests.get(url, timeout=self._timeout)
        if resp.status_code >= 400:
            raise BcchApiError(
                f"BCCh API HTTP error {resp.status_code} for series {code} "
                f"({desde}..{hasta})",
                http_status=resp.status_code,
            )
        return resp

    # ── Public methods ─────────────────────────────────────────────

    def fetch_raw(self, series: str | Series, desde: str, hasta: str) -> dict:
        """Lower-level call: GET + decode + parse, returning the raw parsed dict.

        Useful for debugging or for callers who want the parsed response
        without the ``SeriesResult`` wrapper.

        Args:
            series: A :class:`Series` member or a raw BCCh code string.
            desde: Start date, ``YYYY-MM-DD`` (required).
            hasta: End date, ``YYYY-MM-DD`` (required).

        Returns:
            The parsed response dict from :func:`econchile.parsers.parse_response`
            (keys ``series_id``, ``observations``, ``metadata``).

        Raises:
            BcchApiError: If no token is configured (raised before any
                network I/O), on HTTP >= 400, on network failure, on API
                ``Codigo != 0``, or on a JSON/parse failure.
            ValueError: If ``desde``/``hasta`` are not ``YYYY-MM-DD``.
        """
        code = _series_code(series)
        if not self._token:
            raise BcchApiError(
                "BCCh API token required: pass token=... or set the BCCH_TOKEN env var",
                series=code, desde=desde, hasta=hasta,
            )
        _validate_dates(desde, hasta)
        url = self._build_url(code, desde, hasta)

        for attempt in range(self._max_retries + 1):
            if attempt > 0 and self._retry_backoff > 0:
                time.sleep(self._retry_backoff * 2 ** (attempt - 1))
            is_last = attempt >= self._max_retries
            try:
                resp = self._get(url, code, desde, hasta)
            except BcchApiError as exc:
                http_status = exc.context.get("http_status", 0)
                if (http_status == 429 or http_status >= 500) and not is_last:
                    continue
                raise
            except requests.RequestException as exc:
                if not is_last:
                    continue
                raise BcchApiError(
                    f"network error fetching series {code} ({desde}..{hasta}): "
                    f"{type(exc).__name__}"
                ) from None
            try:
                text = _decode(resp.content)
            except UnicodeDecodeError as exc:
                raise BcchApiError(
                    f"undecodable response for series {code} ({desde}..{hasta}): {exc}"
                ) from exc
            try:
                return parse_response(text)
            except ParsingError as exc:
                raise BcchApiError(
                    f"BCCh API error for series {code} ({desde}..{hasta}): {exc}"
                ) from exc
            except json.JSONDecodeError as exc:
                if not is_last:
                    continue
                raise BcchApiError(
                    f"invalid JSON response for series {code} ({desde}..{hasta}): {exc}"
                ) from exc

    def fetch(self, series: str | Series, desde: str, hasta: str) -> SeriesResult:
        """Main entry: fetch + parse a series into a :class:`SeriesResult`.

        Args:
            series: A :class:`Series` member or a raw BCCh code string.
            desde: Start date, ``YYYY-MM-DD`` (required — no full-history
                downloads by accident).
            hasta: End date, ``YYYY-MM-DD`` (required).

        Returns:
            A :class:`SeriesResult` with typed ``Observation`` objects,
            ``source="api"``, and metadata from the API response.

        Raises:
            ValueError: If ``desde``/``hasta`` are not ``YYYY-MM-DD``.
            BcchApiError: On HTTP >= 400, network failure, API
                ``Codigo != 0``, or a JSON/parse failure.
        """
        identity, code = _resolve_series(series)
        parsed = self.fetch_raw(code, desde, hasta)
        observations = [
            Observation(date=obs["date"], value=obs["value"])
            for obs in parsed["observations"]
        ]
        meta = parsed["metadata"]
        return SeriesResult(
            series=identity,
            observations=observations,
            fetched_at=datetime.now(timezone.utc),
            source="api",
            metadata={
                "series_id": parsed["series_id"],
                "descripEsp": meta.get("descripEsp", ""),
                "descripIng": meta.get("descripIng", ""),
                "series_infos": meta.get("series_infos", []),
            },
        )

    def get_series(self, series: str | Series, desde: str, hasta: str) -> SeriesResult:
        """Legacy alias for :meth:`fetch` (kept for BcchClient compatibility)."""
        return self.fetch(series, desde, hasta)
