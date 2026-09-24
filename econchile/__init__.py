"""econchile — Chilean macroeconomic data, made simple."""

__version__ = "0.2.2"

from econchile.client import BcchClient
from econchile.offline import OfflineClient
from econchile.series_map import Series
from econchile.types import (
    BcchApiError,
    BcchCacheError,
    BcchError,
    BcchOfflineError,
    Frequency,
    Observation,
    Representation,
    SeriesMeta,
    SeriesResult,
)

__all__ = [
    "BcchClient",
    "OfflineClient",
    "Series",
    "SeriesMeta",
    "SeriesResult",
    "Observation",
    "Frequency",
    "Representation",
    "BcchError",
    "BcchApiError",
    "BcchCacheError",
    "BcchOfflineError",
    "__version__",
]
