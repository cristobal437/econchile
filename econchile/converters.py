"""
Data normalization utilities for Chilean macroeconomic sources.

Handles the messy reality of government data: European decimal commas,
Spanish month names, inconsistent null sentinels, and mixed date formats.
Every public function is guaranteed non-crashing — they return None for
unparseable input rather than raising exceptions.
"""

from typing import Any
import re

#: Strings that unambiguously mean "no data available" across Chilean sources.
#: Lowercase for case-insensitive comparison. Zero is deliberately excluded
#: because it can be legitimate data (e.g., trade balance = 0).
NULL_SENTINELS: set[str] = {
    "", "n/a", "na", "null", "none", "...", "-", "nd", "nan",
}


def safe_float(value: Any) -> float | None:
    """Convert *any* value to a float, returning None on failure.

    Handles European decimal commas (``"47,29"`` → ``47.29``),
    null sentinels (``"N/A"``, ``"..."``, etc.), and already-numeric
    values. Never raises — unparseable strings silently return None.

    Args:
        value: A float, int, string, None, or anything else.

    Returns:
        The numeric value as a float, or None if the input represents
        missing or unparseable data.

    Examples:
        >>> safe_float("47,29")
        47.29
        >>> safe_float("N/A")
        None
        >>> safe_float(None)
        None
        >>> safe_float(5.61)
        5.61
    """
    if value is None:
        return None

    s: str = str(value).strip().lower()

    if s in NULL_SENTINELS:
        return None

    s = s.replace(",", ".")

    try:
        return float(s)
    except ValueError:
        return None


def safe_date(value: Any) -> str | None:
    """Parse a date-like string into canonical ``YYYY-MM-DD`` format.

    Recognises three date formats commonly found in Chilean macro data:

    * ``"mar.1996"`` — Spanish three-letter month + dot + four-digit year
    * ``"1996-03"``  — ISO year-month
    * ``"03/1996"``  — month/year with slash

    The day is always set to ``"01"`` since Chilean macro series are
    monthly or quarterly.

    Args:
        value: A date string, None, or sentinel.

    Returns:
        A ``"YYYY-MM-DD"`` string, or None if the input could not be parsed.

    Examples:
        >>> safe_date("mar.1996")
        '1996-03-01'
        >>> safe_date("1977-08")
        '1977-08-01'
        >>> safe_date("")
        None
    """
    if value is None:
        return None

    s: str = str(value).strip()

    if not s or s.lower() in NULL_SENTINELS:
        return None

    # Spanish month: "ene.2024", "mar.1996", etc.
    match_es = re.match(r"^([a-z]{3,4})\.(\d{4})$", s, re.IGNORECASE)
    if match_es:
        spanish_months = {
            "ene": "01", "feb": "02", "mar": "03", "abr": "04",
            "may": "05", "jun": "06", "jul": "07", "ago": "08",
            "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dic": "12",
        }
        month = spanish_months.get(match_es.group(1).lower())
        if month:
            return f"{match_es.group(2)}-{month}-01"

    # ISO year-month: "1996-03"
    match = re.match(r"^(\d{4})-(\d{2})$", s)
    if match:
        return f"{match.group(1)}-{match.group(2)}-01"

    # Slash month/year: "03/1996"
    match = re.match(r"^(\d{2})/(\d{4})$", s)
    if match:
        return f"{match.group(2)}-{match.group(1)}-01"

    return None


def normalize_series(values: list[Any]) -> list[float | None]:
    """Apply :func:`safe_float` to every element in a list.

    Args:
        values: Raw values from a data column.

    Returns:
        A new list where each element is either a float or None.
    """
    return [safe_float(v) for v in values]

