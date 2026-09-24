"""
BCCh macroeconomic series — v0.2 indexed catalog.

Each member maps a human-readable name to a BCCh series code.
Metadata (frequency, representation) is attached via :meth:`meta`.

Usage::

    >>> Series.USD
    <Series.USD: 'F073.TCO.PRE.Z.D'>
    >>> Series.USD.value
    'F073.TCO.PRE.Z.D'
    >>> Series.USD.meta()
    SeriesMeta(series_id='F073.TCO.PRE.Z.D', ...)
"""

from enum import Enum

from econchile.types import Frequency, Representation, SeriesMeta


class Series(str, Enum):
    """BCCh macroeconomic series — v0.2 indexed catalog."""

    # ── FX & money ──
    UF = "F073.UFF.PRE.Z.D"
    USD = "F073.TCO.PRE.Z.D"
    EURO = "F072.EUR.USD.N.O.D"  # EUR per USD (NOT CLP/EUR; that is F072.CLP.EUR.N.O.D)
    TCM = "F073.TCM.IND.199502.D"
    TCR = "F073.TCR.IND.199101.M"
    UTM = "F073.UTR.PRE.Z.M"
    IVP = "F073.IVP.PRE.Z.D"

    # ── Rates ──
    TPM = "F022.TPM.TIN.D001.NO.Z.D"
    TASA_HIPOTECARIA = "F022.VIV.TIP.MA03.UF.Z.M"

    # ── Prices ──
    IPC_VAR = "F074.IPC.VAR.Z.Z.C.M"
    IPC_ANUAL = "G073.IPC.V12.2023.M"
    IPC_INDEX = "F074.IPC.IND.Z.2023.C.M"
    IPC_SAE = "F074.IPCSAE.VAR.Z.2023.C.M"
    IPP = "F075.IPP.IND.P0551.2014.Z.M"  # stale: BCCh stopped updating after 2023-08

    # ── Activity ──
    IMACEC = "F032.IMC.IND.Z.Z.EP18.Z.Z.0.M"
    IMACEC_SA = "F032.IMC.IND.Z.Z.EP18.Z.Z.1.M"
    IMACEC_NO_MINERO = "F032.IMC.IND.Z.Z.EP18.N03.Z.0.M"
    PIB = "F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T"
    PIB_SA = "F032.PIB.FLU.R.CLP.EP18.Z.Z.1.T"
    PIB_CORRIENTE = "F032.PIB.FLU.N.CLP.EP18.Z.Z.0.T"
    PIB_NO_MINERO = "F032.PIB.FLU.R.CLP.EP18.N03.Z.0.T"

    # ── Labor ──
    DESEMPLEO = "F049.DES.TAS.INE9.10.M"
    FUERZA_TRABAJO = "F049.FTR.PMT.INE9.01.M"
    OCUPADOS = "F049.OCU.PMT.INE9.01.M"

    # ── Expectations ──
    TPM_EXPECTED = "F089.TPM.TAS.11.M"
    IPC_EXPECTED = "F089.IPC.V12.14.M"

    # ── External ──
    EXPORTACIONES_COBRE = "F068.B1.FLU.A1.0.C.N.Z.Z.Z.Z.6.0.M"

    # ── Macro ──
    PIB_PER_CAPITA = "F012.PPCP.FLU.N.7.AME.CL.USD.FMI.Z.0.A"

    def meta(self) -> SeriesMeta:
        """Return static metadata for this series."""
        return _META_MAP[self]

    @classmethod
    def from_code(cls, code: str) -> "Series":
        """Look up a Series member by BCCh code.

        Raises KeyError if the code is not in the indexed catalog.
        """
        for member in cls:
            if member.value == code:
                return member
        raise KeyError(code)

    @classmethod
    def list_all(cls) -> list["Series"]:
        """Return all indexed series as a list."""
        return list(cls)


# ── Internal metadata lookup table ──────────────────────────────────────────

