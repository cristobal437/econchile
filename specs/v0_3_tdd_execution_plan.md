# v0.3 — TDD Execution Blueprint

**Status:** for approval. No implementation code yet; this is the order of
attack once you green-light the specs.
**Specs this plan executes:** `v0_3_cache_architecture.md`,
`v0_3_minor_updates.md`.
**Iron rule (repo convention):** write the failing test FIRST, watch it fail
for the right reason, then implement the minimum. All 341 tests stay green at
every merge point.

Run everything with an empty `PYTHONPATH` and empty token (repo convention):

```bash
PYTHONPATH= BCCH_TOKEN= python -m pytest tests/ -q
```

---

## Global constraints (apply to every phase)

- **Public API frozen:** `get()`, `search()`, `list_series()`, `clear_cache()`
  signatures do not change. `Series` enum only GAINS members (28 → 30), never
  renames or drops. The only surface additions are the additive, defaulted
  `SeriesResult.stale` and `SeriesResult.missing_dates`.
- **No new dependency** — `requests` remains the only runtime dep; new code
  uses `sqlite3`/`json`/`datetime`/`warnings` (stdlib).
- **Never touch** `sample_response.json`, `.env`, `econchile/study/`.
- **Labels from BCCh wording** — the two new series' `descripEsp` must be
  captured live before Phase 3 (catalog audit rule).
- Every phase ends in a commit; phases are mergeable independently (main is
  green after each).

---

## Phase 1 — Cache architecture & migration

**Goal:** replace the whole-query `cache` table with the observation store +
`series_meta` + `meta`, migrate existing `cache.db` transactionally, and add
the new observation-level read/write API. **No user-visible behavior change
yet** — the deprecated key-based methods reproduce equivalent behavior so the
clients keep working unchanged.

**Test files written FIRST (in order):**

1. `tests/test_cache_migration.py` (NEW) — the riskiest contract. Cases:
   - `test_fresh_db_gets_v1_schema`: open a new DB → `observations`,
     `series_meta`, `meta` exist; `meta.schema_version == "1"`.
   - `test_legacy_v02_db_migrates`: build a v0.2 `cache` table by hand (one
     key, a real payload JSON with 2 observations + metadata), open `Cache`
     → observations rows present, `cache` table dropped, version 1.
   - `test_nd_value_preserved_as_null_row`: legacy payload observation with
     `value=None` → migrated row has `value IS NULL` AND the row exists.
   - `test_metadata_migrated_to_series_meta`: legacy payload metadata →
     `series_meta.metadata` JSON round-trips.
   - `test_overlapping_windows_newest_wins`: two legacy rows, same series,
     overlapping date, different `fetched_at` → the newer value wins.
   - `test_verification_count_mismatch_rolls_back`: monkeypatch the count so
     verification fails → `BcchCacheError`, and the legacy `cache` table is
     still present and byte-identical.
   - `test_bad_payload_skipped_with_warning`: one undecodable payload →
     migration succeeds, `warnings.warn` emitted, other rows intact.
   - `test_fetched_at_normalized_to_utc`: legacy naive `fetched_at` → migrated
     value is aware UTC.
   - `test_already_migrated_idempotent`: a v1 DB reopens without error or
     data loss.
2. `tests/test_cache.py` (REWRITE) — drop the key-based tests, add:
   - `put_observations`/`get_observations` round-trip (fresh).
   - `get_observations(include_stale=False)` excludes stale rows;
     `include_stale=True` includes fresh + stale but never expired.
   - window filtering: `get_observations` respects `desde`/`hasta`.
   - ND row (`value=None`) is returned as `Observation(value=None)`, distinct
     from a never-fetched date (absent).
   - `purge()` deletes only expired rows and orphaned `series_meta`, updates
     `meta.last_purge_at`, returns the count.
   - deprecated `get`/`set`/`get_series`/`set_series`/`make_key` emit
     `DeprecationWarning`.

