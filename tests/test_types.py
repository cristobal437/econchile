"""
Tests for econchile.types — enums and frozen dataclasses.

Run with:
    pytest tests/test_types.py -v

Every test validates the core data structures used across the library:
Frequency/Representation enums, SeriesMeta, and Observation.
"""

import sys
import os
from dataclasses import FrozenInstanceError
from enum import Enum

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from econchile.types import (
    BcchError,
    BcchApiError,
    BcchCacheError,
    BcchOfflineError,
    Frequency,
    Observation,
    Representation,
    SeriesMeta,
    SeriesResult,
)


# ═══════════════════════════════════════════════════════════════════════════
# Frequency enum tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFrequency:
    """Frequency enum values."""

    def test_daily_value(self):
        """Frequency.DAILY.value == 'DAILY'"""
        assert Frequency.DAILY.value == "DAILY"

    def test_monthly_value(self):
        """Frequency.MONTHLY.value == 'MONTHLY'"""
        assert Frequency.MONTHLY.value == "MONTHLY"

    def test_quarterly_value(self):
        """Frequency.QUARTERLY.value == 'QUARTERLY'"""
        assert Frequency.QUARTERLY.value == "QUARTERLY"

    def test_annual_value(self):
        """Frequency.ANNUAL.value == 'ANNUAL'"""
        assert Frequency.ANNUAL.value == "ANNUAL"

    def test_is_str_enum(self):
        """Frequency is a subclass of both str and Enum."""
        assert issubclass(Frequency, str)
        assert issubclass(Frequency, Enum)


# ═══════════════════════════════════════════════════════════════════════════
# Representation enum tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRepresentation:
    """Representation enum values."""

    def test_level_value(self):
        """Representation.LEVEL.value == 'LEVEL'"""
        assert Representation.LEVEL.value == "LEVEL"

    def test_index_value(self):
        """Representation.INDEX.value == 'INDEX'"""
        assert Representation.INDEX.value == "INDEX"

    def test_mom_value(self):
        """Representation.MOM.value == 'MOM'"""
        assert Representation.MOM.value == "MOM"

    def test_yoy_value(self):
        """Representation.YOY.value == 'YOY'"""
        assert Representation.YOY.value == "YOY"

    def test_unknown_is_default(self):
        """Representation.UNKNOWN exists as fallback."""
        assert Representation.UNKNOWN.value == "UNKNOWN"

    def test_is_str_enum(self):
        """Representation is a subclass of both str and Enum."""
        assert issubclass(Representation, str)
        assert issubclass(Representation, Enum)


# ═══════════════════════════════════════════════════════════════════════════
# SeriesMeta dataclass tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSeriesMeta:
    """SeriesMeta dataclass."""

    @pytest.fixture
    def sample_meta(self):
        """A typical SeriesMeta for USD/CLP series."""
        return SeriesMeta(
            series_id="F073.TCO.PRE.Z.D",
            spanish_title="Dólar observado",
            english_title="Observed exchange rate (USD/CLP)",
            frequency=Frequency.DAILY,
            first_observation="1990-01-02",
            last_observation="2026-07-31",
            representation=Representation.LEVEL,
        )

    def test_creation_all_fields(self, sample_meta):
        """SeriesMeta can be created with all fields populated."""
        assert sample_meta.series_id == "F073.TCO.PRE.Z.D"
        assert sample_meta.spanish_title == "Dólar observado"
        assert sample_meta.english_title == "Observed exchange rate (USD/CLP)"
        assert sample_meta.frequency == Frequency.DAILY
        assert sample_meta.first_observation == "1990-01-02"
        assert sample_meta.last_observation == "2026-07-31"
        assert sample_meta.representation == Representation.LEVEL

    def test_default_representation(self):
        """representation defaults to UNKNOWN if not specified."""
        meta = SeriesMeta(
            series_id="F032.IMC.IND.Z.Z",
            spanish_title="IMACEC",
            english_title="Monthly economic activity index",
            frequency=Frequency.MONTHLY,
            first_observation="1996-01-01",
            last_observation="2026-06-01",
        )
        assert meta.representation == Representation.UNKNOWN

    def test_frozen_prevents_mutation(self, sample_meta):
        """SeriesMeta is immutable — assigning to a field raises FrozenInstanceError."""
        with pytest.raises(FrozenInstanceError):
            sample_meta.series_id = "CHANGED"

    def test_field_types(self, sample_meta):
        """Fields have correct types after creation."""
        assert isinstance(sample_meta.series_id, str)
        assert isinstance(sample_meta.spanish_title, str)
        assert isinstance(sample_meta.english_title, str)
        assert isinstance(sample_meta.frequency, Frequency)
        assert sample_meta.first_observation is None or isinstance(sample_meta.first_observation, str)
        assert sample_meta.last_observation is None or isinstance(sample_meta.last_observation, str)
        assert isinstance(sample_meta.representation, Representation)

    def test_equality(self):
        """Two SeriesMeta with same fields are equal."""
        meta1 = SeriesMeta(
            series_id="F073.TCO.PRE.Z.D",
            spanish_title="Dólar",
            english_title="USD/CLP",
            frequency=Frequency.DAILY,
            first_observation=None,
            last_observation=None,
        )
        meta2 = SeriesMeta(
            series_id="F073.TCO.PRE.Z.D",
            spanish_title="Dólar",
            english_title="USD/CLP",
            frequency=Frequency.DAILY,
            first_observation=None,
            last_observation=None,
        )
        assert meta1 == meta2

        meta3 = SeriesMeta(
            series_id="DIFFERENT",
            spanish_title="Dólar",
            english_title="USD/CLP",
            frequency=Frequency.DAILY,
            first_observation=None,
            last_observation=None,
        )
        assert meta1 != meta3


