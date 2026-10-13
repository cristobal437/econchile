"""
Catalog label integrity (v0.2.1 trust fix).

The codes were always right; six labels were not.  Every expectation below
was verified against the live BCCh API (``descripEsp``) and the official
``series.xlsx`` catalog.  See specs/v021_trust_fix_spec.md.

Run with:
    pytest tests/test_catalog_labels.py -v
"""

import json
import os

import pytest

from econchile.series_map import Series
from econchile.types import Frequency

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
JSON_PATH = os.path.join(REPO_ROOT, "data", "indexed_series.json")
README_PATH = os.path.join(REPO_ROOT, "README.md")


class TestCorrectedLabels:
    """The six labels fixed in v0.2.1 say what BCCh says."""

    def test_euro_is_eur_per_usd(self):
        meta = Series.EURO.meta()
        assert "EUR per USD" in meta.english_title
        assert "USD per EUR" not in meta.english_title
        assert "USD por EUR" not in meta.spanish_title

    def test_tpm_expected_is_current_month(self):
        meta = Series.TPM_EXPECTED.meta()
        assert "current month" in meta.english_title
        assert "11 months" not in meta.english_title
        assert "11 meses" not in meta.spanish_title

    def test_ipc_sae_is_excluding_food_and_energy(self):
        meta = Series.IPC_SAE.meta()
        assert "sin alimentos ni energía" in meta.spanish_title
        assert "seasonally" not in meta.english_title.lower()

    def test_tcm_is_multilateral_base_1998(self):
        meta = Series.TCM.meta()
        assert "multilateral" in meta.spanish_title
        assert "1998=100" in meta.spanish_title
        assert "medio" not in meta.spanish_title

    def test_tcr_base_1986(self):
        meta = Series.TCR.meta()
        assert "1986=100" in meta.spanish_title
        assert "199101" not in meta.spanish_title

    def test_ivp_is_valor_promedio(self):
        meta = Series.IVP.meta()
        assert "valor promedio" in meta.spanish_title
        assert "Real" not in meta.english_title


class TestReadmeLabels:
    """README must not repeat the old wrong labels."""

    def test_readme_euro_direction(self):
        with open(README_PATH, encoding="utf-8") as f:
            readme = f.read()
        assert "USD per EUR" not in readme
        assert "EUR per USD" in readme


@pytest.fixture(scope="module")
def entries():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestIndexedJsonParity:
    """data/indexed_series.json must mirror the enum exactly."""

    def test_same_members_in_same_order(self, entries):
        assert [e["name"] for e in entries] == [m.name for m in Series]

    def test_fields_match_enum(self, entries):
        for e in entries:
            member = Series[e["name"]]
            meta = member.meta()
            assert e["code"] == member.value, e["name"]
            assert str(e["frequency"]).upper() == meta.frequency.value, e["name"]
            assert str(e["representation"]).upper() == meta.representation.value, e["name"]
            assert e["spanish_title"] == meta.spanish_title, e["name"]
            assert e["english_title"] == meta.english_title, e["name"]


class TestV03NewSeriesLabels:
    """The two v0.3 series say what BCCh says (live descripEsp, 2026-10).

    Live text captured for this pin:
      F072.CLP.EUR.N.O.D  "Tipo de cambio nominal euro"
      F089.TPM.TAS.14.M   "Expectativas tasa de política monetaria , dentro de
                           11 meses, mediana"
    """

    def test_clp_eur_is_clp_per_eur(self):
        """CLP per EUR, never the EUR-per-USD direction."""
        meta = Series.CLP_EUR.meta()
        assert meta.series_id == "F072.CLP.EUR.N.O.D"
        assert "CLP por EUR" in meta.spanish_title
        assert "CLP per EUR" in meta.english_title
        assert "EUR por USD" not in meta.spanish_title
        assert "EUR per USD" not in meta.english_title
        assert Series.CLP_EUR is not Series.EURO
        assert Series.CLP_EUR.value != Series.EURO.value

    def test_tpm_11m_is_eleven_months_ahead(self):
        """11 months ahead, not the current month, and not the spot TPM."""
        meta = Series.TPM_11M.meta()
        assert meta.series_id == "F089.TPM.TAS.14.M"
        assert "11 meses" in meta.spanish_title
        assert "11 months ahead" in meta.english_title
        assert "mes en curso" not in meta.spanish_title
        assert "current month" not in meta.english_title
        assert Series.TPM_11M.value not in (
            Series.TPM_EXPECTED.value,   # F089.TPM.TAS.11.M - current month
            Series.TPM.value,            # F022.TPM.TIN.D001.NO.Z.D - the rate itself
        )

    def test_new_series_carry_metadata(self):
        """Both are in the JSON catalog with the same titles as the enum."""
        for member in (Series.CLP_EUR, Series.TPM_11M):
            meta = member.meta()
            assert meta.spanish_title and meta.english_title
            assert meta.frequency == Frequency.MONTHLY or meta.frequency == Frequency.DAILY