_META_MAP: dict[Series, SeriesMeta] = {
    Series.UF: SeriesMeta(
        series_id=Series.UF.value,
        spanish_title="Unidad de Fomento (UF)",
        english_title="Unidad de Fomento (UF)",
        frequency=Frequency.DAILY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.USD: SeriesMeta(
        series_id=Series.USD.value,
        spanish_title="Tipo de cambio nominal (dólar observado $CLP/USD)",
        english_title="Nominal exchange rate (Observed dollar $CLP/USD)",
        frequency=Frequency.DAILY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.EURO: SeriesMeta(
        series_id=Series.EURO.value,
        spanish_title="Euro por dólar de EE.UU. (EUR por USD)",
        english_title="Euro per US dollar (EUR per USD)",
        frequency=Frequency.DAILY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.TCM: SeriesMeta(
        series_id=Series.TCM.value,
        spanish_title="Tipo de cambio nominal multilateral (TCM, índice 2 enero 1998=100)",
        english_title="Multilateral nominal exchange rate (TCM, index 2 Jan 1998=100)",
        frequency=Frequency.DAILY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.TCR: SeriesMeta(
        series_id=Series.TCR.value,
        spanish_title="Tipo de cambio real (TCR, índice promedio 1986=100)",
        english_title="Real exchange rate index (TCR, average 1986=100)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.UTM: SeriesMeta(
        series_id=Series.UTM.value,
        spanish_title="Unidad Tributaria Mensual (UTM)",
        english_title="Monthly Tax Unit (UTM)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.IVP: SeriesMeta(
        series_id=Series.IVP.value,
        spanish_title="Índice de valor promedio (IVP)",
        english_title="Average value index (IVP)",
        frequency=Frequency.DAILY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.TPM: SeriesMeta(
        series_id=Series.TPM.value,
        spanish_title="Tasa de política monetaria (TPM)",
        english_title="Monetary policy rate (MPR)",
        frequency=Frequency.DAILY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.TASA_HIPOTECARIA: SeriesMeta(
        series_id=Series.TASA_HIPOTECARIA.value,
        spanish_title="Tasa de interés de créditos hipotecarios (en UF)",
        english_title="Mortgage lending rate (in UF)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.IPC_VAR: SeriesMeta(
        series_id=Series.IPC_VAR.value,
        spanish_title="IPC variación mensual",
        english_title="CPI monthly change",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.MOM,
    ),
    Series.IPC_ANUAL: SeriesMeta(
        series_id=Series.IPC_ANUAL.value,
        spanish_title="IPC variación anual (base 2023)",
        english_title="CPI annual change (base 2023)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.YOY,
    ),
    Series.IPC_INDEX: SeriesMeta(
        series_id=Series.IPC_INDEX.value,
        spanish_title="IPC índice general base 2023=100",
        english_title="CPI general index base 2023=100",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.IPC_SAE: SeriesMeta(
        series_id=Series.IPC_SAE.value,
        spanish_title="IPC SAE (sin alimentos ni energía), variación mensual (base 2023)",
        english_title="CPI excluding food and energy (SAE), monthly change (base 2023)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.MOM,
    ),
    Series.IPP: SeriesMeta(
        series_id=Series.IPP.value,
        spanish_title="Índice de precios de producción (IPP) — BCCh dejó de actualizar después de 2023-08",
        english_title="Producer price index (PPI) — BCCh stopped updating after 2023-08",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.IMACEC: SeriesMeta(
        series_id=Series.IMACEC.value,
        spanish_title="Imacec empalmado, serie original (índice 2018=100)",
        english_title="Imacec spliced, original series (index 2018=100)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.IMACEC_SA: SeriesMeta(
        series_id=Series.IMACEC_SA.value,
        spanish_title="Imacec empalmado, desestacionalizado (índice 2018=100)",
        english_title="Imacec spliced, seasonally adjusted (index 2018=100)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.IMACEC_NO_MINERO: SeriesMeta(
        series_id=Series.IMACEC_NO_MINERO.value,
        spanish_title="Imacec empalmado, sin minería (índice 2018=100)",
        english_title="Imacec spliced, excluding mining (index 2018=100)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.INDEX,
    ),
    Series.PIB: SeriesMeta(
        series_id=Series.PIB.value,
        spanish_title="PIB, volumen a precios del año anterior encadenado (base 2018)",
        english_title="GDP, volume at previous year prices chained (base 2018)",
        frequency=Frequency.QUARTERLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.PIB_SA: SeriesMeta(
        series_id=Series.PIB_SA.value,
        spanish_title="PIB, volumen a precios del año anterior encadenado, desestacionalizado (base 2018)",
        english_title="GDP, volume at previous year prices chained, seasonally adjusted (base 2018)",
        frequency=Frequency.QUARTERLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.PIB_CORRIENTE: SeriesMeta(
        series_id=Series.PIB_CORRIENTE.value,
        spanish_title="PIB, valores corrientes (base 2018)",
        english_title="GDP, current prices (base 2018)",
        frequency=Frequency.QUARTERLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.PIB_NO_MINERO: SeriesMeta(
        series_id=Series.PIB_NO_MINERO.value,
        spanish_title="PIB, volumen a precios del año anterior encadenado, sin minería (base 2018)",
        english_title="GDP, volume at previous year prices chained, excluding mining (base 2018)",
        frequency=Frequency.QUARTERLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.DESEMPLEO: SeriesMeta(
        series_id=Series.DESEMPLEO.value,
        spanish_title="Tasa de desocupación",
        english_title="Unemployment rate",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.FUERZA_TRABAJO: SeriesMeta(
        series_id=Series.FUERZA_TRABAJO.value,
        spanish_title="Fuerza de trabajo",
        english_title="Labor force",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.OCUPADOS: SeriesMeta(
        series_id=Series.OCUPADOS.value,
        spanish_title="Personas ocupadas",
        english_title="Employed persons",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.TPM_EXPECTED: SeriesMeta(
        series_id=Series.TPM_EXPECTED.value,
        # BCCh descripEsp: "en el mes, mediana" (Encuesta de Expectativas
        # Económicas). NOT 11 months ahead; that is F089.TPM.TAS.14.M.
        spanish_title="Expectativa de TPM para el mes en curso (mediana, EEE)",
        english_title="Expected monetary policy rate, current month (median, EEE survey)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.IPC_EXPECTED: SeriesMeta(
        series_id=Series.IPC_EXPECTED.value,
        spanish_title="Expectativa de inflación IPC a 12 meses (11 meses adelante)",
        english_title="CPI inflation expectation 12 months ahead (11 months forward)",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.YOY,
    ),
    Series.EXPORTACIONES_COBRE: SeriesMeta(
        series_id=Series.EXPORTACIONES_COBRE.value,
        spanish_title="Exportaciones de cobre",
        english_title="Copper exports",
        frequency=Frequency.MONTHLY,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
    Series.PIB_PER_CAPITA: SeriesMeta(
        series_id=Series.PIB_PER_CAPITA.value,
        spanish_title="PIB per cápita (USD PPP, FMI)",
        english_title="GDP per capita (PPP USD, IMF)",
        frequency=Frequency.ANNUAL,
        first_observation=None,
        last_observation=None,
        representation=Representation.LEVEL,
    ),
}