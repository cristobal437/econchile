# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-09-24

Trust fix: the library now says what BCCh says. No codes or values
changed. See `specs/v021_trust_fix_spec.md`.

### Fixed

- **Six series labels were wrong** (codes and data were always correct).
  Verified against the live API `descripEsp` and the official
  `series.xlsx` catalog:
  - `EURO` is **EUR per USD** (~0.87), not USD per EUR. Check:
    USD ÷ EURO = BCCh's own CLP per EUR (`F072.CLP.EUR.N.O.D`).
  - `TPM_EXPECTED` is the expected policy rate for the **current month**
    (EEE survey median), not 11 months ahead (that is `F089.TPM.TAS.14.M`).
  - `IPC_SAE` is CPI **sin alimentos ni energía** (core), not seasonally
    adjusted.
  - `TCM` is the **multilateral** nominal exchange rate (index 2 Jan
    1998=100), not "tipo de cambio medio".
  - `TCR` base is **average 1986=100**, not 199101=1.
  - `IVP` is **Índice de valor promedio**, not "valor real".
- **Raw BCCh codes now work in `BcchClient.get` and `OfflineClient.get`**,
  as the 0.2.0 README promised. Any code-shaped string outside the
  indexed enum passes through; `result.series` is the code string and it
  is cached under the code. Previously it raised `KeyError`.
- `desde` after `hasta` now raises `ValueError` before any cache or API
  call (previously it spent a network request).
- README: `source` (always `"api"` today) and `metadata` keys now
  describe the real behaviour; the offline fallback scope (same query,
  within the TTL) is stated plainly; the stale "token required for v0.1"
  line is gone; `search()` is documented as indexed-only.
- Stale docstrings (`v0.1 catalog`, DataFrame, `partial_series`),
  AGENTS.md (version, test count, 429 is retried), SECURITY.md (0.2.x).

### Added

- Top-level exports: `from econchile import OfflineClient, Observation,
  Frequency, Representation, BcchError, BcchApiError, BcchCacheError,
  BcchOfflineError`.
- `tests/test_catalog_labels.py`: label regression tests and
  `data/indexed_series.json` ↔ enum parity.
- `examples/charts_tutorial.ipynb` (didactic, multi-chart) and an
  `examples` extra (merged after 0.2.0).
- 331 tests (was 315).

## [0.2.0] - 2026-08-22

### Added

- **Indexed catalog expanded from 7 to 28 series** — every new code
  live-verified against the BCCh API before inclusion:
  - FX & money: EURO, TCM, TCR, UTM, IVP (plus existing UF, USD)
  - Rates: TASA_HIPOTECARIA (plus TPM)
  - Prices: IPC_ANUAL, IPC_SAE, IPP (plus IPC_VAR, IPC_INDEX)
  - Activity: IMACEC_SA, IMACEC_NO_MINERO, PIB_SA, PIB_CORRIENTE,
    PIB_NO_MINERO (plus IMACEC, PIB)
  - Labor: DESEMPLEO, FUERZA_TRABAJO, OCUPADOS
  - Expectations: TPM_EXPECTED, IPC_EXPECTED
  - External: EXPORTACIONES_COBRE
  - Macro: PIB_PER_CAPITA
- `data/indexed_series.json` — machine-readable catalog of the 28
  indexed series (name, code, frequency, representation, titles).
- README: 28-row "Indexed series" table + note that the full BCCh
  catalog (~30k series) is reachable via raw codes.

### Notes

- `EURO` (F072.EUR.USD.N.O.D) is **USD per EUR** (~0.86), not CLP/EUR —
  documented in the docstring.
- `IPP` last updated by BCCh in 2023-08 (upstream staleness, documented).
- `IPC_VAR` keeps its existing code (backwards-compatible — no breaking
  changes).

## [0.1.3] - 2026-08-18

### Fixed

- **PyPI publish now runs only on version tags** — the `publish` job is
  guarded with `if: startsWith(github.ref, 'refs/tags/v')`. Previously a
  pull request could trigger a publish (0.1.2 was actually published by a
  PR-triggered run); now PRs and main pushes can never publish.
