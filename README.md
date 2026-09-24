# econchile

[![PyPI version](https://img.shields.io/pypi/v/econchile.svg)](https://pypi.org/project/econchile/)
[![Python versions](https://img.shields.io/pypi/pyversions/econchile.svg)](https://pypi.org/project/econchile/)
[![CI](https://github.com/cristobal437/econchile/actions/workflows/workflow.yml/badge.svg)](https://github.com/cristobal437/econchile/actions/workflows/workflow.yml)

Chilean macroeconomic data (Banco Central de Chile) for Python.

> [!WARNING]
> **Unofficial, community-driven project.** Not developed, endorsed, or
> supported by the Banco Central de Chile. Data is sourced from the BCCh
> public API; users are responsible for validating values against the
> official sources before making decisions based on them.

`econchile` is a thin, practical client for the BCCh SIE REST web service. It downloads official series (UF, USD, EURO, TPM, IPC, IMACEC, PIB, and more), parses them into clean, typed data, and keeps a local SQLite cache so repeat queries are instant and a script can fall back to its last good result when the API fails.

## Install

```bash
pip install econchile
```

Requires Python 3.10+. To install from source instead:

```bash
git clone https://github.com/cristobal437/econchile.git
cd econchile
pip install -e .
```

## Authentication

Request a free API token from the BCCh statistics database (SIE), see the official API documentation.

```bash
export BCCH_TOKEN="your-token-here"
```

Windows PowerShell:

```powershell
$env:BCCH_TOKEN="your-token-here"
```

The library reads `BCCH_TOKEN` from the environment — it does not load `.env` files itself. The token is only needed when the API is actually called (see Gotchas).

## Quickstart

```python
from econchile import BcchClient, Series

client = BcchClient()

# Last 3 months of the UF (daily)
result = client.get(Series.UF, "2024-01-01", "2024-03-31")

for obs in result.observations:
    print(obs.date, obs.value)
```

Dates are always `YYYY-MM-DD`. Missing observations have `value=None`.

## The two clients

| Client | Strategy | Best for |
|--------|----------|----------|
| `BcchClient` | **Cache-first** — serves from the local cache when fresh, hits the API only on a miss | Interactive use, repeated queries |
| `OfflineClient` | **API-first** — always tries the API, falls back to the cache when it fails | Cron jobs and scripts that must not crash |

```python
from econchile import OfflineClient, Series

client = OfflineClient()
result = client.get(Series.USD, "2024-01-01", "2024-03-31")  # if the API fails, serves the cached result of this same query (within the TTL)
```

## Indexed series (v0.2)

The library indexes 28 series for convenient access via `Series.NAME` or `client.get("name")`. All codes were live-verified against the BCCh API.

**FX & money**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.UF` | `F073.UFF.PRE.Z.D` | daily | Unidad de Fomento |
| `Series.USD` | `F073.TCO.PRE.Z.D` | daily | Nominal exchange rate (CLP/USD) |
| `Series.EURO` | `F072.EUR.USD.N.O.D` | daily | Euro per US dollar (EUR per USD, NOT CLP/EUR) |
| `Series.TCM` | `F073.TCM.IND.199502.D` | daily | Multilateral nominal exchange rate (index 2 Jan 1998=100) |
| `Series.TCR` | `F073.TCR.IND.199101.M` | monthly | Real exchange rate index (average 1986=100) |
| `Series.UTM` | `F073.UTR.PRE.Z.M` | monthly | Monthly Tax Unit (UTM) |
| `Series.IVP` | `F073.IVP.PRE.Z.D` | daily | Average value index (IVP) |

**Rates**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.TPM` | `F022.TPM.TIN.D001.NO.Z.D` | daily | Monetary policy rate |
| `Series.TASA_HIPOTECARIA` | `F022.VIV.TIP.MA03.UF.Z.M` | monthly | Mortgage lending rate (in UF) |

**Prices**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.IPC_VAR` | `F074.IPC.VAR.Z.Z.C.M` | monthly | CPI, month-over-month change |
| `Series.IPC_ANUAL` | `G073.IPC.V12.2023.M` | monthly | CPI, annual change (base 2023) |
| `Series.IPC_INDEX` | `F074.IPC.IND.Z.2023.C.M` | monthly | CPI general index (base 2023=100) |
| `Series.IPC_SAE` | `F074.IPCSAE.VAR.Z.2023.C.M` | monthly | CPI excluding food and energy (SAE), MoM change (base 2023) |
| `Series.IPP` | `F075.IPP.IND.P0551.2014.Z.M` | monthly | Producer price index (stale: BCCh stopped updating after 2023-08) |

**Activity**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.IMACEC` | `F032.IMC.IND.Z.Z.EP18.Z.Z.0.M` | monthly | Economic activity index, original (base 2018=100) |
| `Series.IMACEC_SA` | `F032.IMC.IND.Z.Z.EP18.Z.Z.1.M` | monthly | Economic activity index, seasonally adjusted (base 2018=100) |
| `Series.IMACEC_NO_MINERO` | `F032.IMC.IND.Z.Z.EP18.N03.Z.0.M` | monthly | Economic activity index, excluding mining (base 2018=100) |
| `Series.PIB` | `F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T` | quarterly | GDP, chained volumes (base 2018) |
| `Series.PIB_SA` | `F032.PIB.FLU.R.CLP.EP18.Z.Z.1.T` | quarterly | GDP, chained volumes, seasonally adjusted (base 2018) |
| `Series.PIB_CORRIENTE` | `F032.PIB.FLU.N.CLP.EP18.Z.Z.0.T` | quarterly | GDP, current prices (base 2018) |
| `Series.PIB_NO_MINERO` | `F032.PIB.FLU.R.CLP.EP18.N03.Z.0.T` | quarterly | GDP, chained volumes, excluding mining (base 2018) |

**Labor**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.DESEMPLEO` | `F049.DES.TAS.INE9.10.M` | monthly | Unemployment rate |
| `Series.FUERZA_TRABAJO` | `F049.FTR.PMT.INE9.01.M` | monthly | Labor force |
| `Series.OCUPADOS` | `F049.OCU.PMT.INE9.01.M` | monthly | Employed persons |

**Expectations**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.TPM_EXPECTED` | `F089.TPM.TAS.11.M` | monthly | Expected policy rate for the current month (median, EEE survey) |
| `Series.IPC_EXPECTED` | `F089.IPC.V12.14.M` | monthly | CPI inflation expectation, 12 months ahead (11 months forward) |

**External**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.EXPORTACIONES_COBRE` | `F068.B1.FLU.A1.0.C.N.Z.Z.Z.Z.6.0.M` | monthly | Copper exports |

**Macro**

| Series | BCCh code | Frequency | Meaning |
|--------|-----------|-----------|---------|
| `Series.PIB_PER_CAPITA` | `F012.PPCP.FLU.N.7.AME.CL.USD.FMI.Z.0.A` | annual | GDP per capita (PPP USD, IMF) |

### Any other BCCh series (raw codes)

The indexed list above covers the most-used macro series. Any other series in the BCCh catalog (~30k) works too: pass its code string to `client.get()`. The result's `series` field is then the code string instead of a `Series` member:

```python
eur_clp = client.get("F072.CLP.EUR.N.O.D", "2024-01-01", "2024-03-31")  # CLP per EUR, not indexed
```

`client.search("keyword")` searches the **indexed** series only (name, code, Spanish and English titles). To find other codes, use the BCCh catalog at [si3.bcentral.cl](https://si3.bcentral.cl/siete) or the `series.xlsx` asset on the [v0.2.0 release](https://github.com/cristobal437/econchile/releases/tag/v0.2.0).

```python
hits = client.search("ipc")
for meta in hits:
    print(meta.series_id, meta.spanish_title)
```

## Gotchas

- **Date format**: pass `YYYY-MM-DD` to `get()`; the library converts the API's native `DD-MM-YYYY` for you.
- **Missing data**: the BCCh API marks gaps as "ND". These become `value=None`, not zeros or exceptions, check for `None` before using a value.
- **Representations**: `IPC_VAR` is a monthly % change, `IPC_INDEX` is a base-2023 index. Same variable, different meaning.
- **Cache freshness**: cached results are reused for 24 hours by default; configure via `ttl_seconds` on `BcchClient(...)` or `OfflineClient(...)` (the cache lives at `~/.econchile/cache.db`).
- **Errors**: unknown series names raise `KeyError`, malformed or inverted dates (`desde` after `hasta`) raise `ValueError`, API failures raise `BcchApiError`, and `OfflineClient` raises `BcchOfflineError` when the cache fallback is also empty. Import them with `from econchile import BcchApiError, BcchOfflineError`. **No token?** Both clients construct fine without one — the token is only needed when the API is actually called: `BcchClient` cache hits work, and `OfflineClient` serves cached data (a missing token is treated as an API failure, so the cache fallback applies).
- **Offline fallback scope**: the cache stores whole queries. `OfflineClient` can only fall back to a result for the **same series and the same `desde`/`hasta`**, fetched within the TTL (24h by default). A query with a new date window (e.g. "up to today" in a daily cron) has nothing to fall back to.
- **Token with special characters**: BCCh API tokens may contain `/` characters. The library URL-encodes them automatically via `urllib.parse.urlencode` (`/` → `%2F`), so you can paste the token as-is. Only if you build request URLs *by hand* (e.g. `curl`) do you need to encode it yourself — `urllib.parse.quote(token)` — otherwise the BCCh API rejects the request.

## API

- `BcchClient.get(series, desde, hasta, use_cache=True)` — fetch a series over a date range; returns a `SeriesResult`.
- `OfflineClient.get(series, desde, hasta)` — same, but API-first with cache fallback.
- `BcchClient.search(keyword)` — case- and accent-insensitive catalog search → `list[SeriesMeta]`.
- `BcchClient.list_series()` — all series metadata → `list[SeriesMeta]`.
- `BcchClient.clear_cache()` — empty the local cache (returns the number of rows removed).

A `SeriesResult` has:

- `series` — the `Series` member (or the code string, for raw codes)
- `observations` — list of `Observation(date: str, value: float | None)`
- `fetched_at` — timestamp (UTC)
- `source` — always `"api"` today (cache hits return the stored result unchanged)
- `metadata` — as returned by BCCh: `series_id`, `descripEsp`, `descripIng`, `series_infos`. For frequency and representation use `Series.X.meta()`

## Development

```bash
pip install -e ".[test]"
python -m pytest tests/
```

Want to see the whole library in action? Run the interactive walkthrough, or open the charts tutorial:

```bash
jupyter notebook examples/econchile_walkthrough.ipynb
jupyter notebook examples/charts_tutorial.ipynb
```

Works without a token for the first sections (catalog, search, errors, offline) — only the live-data cells need `BCCH_TOKEN`.

## License

MIT
