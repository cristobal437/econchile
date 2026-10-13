# AGENTS.md

## What this is

Small Python 3.10+ OSS library wrapping the Banco Central de Chile (BCCh) SIE REST API: fetch official macro series (UF, USD, TPM, IPC, IMACEC, PIB — 30 indexed, any other BCCh code via raw string), parse into typed data, cache in SQLite. Published on PyPI as `econchile` (v0.3.0, MIT).

## Commands

```bash
pip install -e ".[test]"     # editable install + pytest
python -m pytest tests/ -q   # 447 tests, must stay green
python examples/demo.py      # offline demo, runs without BCCH_TOKEN
```

`BCCH_TOKEN` comes from the environment only — the library does not load `.env` itself. `.env` is gitignored and holds a real token locally; never let it into a commit or diff.

## Conventions

- `specs/*.md` are the source of truth: one spec per module (v0.1 baseline) plus one delta spec per release (`v02_*`, `v021_*`, `v022_*`, `v0_3_*`; index in `specs/README.md`). When they disagree, the newest delta spec wins. Read the spec before touching a module.
- `tests/` are the contract. All 447 tests must stay green.
- Series labels must match BCCh's own wording (API `descripEsp` / official `series.xlsx`), never be guessed from the code string. `data/indexed_series.json` is generated from the enum; `tests/test_catalog_labels.py` enforces parity.
- **NEVER create or commit anything under `econchile/study/`** — private, gitignored annotated learning notes. Do not add `*_annotated.py` versions of new files.
- **NEVER commit `.env`, `.env.local`, or any secret.**
- `sample_response.json` (8MB real API fixture, UTF-16) stays tracked as-is — `tests/test_parsers.py` needs it. It is excluded from the sdist via MANIFEST.in. Do not trim, remove, or "fix" it.
- PyPI releases: push tag `v*` → CI runs tests (Python 3.10/3.11/3.12) then publishes via trusted publisher. No manual publish.

## Architecture

Resolution chain: API → SQLite cache → raise. Two clients: `BcchClient` is
cache-first (serve what fresh windows cover, hit API on a miss); `OfflineClient`
is API-first (try API, fall back to cached data on failure). The cache is
observation-level (schema v1: `meta`, `observations`, `series_meta`,
`fetched_windows`) and upgrades a v0.2 cache in one transaction on first open.
Cache lives at `~/.econchile/cache.db`: `ttl_seconds` 24h (fresh),
`max_stale_age` 7d (servable stale, offline path only), `max_purge_age` 5y.

## Gotchas

- Public API takes dates as `YYYY-MM-DD`; BCCh sends `DD-MM-YYYY` internally — converters handle the conversion, don't mix formats.
- BCCh marks missing data with `statusCode == "ND"` → parsed as `value=None`, never zero or an exception.
- BCCh response encoding is unstable: UTF-16 with BOM, UTF-8, or latin-1 (ISO-8859-1) with raw accented bytes. Decode order: UTF-16 BOM → UTF-8 → latin-1 (latin-1 never fails). Applies to both `fetcher._decode` and `parsers.parse_response`.
- The fetcher retries transient failures — network errors, HTTP 5xx, HTTP 429, and non-JSON/HTML bodies — up to `max_retries` (default 2) with exponential backoff (`retry_backoff * 2**attempt`). Other HTTP 4xx and `Codigo != 0` business errors are never retried.
- **Token is optional at construction** (both clients); it is validated at fetch time and a missing token raises `BcchApiError` before any network I/O — so `OfflineClient` serves cached data without a token (the fallback treats it as an API failure), and `BcchClient` cache hits work too.
- BCCh API tokens may contain `/` — the fetcher URL-encodes them automatically via `urlencode` (`/` → `%2F`). Never build API URLs by hand-formatting the raw token into the query string; always pass it through `urllib.parse.urlencode`/`quote`.

- A fetched window with **no published values** (a weekend for a daily series)
  is a real answer: it is served as an empty observation list, not as a cache
  miss and not as a gap. A row with `value=None` (BCCh "ND") is coverage too.
- Stale data is served **only** by the `OfflineClient` fallback. `BcchClient`
  reads are fresh-only and window-based: every day of the range must be
  covered, so a partial window is a deliberate miss, not a silent hole.
- Tests must not assume two `datetime.now()` calls differ. A Windows clock
  ticks in ~15 ms steps, so with `ttl_seconds=0` a row written and read in the
  same tick ties at the boundary and counts as fresh. Age rows explicitly
  (see `_set_age` in `tests/test_cache.py`). Linux CI will not catch this.
- Indexing a series breaks any test that used its code as the "not indexed"
  example. Raw-code tests use a code asserted to be outside the catalog.
- Series labels come from BCCh's live `descripEsp`, never guessed from the
  code string. Pin the live text in `tests/test_catalog_labels.py` first.

## Contribution flow

1. Read the relevant `specs/*.md` (write/update it if the feature isn't specced).
2. Write the test contract in `tests/` (tests fail first).
3. Implement in `econchile/`.
4. `python -m pytest tests/ -q` — all green.
5. Commit. No secrets, no `study/`, no fixture changes.
