# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-10-23

Security patch for the eMudhra / emSign CA compromise. The private keys of
eight eMudhra intermediate CAs were recovered from the certificates
themselves, so an attacker holding them can impersonate any site for any
client that still trusts the emSign roots. Browser vendors pushed revocation
on 17-22 Oct; everything else needs a software update.

### Security

- Require `certifi>=2026.10.19`, the first release with the compromised
  emSign root removed (GHSA-6q48-6c7w-9gw2). It was previously only a
  transitive dependency of `requests`, so econchile could be installed onto
  an environment whose bundle still trusted the compromised root. `requests`
  verifies TLS against certifi's bundle and nothing else, so this floor is
  what actually changes what an installed econchile trusts. No API change.

### Notes

- Upgrading econchile does not, by itself, fix your machine: each tool
  carries its own trust store. pip has a vendored certifi, uv bundles its
  own roots, and Git for Windows ships a separate CA bundle. Patched
  versions per tool: certifi 2026.10.19, pip 26.2.2, uv 0.13.5, Git for
  Windows 2.56.0.3.

## [0.3.0] - 2026-10-13

Observation-level cache: the cache stores individual data points now, so the
offline fallback can serve a date window it was never asked for. This is the
release that makes daily "up to today" cron jobs survive an API outage.
See `specs/v0_3_cache_architecture.md` and `specs/v0_3_minor_updates.md`.

### Added

- Observation-level SQLite cache (schema v1): `meta`, `observations`,
  `series_meta` and `fetched_windows` replace the single key-based `cache`
  table. A v0.2 cache migrates automatically in one transaction (newest
  `fetched_at` wins per series/date, and the old keys become fetched windows,
  so repeated queries still hit). Any failure rolls back and leaves the
  original cache intact, and a cache written by a *newer* econchile is
  refused untouched instead of being re-migrated.
- `SeriesResult.stale` and `SeriesResult.missing_dates`, both additive and
  defaulted, so every existing call site keeps working. `OfflineClient` now
  reports when it served stale data and which window boundaries had nothing
  stored. Both are emitted by `to_dict()`.
- `OfflineClient(strict=True)` — refuse a partial window instead of serving it.
- `Series.CLP_EUR` (`F072.CLP.EUR.N.O.D`, CLP per EUR) and `Series.TPM_11M`
  (`F089.TPM.TAS.14.M`, expected TPM 11 months ahead): 30 indexed series, up
  from 28. Both codes were live-verified and their labels taken verbatim from
  BCCh's `descripEsp`.
- `econchile/py.typed` (PEP 561), declared in `[tool.setuptools.package-data]`
  and shipped in both the wheel and the sdist.

### Changed

- **Offline fallback scope.** v0.2 could only fall back to a result for the
  *same* `desde`/`hasta` it had been fetched with, so a cron asking for a new
  window had nothing to fall back to. `OfflineClient` now serves any window
  the cache covers with fresh data, and when the API is down it serves stale
  data up to `max_stale_age` (7 days), flagging it with `stale=True` and
  listing uncovered boundaries in `missing_dates`.
- `BcchClient` cache reads are window-based: a hit requires fresh coverage of
  every day requested, so the normal read path never serves a partial window.
  Fetching 2020 and 2024 separately and then asking for 2020-2024 is a
  deliberate miss, not a silent three-year hole.
- `Cache.size()` counts stored observations instead of query entries, so
  `clear_cache()` still counts data points.
- `Cache.get/set/get_series/set_series/make_key` are deprecated: they still
  work (they warn and delegate to the new store) but nothing in the library
  calls them any more. They will be removed in a future release.

### Removed

- `converters.count_valid` and `parsers.SeriesNotFoundError` — provably unused.
  `converters.safe_date`/`normalize_series` and the legacy `data/` files were
  **kept**: `examples/demo.py`, the documented one-command offline demo,
  depends on them.

### Fixed

- Five cache freshness tests were timing-dependent: with `ttl_seconds=0` they
  assumed the read landed in a later clock tick than the write, but a Windows
  clock ticks in ~15 ms steps, so a row written in the same tick ties at the
  boundary and counts as fresh. They now age rows explicitly. This could not
  have been caught by CI, which runs on Linux.

## [0.2.2] - 2026-09-24

Packaging and CI hardening. No library behaviour changes.
See `specs/v022_packaging_ci_spec.md`.

### Fixed

- `pytest` now passes inside the published sdist: tests that need files
  not shipped there (`.github/`, `examples/`, legacy `data/` backups)
  skip themselves, and `data/indexed_series.json` is now included.
  (0.2.0 and 0.2.1 sdists had 8+ failing tests.)
- Build requirement raised to `setuptools>=77` — `license = "MIT"`
  (PEP 639) is rejected by older setuptools.
- Publish job now fails loudly when the tag does not match the
  `pyproject.toml` version, instead of silently uploading nothing.
- Walkthrough notebook re-rendered (ships with outputs) and no longer
  prints "ALL 7 SERIES".

### Changed

- CI tests Python 3.10 – 3.14 (added 3.13 and 3.14, `fail-fast: false`);
  classifiers match.
- CI builds the package and runs `twine check --strict` on every PR.
- CI actions moved off Node 20 (`actions/checkout@v5`,
  `actions/setup-python@v6`); workflow default permissions are read-only.
- `specs/README.md` explains baseline vs delta specs and lists known drift.

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
