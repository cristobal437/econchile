"""
Tests for econchile.series_map — BCCh series enum catalog.

Run with:
    pytest tests/test_series_map.py -v

Validates all 28 v0.2 indexed series members, metadata correctness,
from_code lookup, uniqueness, and list_all.
"""

import pytest

from econchile.series_map import Series
from econchile.types import Frequency, Representation, SeriesMeta

EXPECTED_COUNT = 28

# ── member -> (code, frequency, representation) ──────────────────────────
# Every code here was live-verified against the BCCh API before inclusion
# (see specs/v02_indexed_catalog_spec.md).
CATALOG: dict[str, tuple[str, Frequency, Representation]] = {
    # FX & money
    "UF": ("F073.UFF.PRE.Z.D", Frequency.DAILY, Representation.LEVEL),
    "USD": ("F073.TCO.PRE.Z.D", Frequency.DAILY, Representation.LEVEL),
    "EURO": ("F072.EUR.USD.N.O.D", Frequency.DAILY, Representation.LEVEL),
    "TCM": ("F073.TCM.IND.199502.D", Frequency.DAILY, Representation.INDEX),
    "TCR": ("F073.TCR.IND.199101.M", Frequency.MONTHLY, Representation.INDEX),
    "UTM": ("F073.UTR.PRE.Z.M", Frequency.MONTHLY, Representation.LEVEL),
    "IVP": ("F073.IVP.PRE.Z.D", Frequency.DAILY, Representation.LEVEL),
    # Rates
    "TPM": ("F022.TPM.TIN.D001.NO.Z.D", Frequency.DAILY, Representation.LEVEL),
    "TASA_HIPOTECARIA": ("F022.VIV.TIP.MA03.UF.Z.M", Frequency.MONTHLY, Representation.LEVEL),
    # Prices
    "IPC_VAR": ("F074.IPC.VAR.Z.Z.C.M", Frequency.MONTHLY, Representation.MOM),
    "IPC_ANUAL": ("G073.IPC.V12.2023.M", Frequency.MONTHLY, Representation.YOY),
    "IPC_INDEX": ("F074.IPC.IND.Z.2023.C.M", Frequency.MONTHLY, Representation.INDEX),
    "IPC_SAE": ("F074.IPCSAE.VAR.Z.2023.C.M", Frequency.MONTHLY, Representation.MOM),
    "IPP": ("F075.IPP.IND.P0551.2014.Z.M", Frequency.MONTHLY, Representation.INDEX),
    # Activity
    "IMACEC": ("F032.IMC.IND.Z.Z.EP18.Z.Z.0.M", Frequency.MONTHLY, Representation.INDEX),
    "IMACEC_SA": ("F032.IMC.IND.Z.Z.EP18.Z.Z.1.M", Frequency.MONTHLY, Representation.INDEX),
    "IMACEC_NO_MINERO": ("F032.IMC.IND.Z.Z.EP18.N03.Z.0.M", Frequency.MONTHLY, Representation.INDEX),
    "PIB": ("F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T", Frequency.QUARTERLY, Representation.LEVEL),
    "PIB_SA": ("F032.PIB.FLU.R.CLP.EP18.Z.Z.1.T", Frequency.QUARTERLY, Representation.LEVEL),
    "PIB_CORRIENTE": ("F032.PIB.FLU.N.CLP.EP18.Z.Z.0.T", Frequency.QUARTERLY, Representation.LEVEL),
    "PIB_NO_MINERO": ("F032.PIB.FLU.R.CLP.EP18.N03.Z.0.T", Frequency.QUARTERLY, Representation.LEVEL),
    # Labor
    "DESEMPLEO": ("F049.DES.TAS.INE9.10.M", Frequency.MONTHLY, Representation.LEVEL),
    "FUERZA_TRABAJO": ("F049.FTR.PMT.INE9.01.M", Frequency.MONTHLY, Representation.LEVEL),
    "OCUPADOS": ("F049.OCU.PMT.INE9.01.M", Frequency.MONTHLY, Representation.LEVEL),
    # Expectations
    "TPM_EXPECTED": ("F089.TPM.TAS.11.M", Frequency.MONTHLY, Representation.LEVEL),
    "IPC_EXPECTED": ("F089.IPC.V12.14.M", Frequency.MONTHLY, Representation.YOY),
    # External + macro
    "EXPORTACIONES_COBRE": ("F068.B1.FLU.A1.0.C.N.Z.Z.Z.Z.6.0.M", Frequency.MONTHLY, Representation.LEVEL),
    "PIB_PER_CAPITA": ("F012.PPCP.FLU.N.7.AME.CL.USD.FMI.Z.0.A", Frequency.ANNUAL, Representation.LEVEL),
}