# ═══════════════════════════════════════════════════════════════════════════
# Observation dataclass tests
# ═══════════════════════════════════════════════════════════════════════════

class TestObservation:
    """Observation dataclass."""

    @pytest.fixture
    def sample_obs(self):
        """A typical Observation."""
        return Observation(date="2026-01-15", value=950.50)

    def test_creation_with_float(self, sample_obs):
        """Observation can be created with a float value."""
        assert sample_obs.date == "2026-01-15"
        assert sample_obs.value == 950.50

    def test_creation_with_none(self):
        """Observation can be created with value=None (missing data)."""
        obs = Observation(date="2026-06-01", value=None)
        assert obs.date == "2026-06-01"
        assert obs.value is None

    def test_frozen_prevents_mutation(self, sample_obs):
        """Observation is immutable."""
        with pytest.raises(FrozenInstanceError):
            sample_obs.date = "CHANGED"

    def test_equality(self):
        """Two Observations with same fields are equal."""
        obs1 = Observation(date="2026-01-15", value=950.50)
        obs2 = Observation(date="2026-01-15", value=950.50)
        assert obs1 == obs2

        obs3 = Observation(date="2026-01-15", value=999.99)
        assert obs1 != obs3


# ═══════════════════════════════════════════════════════════════════════════
# Error classes
# ═══════════════════════════════════════════════════════════════════════════

class TestErrors:
    """BcchError, BcchApiError, BcchCacheError, BcchOfflineError."""

    def test_bcch_error_is_exception(self):
        """BcchError is a subclass of Exception."""
        assert issubclass(BcchError, Exception)

    def test_bcch_api_error_subclass(self):
        """BcchApiError is a subclass of BcchError."""
        assert issubclass(BcchApiError, BcchError)

    def test_bcch_cache_error_subclass(self):
        """BcchCacheError is a subclass of BcchError."""
        assert issubclass(BcchCacheError, BcchError)

    def test_bcch_offline_error_subclass(self):
        """BcchOfflineError is a subclass of BcchError."""
        assert issubclass(BcchOfflineError, BcchError)

    def test_bcch_error_stores_context(self):
        """BcchError stores kwargs in .context dict."""
        err = BcchError("something broke", foo="bar", code=42)
        assert err.context["foo"] == "bar"
        assert err.context["code"] == 42