**Production files touched:** `econchile/cache.py` (schema, `_migrate()`,
new API, deprecated shims). `econchile/types.py` is **NOT** touched in this
phase (fields added in Phase 2).

**Mechanical test edits that must land in Phase 1** (amendment): the clients
are untouched, but the storage changed, so old tests that assumed the old
storage details are updated (intent kept):
- `tests/test_client.py` / `tests/test_offline.py`: exact `result ==
  sample_result` comparisons of a cache-served result → compare `series`,
  `observations`, `source`, `metadata` (a cache-served `fetched_at` is now
  the time the observations were stored, per §7 of the cache spec, not the
  fixture's fake timestamp).
- `tests/test_offline.py::_insert_expired_row` wrote SQL into the dropped
  `cache` table → writes an expired observation + window instead; the
  "expired row is swept on read" assertion becomes "expired data is not
  served" (sweeping now happens in `purge()`).

**RED → GREEN:** write both test files → run → they fail (import/schema
errors) → implement `cache.py` → run until green → full suite green → commit
`feat(cache): observation-level store + transactional v0.2 migration`.

**Merge check:** `PYTHONPATH= BCCH_TOKEN= python -m pytest tests/ -q` passes;
`tests/test_cache_migration.py` + rewritten `tests/test_cache.py` are the new
contract.

---

## Phase 2 — Client integration (stale / partial serving)

**Goal:** wire `BcchClient` and `OfflineClient` to the observation store with
the resolution rules in §5 of the cache spec. This is where `stale` and
`missing_dates` become real.

**Test files written FIRST:**

1. `tests/test_types.py` (EXTEND) — `SeriesResult` defaults: constructing
   with only the original 5 args yields `stale=False`, `missing_dates=[]`.
2. `tests/test_client.py` (EXTEND) — cache-first fresh-bounds semantics:
   - full fresh coverage ⇒ served from cache, `stale=False`, `missing_dates=[]`,
     no network call.
   - missing/expired bound ⇒ API called and result stored.
   - partial coverage ⇒ falls through to API (never serves partial).
   - `use_cache=False` bypasses read AND write.
3. `tests/test_offline.py` (EXTEND, plus re-point old internals) — the
   resilience behaviour:
   - API fails, fresh observations cover the window ⇒ served, `stale=False`.
   - API fails, observations exist but older than TTL (≤ 7d) ⇒ served with
     `stale=True`.
   - API fails, leading gap ⇒ served with `missing_dates == ["desde"]`.
   - API fails, trailing gap ⇒ `missing_dates == ["hasta"]`.
   - API fails, both gaps ⇒ `missing_dates == ["desde","hasta"]`.
   - API fails, no usable observations ⇒ `BcchOfflineError`,
     `cache_had_data=False`.
   - `strict=True` + partial ⇒ `BcchOfflineError` instead of partial data.
   - ND (NULL) rows are served, NOT listed in `missing_dates`.

**Mechanical edits required** (old tests that reached into the key-based
cache): update `tests/test_offline.py` helpers that used
`_cache.set_series`/`get_series`/`_serialize`/`make_key` and the
`_insert_expired_row` SQL, and `tests/test_client.py` fixtures that used
`make_key`/`_cache.get`/`_cache.set`. These edits are the only reason Phase 1
shims existed.

**Production files touched:**
- `econchile/types.py` — add `stale: bool = False`,
  `missing_dates: list[str] = field(default_factory=list)`.
- `econchile/client.py` — fresh-bounds hit check; `put_observations` on store.
- `econchile/offline.py` — stale/partial fallback; `strict` ctor flag
  (default `False`); set `stale`/`missing_dates`.
- `econchile/cache.py` — remove the deprecated shims' use by the clients
  (the shims themselves stay until a later release, but nothing calls them).

**RED → GREEN → commit** `feat(clients): stale/partial serving with
SeriesResult flags`.

**Merge check:** full suite green; `BcchClient`/`OfflineClient` public
signatures unchanged; a manual scripted check confirms a simulated cron
(API down → still serves stale USD) behaves per spec.

---

## Phase 3 — Two new series

**Goal:** add `CLP_EUR` and `TPM_11M` to the catalog (28 → 30).

**Pre-step (no tests):** capture the live `descripEsp`/`descripIng` for
`F072.CLP.EUR.N.O.D` and `F089.TPM.TAS.14.M` (small window, `use_cache=False`).
Pin the wording; do not invent labels.

**Test files written FIRST:**
1. `tests/test_series_map.py` (EXTEND) — `Series.CLP_EUR.value ==
   "F072.CLP.EUR.N.O.D"`, `Series.TPM_11M.value == "F089.TPM.TAS.14.M"`, both
   present in `Series.list_all()`; `len(list_all()) == 30`.
2. `tests/test_catalog_labels.py` (EXTEND) — pin the live `descripEsp` wording
   for both; assert `CLP_EUR` is "CLP per EUR" (not "EUR per USD"), and
   `TPM_11M` is "11 months ahead" (not current-month).

**Production files touched:**
- `econchile/series_map.py` — two enum members + `_META_MAP` entries.
- `data/indexed_series.json` — regenerate (never hand-edit).
- `README.md` — "28" → "30".

**RED → GREEN → commit** `feat(catalog): CLP_EUR + TPM_11M (30 series)`.

**Merge check:** full suite green; `test_catalog_labels.py` parity holds
(JSON ↔ enum ↔ labels).

---

## Phase 4 — Cleanups

**Goal:** remove dead code and legacy files, add `py.typed`.

**Order (deletion only after green on removal):**
1. `econchile/converters.py` — delete `safe_date`, `normalize_series`,
   `count_valid`.
2. `econchile/parsers.py` — delete `SeriesNotFoundError`.
3. Legacy data — `git rm data/chile-macro.json data/macro-chile-lean.csv`
   and `git rm -r data/archived`.
4. `examples/charts_demo.py` — repoint off the deleted CSV (or delete the
   example).
5. `py.typed` — add empty `econchile/py.typed`; `pyproject.toml`
   package-data; `MANIFEST.in` include.

**Test files written FIRST (or edited, in order):**
1. `tests/test_converters.py` (TRIM) — remove `TestSafeDate`,
   `TestRealDataIntegration`, and the `safe_date`/`normalize_series`/
   `count_valid` imports; keep `TestSafeFloat` + `TestNullSentinels`.
2. `tests/test_parsers.py` (EDIT) — drop the `SeriesNotFoundError` import.
3. `tests/test_release.py` (EXTEND) — assert `econchile/py.typed` exists and
   is declared in `pyproject.toml` `[tool.setuptools.package-data]`; assert
   README no longer claims "28" series.

**RED → GREEN:** run the suite after each deletion; deletions only land when
the trimmed tests pass. Final full-suite run → commit
`chore: remove dead converters, legacy data, add py.typed`.

**Merge check:** full suite green; `python -m build` then install the wheel in
a clean venv and confirm `site-packages/econchile/py.typed` is present.

---

## Out of scope (explicitly NOT in these phases)

- Weekly live-series health-check workflow (needs `BCCH_TOKEN` GitHub secret
  + approval).
- Versioning `series.xlsx` in the repo.
- `strict=True` on `BcchClient` (cache-first is always fresh-only).
- Interior-gap detection (frequency calendar) — documented limitation.

## Release (after all 4 phases merge)

Same flow as always: update `specs/README.md` drift table (new delta specs
listed), CHANGELOG for 0.3.0, re-render the walkthrough notebook, bump version
in `pyproject.toml` + `__init__.py`, then PR → Cristóbal merges → tag `v0.3.0`
→ CI publishes → clean-install smoke.