- **Malformed API responses raise `ParsingError`** instead of leaking raw
  `AttributeError`/`KeyError` — `null`, `[]`, `{}`, missing/invalid
  `Series` or `seriesId`, and non-list `Obs` are all validated after
  decoding. A valid response with an empty `Obs` list still returns
  `observations=[]` (not an error).
- **HTTP 429 (rate limit) is now retried** like 5xx, with the same
  `max_retries`/backoff; persistent 429 raises a clean `BcchApiError`.

### Changed

- README: added the Windows PowerShell token setup
  (`$env:BCCH_TOKEN=...`) and precise offline wording ("falls back to
  previously cached results when the API is unavailable").

### Added

- Release-level contract tests (`tests/test_release.py`): CI publish
  guard, pyproject/`__version__` consistency, README auth docs.
- 13 new tests (223 total).

## [0.1.2] - 2026-08-07

### Fixed

- **`OfflineClient` works without a token** — the token is now optional at
  construction; it is validated at fetch time. A missing token raises
  `BcchApiError` (before any network I/O) and the existing cache fallback
  treats it like any other API failure, so cache-only usage works without
  `BCCH_TOKEN` set. `BcchClient` cache hits also work token-less.
  Previously `OfflineClient()` could not even be constructed without a
  token, defeating its documented "survives API outages" purpose.

### Changed

- `Fetcher.__init__` no longer raises `ValueError` when no token is
  configured — construction never requires credentials. Docstrings and
  README/AGENTS.md updated accordingly.

### Added

- 5 new contract tests (206 total): token-less construction, cache
  served without a token, `BcchOfflineError` when no token AND no cache.

## [0.1.1] - 2026-08-07

### Fixed

- **Latin-1 responses now decode** — the BCCh API sometimes serves
  ISO-8859-1 with raw accented bytes (e.g. USD, TPM, IPC series). Decode
  order is now UTF-16 BOM → UTF-8 → latin-1 (latin-1 never fails), in both
  the fetcher and the parsers. Previously these series raised a raw
  `UnicodeDecodeError`.
- **Decode failures wrap as `BcchApiError`** — a truncated UTF-16 body now
  surfaces as `BcchApiError`, matching the documented error contract,
  instead of a raw `UnicodeDecodeError`.
- **Transient failures are retried** — the fetcher retries network errors,
  HTTP ≥ 500, and non-JSON/HTML bodies up to `max_retries` (default 2) with
  exponential backoff (`retry_backoff * 2**attempt`). HTTP 4xx and
  `Codigo != 0` business errors are never retried.
- **`OfflineClient` no longer masks bugs** — the cache fallback now triggers
  only on API/network failures (`BcchApiError`, `requests.RequestException`,
  `OSError`). Programming errors propagate instead of being silently hidden
  behind stale-cache serving.
- **Tests skip gracefully without the 8MB fixture** — `test_parsers.py`
  skips when `sample_response.json` is absent (e.g. when running tests from
  an sdist install) instead of crashing.

### Added

- `Fetcher(max_retries=..., retry_backoff=...)` — configurable retry policy.
- `[project.urls]` in `pyproject.toml` — Homepage/Repository links on PyPI.
- CI now also runs on **pull requests** to `main` (previously only tag
  pushes and manual dispatch).
- 13 new tests (201 total, all offline).

### Changed

- Removed the stale `result.to_frame()` example from the `BcchClient`
  docstring (pandas integration is not shipped yet).
- `AGENTS.md` gotchas updated for the new decode order and retry policy.

## [0.1.0] - 2026-08-06

Initial release.

- BCCh SIE REST client with 7 core series: UF, USD, TPM, IPC_VAR,
  IPC_INDEX, IMACEC, PIB.
- Typed results (`SeriesResult`, `Observation`, `SeriesMeta`), explicit
  date-window queries (never silent full-history downloads).
- SQLite cache with 24h TTL at `~/.econchile/cache.db`.
- Two clients: `BcchClient` (cache-first, interactive) and `OfflineClient`
  (API-first with cache fallback, cron/scheduled jobs).
- Published to PyPI via GitHub Actions trusted publisher (OIDC).
