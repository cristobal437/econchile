"""
Response parsing for the BCCh REST API.

Takes raw HTTP response bodies (UTF-16 with BOM, plain UTF-8, or
latin-1 / ISO-8859-1), decodes them, validates the API-level status
code, and returns clean, typed Python dicts ready for downstream
consumption.

Uses ``converters.safe_float`` for value normalisation, inheriting its
crash-safe guarantees.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from econchile.converters import safe_float


# ─── Error hierarchy ────────────────────────────────────────────────────

class ParsingError(Exception):
    """API-level error returned by BCCh (``Codigo != 0``)."""

    def __init__(self, codigo: int, descripcion: str) -> None:
        self.codigo = codigo
        self.descripcion = descripcion
        super().__init__(f"BCCh API error {codigo}: {descripcion}")


class SeriesNotFoundError(ParsingError):
    """The requested series does not exist in the BCCh catalog."""


# ─── Public API ─────────────────────────────────────────────────────────

def parse_response(raw_text: str | bytes) -> dict[str, Any]:
    """Parse a raw BCCh API response into a clean, typed dict.

    Args:
        raw_text: Raw HTTP response body.  May be a decoded ``str``
            (UTF-8) or raw ``bytes`` with a UTF-16 BOM (``FF FE``).

    Returns:
        A dict with keys ``series_id``, ``observations``, and ``metadata``.

    Raises:
        ParsingError: If the API-level ``Codigo`` is non-zero.
    """
    # ── Decode bytes if needed ──────────────────────────────────────
    if isinstance(raw_text, bytes):
        # BCCh returns UTF-16 LE with BOM (bytes: FF FE).
        # Python's 'utf-16' codec auto-detects and strips the BOM.
        # Fallback order: UTF-16 BOM → UTF-8 → latin-1 (never fails).
        if raw_text[:2] == b'\xff\xfe':
            raw_text = raw_text.decode('utf-16')
        else:
            try:
                raw_text = raw_text.decode('utf-8')
            except UnicodeDecodeError:
                raw_text = raw_text.decode('latin-1')

    data: dict[str, Any] = json.loads(raw_text)

    if not isinstance(data, dict):
        raise ParsingError(-999, f"unexpected top-level type: {type(data).__name__}")

    # ── Validate API-level status ────────────────────────────────────
    codigo: int = data.get("Codigo", 0)
    descripcion: str = data.get("Descripcion", "")

    if codigo != 0:
        raise ParsingError(codigo, descripcion)

    # ── Validate structure ───────────────────────────────────────────
    series = data.get("Series")
    if not isinstance(series, dict):
        raise ParsingError(-999, f"Series missing or not a dict: {type(series).__name__}")

    series_id: str = series.get("seriesId", "")
    if not series_id:
        raise ParsingError(-999, "seriesId missing or empty")

    raw_obs = series.get("Obs", [])
    if not isinstance(raw_obs, list):
        raise ParsingError(-999, f"Obs is not a list: {type(raw_obs).__name__}")

    for obs in raw_obs:
        if not isinstance(obs, dict):
            raise ParsingError(-999, f"observation is not a dict: {type(obs).__name__}")
        if "indexDateString" not in obs or "statusCode" not in obs or "value" not in obs:
            raise ParsingError(-999, "observation missing required fields")

    # ── Build result ─────────────────────────────────────────────────
    return {
        "series_id": series_id,
        "observations": parse_observations(raw_obs),
        "metadata": {
            "descripEsp": series.get("descripEsp", ""),
            "descripIng": series.get("descripIng", ""),
            "series_infos": data.get("SeriesInfos", []),
        },
    }


def parse_observations(obs_list: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Convert raw BCCh observation dicts into clean ``{date, value}`` pairs.

    Args:
        obs_list: The raw ``Obs`` array from the API response.  Each
            element is a dict with keys ``indexDateString``, ``value``,
            and ``statusCode``.

    Returns:
        A list of observation dicts, each with:

        * ``date`` — ``str`` in ``YYYY-MM-DD`` format
        * ``value`` — ``float`` or ``None``
    """
    results: list[dict[str, Any]] = []

    for obs in obs_list:
        status: str = obs.get("statusCode", "")
        date_str: str = obs.get("indexDateString", "")

        # ── Parse the date ───────────────────────────────────────────
        # BCCh uses DD-MM-YYYY (e.g. "09-08-1982").
        try:
            dt = datetime.strptime(date_str, "%d-%m-%Y")
            date_iso = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_iso = date_str

        # ── Parse the value ──────────────────────────────────────────
        if status == "ND":
            # "No data" — return None regardless of the value string.
            # The value string is typically "NaN" in this case.
            value: float | None = None
        else:
            # "OK" or any other status — attempt numeric conversion.
            # safe_float handles comma decimals, "NaN" string, etc.
            raw_value: str = obs.get("value", "")
            value = safe_float(raw_value)

        results.append({"date": date_iso, "value": value})

    return results