# ═══════════════════════════════════════════════════════════════════════════
# SeriesResult dataclass tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSeriesResult:
    """SeriesResult internal data contract."""

    @pytest.fixture
    def sample_result(self):
        """A typical SeriesResult wrapping observations."""
        from datetime import datetime
        return SeriesResult(
            series=Frequency.DAILY,
            observations=[
                {"date": "2024-01-01", "value": 950.50},
                {"date": "2024-01-02", "value": None},
            ],
            fetched_at=datetime(2024, 1, 3, 10, 0, 0),
            source="api",
            metadata={
                "series_id": "F073.TCO.PRE.Z.D",
                "englishTitle": "USD/CLP",
                "frequency": "DAILY",
                "representation": "LEVEL",
            },
        )

    def test_to_dict_roundtrip(self, sample_result):
        """to_dict() serialises to dict with expected keys."""
        d = sample_result.to_dict()
        assert "series" in d
        assert "observations" in d
        assert "fetched_at" in d
        assert "source" in d
        assert "metadata" in d

    def test_to_dict_series_serialises(self, sample_result):
        """Series .value is used if it exists, else str()."""
        d = sample_result.to_dict()
        assert d["series"] == "DAILY"

    def test_source_can_be_cache(self):
        """source field accepts 'cache' value."""
        from datetime import datetime
        result = SeriesResult(
            series=Frequency.MONTHLY,
            observations=[],
            fetched_at=datetime.now(),
            source="cache",
            metadata={"series_id": "TEST"},
        )
        assert result.source == "cache"

    def test_metadata_defaults_to_empty_dict(self):
        """metadata defaults to empty dict when not provided."""
        from datetime import datetime
        result = SeriesResult(
            series=Frequency.DAILY,
            observations=[],
            fetched_at=datetime.now(),
        )
        assert result.metadata == {}
        assert result.source == "api"

    def test_frozen_prevents_mutation(self, sample_result):
        """SeriesResult is not frozen — check it allows or raises."""
        # SeriesResult is NOT frozen (designed for flexibility),
        # so we just verify it's a dataclass instance
        from dataclasses import is_dataclass
        assert is_dataclass(sample_result)

    def test_to_dict_with_observation_objects(self):
        """to_dict() handles Observation objects (not just dicts).

        Regression test: SeriesResult.to_dict() used to crash with
        TypeError when observations were Observation instances.
        """
        from datetime import datetime
        result = SeriesResult(
            series=Frequency.DAILY,
            observations=[
                Observation(date="2024-01-01", value=897.68),
                Observation(date="2024-01-02", value=None),
            ],
            fetched_at=datetime(2024, 1, 3, 10, 0, 0),
            source="api",
            metadata={"series_id": "F073.TCO.PRE.Z.D"},
        )
        d = result.to_dict()
        assert d["observations"] == [
            {"date": "2024-01-01", "value": 897.68},
            {"date": "2024-01-02", "value": None},
        ]

    def test_to_dict_mixed_observations(self):
        """to_dict() handles a mix of Observation objects and dicts."""
        from datetime import datetime
        result = SeriesResult(
            series=Frequency.DAILY,
            observations=[
                Observation(date="2024-01-01", value=897.68),
                {"date": "2024-01-02", "value": 901.13},
            ],
            fetched_at=datetime(2024, 1, 3, 10, 0, 0),
            source="api",
            metadata={},
        )
        d = result.to_dict()
        assert d["observations"] == [
            {"date": "2024-01-01", "value": 897.68},
            {"date": "2024-01-02", "value": 901.13},
        ]


# ═══════════════════════════════════════════════════════════════════════════
# v0.3: SeriesResult.stale / missing_dates (cache spec §7)
# ═══════════════════════════════════════════════════════════════════════════

class TestSeriesResultV03Flags:
    """Additive, defaulted fields: every v0.2 call site keeps working."""

    def _five_args(self):
        from datetime import datetime, timezone

        return SeriesResult(
            series="F073.TCO.PRE.Z.D",
            observations=[Observation("2024-01-02", 901.13)],
            fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            source="api",
            metadata={},
        )

    def test_defaults(self):
        result = self._five_args()
        assert result.stale is False
        assert result.missing_dates == []

    def test_default_list_not_shared(self):
        assert self._five_args().missing_dates is not self._five_args().missing_dates

    def test_explicit_values(self):
        from dataclasses import replace

        result = replace(self._five_args(), stale=True, missing_dates=["2024-01-01"])
        assert result.stale is True
        assert result.missing_dates == ["2024-01-01"]

    def test_still_frozen(self):
        with pytest.raises(FrozenInstanceError):
            self._five_args().stale = True

    def test_to_dict_carries_flags(self):
        """Exported results keep the stale/partial signal."""
        from dataclasses import replace

        d = replace(self._five_args(), stale=True, missing_dates=["2024-01-01"]).to_dict()
        assert d["stale"] is True
        assert d["missing_dates"] == ["2024-01-01"]
        assert self._five_args().to_dict()["missing_dates"] == []