# ═══════════════════════════════════════════════════════════════════════════
# Series enum tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSeriesEnum:
    """Series enum members and their BCCh codes."""

    def test_member_count(self):
        """Series has exactly the expected number of members."""
        assert len(list(Series)) == EXPECTED_COUNT

    def test_all_catalog_members_exist(self):
        """Every catalog entry is a real Series member."""
        for name in CATALOG:
            assert hasattr(Series, name), f"missing member {name}"

    def test_is_str_enum(self):
        """Series is a subclass of both str and Enum."""
        assert issubclass(Series, str)
        from enum import Enum
        assert issubclass(Series, Enum)

    @pytest.mark.parametrize("name, code", [(n, c[0]) for n, c in CATALOG.items()])
    def test_member_code(self, name, code):
        """Each member's .value equals its verified BCCh code."""
        assert getattr(Series, name).value == code

    def test_codes_are_unique(self):
        """No two members share a BCCh code (catches copy-paste)."""
        codes = [m.value for m in Series]
        assert len(codes) == len(set(codes))


# ═══════════════════════════════════════════════════════════════════════════
# Meta tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMeta:
    """Series.meta() returns correct SeriesMeta."""

    @pytest.mark.parametrize("name, freq", [(n, c[1]) for n, c in CATALOG.items()])
    def test_frequency(self, name, freq):
        """Each series reports correct frequency."""
        s = getattr(Series, name)
        assert s.meta().frequency == freq

    @pytest.mark.parametrize("name, rep", [(n, c[2]) for n, c in CATALOG.items()])
    def test_representation(self, name, rep):
        """Each series reports correct representation tag."""
        s = getattr(Series, name)
        assert s.meta().representation == rep

    def test_meta_returns_series_meta(self):
        """meta() returns a SeriesMeta instance."""
        assert isinstance(Series.USD.meta(), SeriesMeta)

    def test_meta_has_spanish_title(self):
        """SeriesMeta includes a non-empty spanish_title."""
        meta = Series.UF.meta()
        assert isinstance(meta.spanish_title, str)
        assert len(meta.spanish_title) > 0


# ═══════════════════════════════════════════════════════════════════════════
# from_code tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFromCode:
    """Series.from_code() lookup."""

    @pytest.mark.parametrize("name, code", [(n, c[0]) for n, c in CATALOG.items()])
    def test_roundtrip_all_members(self, name, code):
        """Every member's code resolves back to itself."""
        assert Series.from_code(code) is getattr(Series, name)

    def test_unknown_code_raises_key_error(self):
        """from_code with unknown code raises KeyError."""
        with pytest.raises(KeyError):
            Series.from_code("F000.XXX.XXX.Z.Z.Z")


# ═══════════════════════════════════════════════════════════════════════════
# list_all tests
# ═══════════════════════════════════════════════════════════════════════════

class TestListAll:
    """Series.list_all() returns all members."""

    def test_returns_all_items(self):
        """list_all() returns exactly the expected number of members."""
        result = Series.list_all()
        assert len(result) == EXPECTED_COUNT

    def test_all_members_are_series_instances(self):
        """Every item in list_all() is a Series."""
        result = Series.list_all()
        for item in result:
            assert isinstance(item, Series)
