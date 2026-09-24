"""
Shared data structures for the econchile library.

Defines the enums and dataclasses that every module uses for
series metadata, observations, and value representation tagging.
Stdlib only — no external dependencies.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ─── Errors ───────────────────────────────────────────────────────────────

class BcchError(Exception):
    """Base error for all econchile failures."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        self.context: dict[str, Any] = kwargs
        super().__init__(message)


class BcchApiError(BcchError):
    """Raised when the BCCh API returns an error (Codigo != 0)."""


class BcchCacheError(BcchError):
    """Raised when SQLite cache read/write fails."""


class BcchOfflineError(BcchError):
    """Raised when all fallback layers (API + cache) are exhausted.

    ``self.context`` carries ``series``, ``desde``, ``hasta``,
    ``api_error`` (the original failure) and ``cache_had_data``.
    """


# ─── Internal data contract ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SeriesResult:
    """The internal contract that all modules agree on.

    *fetcher* produces it, *cache* stores it, *client* returns it,
    *offline* returns it on fallback.

    Attributes:
        series: The Series enum member this result is for, or the raw
            BCCh code string for series outside the indexed catalog.
        observations: Parsed, clean observations (date + value).
        fetched_at: When this result was produced (UTC).
        source: Always ``"api"`` today — cache hits return the stored
            result unchanged, so it keeps the value it was fetched with.
        metadata: As returned by the BCCh API: ``series_id``,
            ``descripEsp``, ``descripIng``, ``series_infos``.  For
            frequency / representation use ``Series.X.meta()``.
    """
    series: Any          # Series enum or raw code str — Any avoids a circular import
    observations: list   # list[Observation] — kept as `list` for simplicity
    fetched_at: datetime
    source: str = "api"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for SQLite storage / JSON cache."""
        return {
            "series": self.series.value if hasattr(self.series, "value") else str(self.series),
            "observations": [
                {"date": o.date, "value": o.value}
                if isinstance(o, Observation)
                else {"date": o["date"], "value": o["value"]}
                for o in self.observations
            ],
            "fetched_at": self.fetched_at.isoformat(),
            "source": self.source,
            "metadata": self.metadata,
        }


# ─── Enums ───────────────────────────────────────────────────────────────

class Frequency(str, Enum):
    """Standard frequency codes matching BCCh API values."""

    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class Representation(str, Enum):
    """Tags how a series value should be interpreted.

    Addresses the data gotcha where the same variable name means
    different things in different sources (e.g. IPC = MoM% in JSON
    but index level in CSV).
    """

    LEVEL = "LEVEL"
    """Absolute value (USD=950, PIB=51,629bn CLP)."""

    INDEX = "INDEX"
    """Index number with base year (IPC=103.5 base 2023)."""

    MOM = "MOM"
    """Month-over-month % change."""

    YOY = "YOY"
    """Year-over-year % change."""

    UNKNOWN = "UNKNOWN"
    """Not yet tagged (default)."""


# ─── Dataclasses ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SeriesMeta:
    """Static metadata about a BCCh series.

    Represents what you'd get from ``siete.buscar()`` — the
    descriptive information about a series, not its data points.

    Frozen (immutable) to prevent accidental mutation in downstream
    analysis code.
    """

    series_id: str
    """BCCh series code (e.g. ``F073.TCO.PRE.Z.D``)."""

    spanish_title: str
    """Title in Spanish."""

    english_title: str
    """Title in English."""

    frequency: Frequency
    """Temporal frequency of the series."""

    first_observation: str | None
    """First available date in ``YYYY-MM-DD`` format, or None."""

    last_observation: str | None
    """Last available date in ``YYYY-MM-DD`` format, or None."""

    representation: Representation = Representation.UNKNOWN
    """How to interpret values (defaults to UNKNOWN)."""


@dataclass(frozen=True)
class Observation:
    """A single data point in a BCCh series.

    Replaces the loose ``{\"date\": ..., \"value\": ...}`` dict that
    ``parsers.parse_observations`` currently returns, giving callers
    a properly typed, immutable container.

    Frozen (immutable) to prevent accidental mutation in downstream
    analysis code.
    """

    date: str
    """Date in ``YYYY-MM-DD`` format."""

    value: float | None
    """Numeric value, or None if the data point is missing."""
