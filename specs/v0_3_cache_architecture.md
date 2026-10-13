# v0.3 — Cache Architecture: Observation-level cache & transactional migration

**Status:** approved; amended during implementation (see §12).
**Supersedes:** `cache_spec.md` (baseline) and the stale/offline semantics in
`offline_spec.md` and `cache_bugfix_spec.md`. Where they disagree, THIS spec
wins.

---

## 1. What this changes and why

Today the cache stores **whole-query results**: `cache(key, payload,
fetched_at, expires_at)` with `key = {series}|{desde}|{hasta}`. That has two
failures:

1. A daily cron asking `USD, 2020-01-01 → hoy` gets a NEW `hasta` every day,
   so the key changes every day and never hits.
2. On an API outage, `OfflineClient` looks up that exact key, misses, and dies
   with `BcchOfflineError` even though 5 years of USD points are stored.

**v0.3 stores observations per `(series, date)`.** Any requested window is
assembled from stored points. When the API fails, offline serves what exists
(marked stale), instead of dying.

This is the edge over `bcch-sdk`. It is the largest, riskiest change in v0.3.

---

## 2. New SQLite schema (version 1)

Four tables replace the single `cache` table (`fetched_windows` was added
during implementation, see §12):

```sql
CREATE TABLE observations (
    series      TEXT NOT NULL,     -- BCCh series code (enum .value or raw code)
    date        TEXT NOT NULL,     -- YYYY-MM-DD
    value       REAL,              -- NULL = API said "ND"; row present = "queried"
    fetched_at  TEXT NOT NULL,     -- ISO-8601 UTC (aware, +00:00) when last fetched
    PRIMARY KEY (series, date)
);

CREATE TABLE series_meta (
    series      TEXT PRIMARY KEY,
    metadata    TEXT NOT NULL,     -- JSON: {"series_id","descripEsp","descripIng","series_infos"}
    updated_at  TEXT NOT NULL      -- ISO-8601 UTC (aware)
);

CREATE TABLE fetched_windows (
    series      TEXT NOT NULL,
    desde       TEXT NOT NULL,     -- YYYY-MM-DD, the window that was REQUESTED
    hasta       TEXT NOT NULL,     -- YYYY-MM-DD
    fetched_at  TEXT NOT NULL,     -- ISO-8601 UTC (aware)
    PRIMARY KEY (series, desde, hasta)
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Seeded with: ('schema_version', '1') and ('last_purge_at', <now>)
```

- **`fetched_windows` records which windows were asked of the API.** It is
  the window-level twin of an ND row: "this range was queried". Observations
  alone cannot tell "BCCh has nothing after 2024-12-01" from "never asked":
  monthly/quarterly/annual series are dated on the first day of the period
  (IPC for Dec 2024 is `2024-12-01`), so a window ending `2024-12-31` has no
  observation on or after `hasta` even when it is complete. The same happens
  whenever `hasta` is later than the last published value.

- `PRIMARY KEY (series, date)` gives a clustered index on the exact access
  pattern (`WHERE series = ? AND date BETWEEN ? AND ?`). No extra index.
- **`series_meta` exists to preserve the public `metadata` field on cache
  hits.** Without it, a cache hit would return `metadata={}` and silently
  regress `result.metadata["descripEsp"]` for cached reads. It is updated on
  every API fetch and populated from legacy payloads during migration (§3.3).
- **`meta` holds singleton state** (`schema_version`, `last_purge_at`) and is
  the migration gate (§3.1). It is a table, not `PRAGMA user_version`, so it
  is greppable, testable, and extensible.

### `fetched_at` timezone rule (mandatory)

- New writes ALWAYS store `datetime.now(timezone.utc).isoformat(timespec="microseconds")`
  (aware, ends `+00:00`, fixed width so string order == time order).
- Freshness/staleness are computed as `now_utc - fetched_at_utc`, both aware.
- Legacy naive strings (from the old `datetime.now()`) are normalized to
  aware-UTC **during migration** via
  `datetime.fromisoformat(s).astimezone()` → `.astimezone(timezone.utc)`.
  A migrated row must never leave a naive datetime in the column.

---

## 3. Transactional migration

### 3.1 State machine

On every `Cache.__init__`, after opening the DB, run `_migrate()`, which
decides by inspection (read-only checks first):

| Observed state | Action |
|---|---|
| No `meta` table, no `cache` table, no `observations` | Fresh DB: create v1 schema (§2), seed `schema_version=1`, `last_purge_at=now`. |
| `meta.schema_version == 1` | Already migrated: `CREATE TABLE IF NOT EXISTS` all four tables (idempotent), done. |
| `meta.schema_version` is anything else | Written by a newer econchile: raise `BcchCacheError`, touch nothing. |
| No `meta`, but `cache` table exists | **Legacy v0.2 → migrate** (§3.2). |
| No `meta`, but `observations` exists (no `cache`) | Treat as v1: create missing tables, seed `schema_version=1`. |

The inspection runs INSIDE a `BEGIN IMMEDIATE` transaction, so two
processes opening the same legacy DB cannot both migrate it.

### 3.2 Migration procedure (v0.2 → v1)

Run inside **one SQLite transaction**. SQLite DDL (CREATE/DROP) is
transactional, so a rollback restores the old `cache` table exactly.

1. `BEGIN` (immediate).
2. `CREATE TABLE observations`, `series_meta`, `fetched_windows`, `meta` (as §2).
3. Read legacy rows **ordered by `fetched_at ASC`** (`SELECT key, payload, fetched_at FROM cache ORDER BY fetched_at ASC`).
4. For each row:
   - JSON-decode `payload` and normalize `fetched_at`. If either fails, or
     the payload lacks `series`/`observations` → **skip the row**, count it,
     and `warnings.warn(...)` once at the end (§3.4). Never hard-fail the
     whole migration on one bad row.
   - Extract `series` (payload `series` string), `observations` (list of
     `{"date","value"}`), `metadata` (dict).
   - For each observation: `INSERT OR REPLACE INTO observations
     (series, date, value, fetched_at) VALUES (?, ?, ?, ?)` using the
     **legacy `cache.fetched_at` column value** (normalized to aware-UTC per
     §2) as the observation `fetched_at`.
   - `INSERT OR REPLACE INTO series_meta (series, metadata, updated_at)` with
     the payload `metadata` (JSON dump) and the same normalized fetched_at.
   - `INSERT OR REPLACE INTO fetched_windows` with the `desde`/`hasta` parsed
     from the legacy key (`{code}|{desde}|{hasta}`) and the same normalized
     fetched_at. A key that does not parse just records no window.
   - Ordering by `fetched_at ASC` + `OR REPLACE` means overlapping windows
     resolve to the **newest** fetch (last write wins on the PK).
5. **Verify** (§3.3): count rows in `observations` and compare to the number
   of DISTINCT `(series, date)` pairs across all successfully-decoded
   payloads. On mismatch, raise → rollback.
6. `INSERT INTO meta (key,value) VALUES ('schema_version','1')` and
   `('last_purge_at', <now>)`.
7. `DROP TABLE cache`.
8. `COMMIT`.

On ANY exception in steps 1–8, the `except` block issues `ROLLBACK` and
re-raises as `BcchCacheError` with the message
`"cache migration failed; original cache left intact: {exc}"`. The old
`cache` table is preserved because either (a) we failed before `DROP`, or
(b) the rollback undid the DROP.

### 3.3 Verification (before DROP)

`expected = len({(payload["series"], obs["date"]) for every successfully
decoded row and every obs in it})` must equal
`SELECT COUNT(*) FROM observations`. Mismatch aborts and rolls back. This is
the guard against silent data loss. (It counts DISTINCT pairs: overlapping
legacy windows legitimately collapse onto one row per date, so a plain sum
of observation lists would fail every migration that has overlaps.)

The count is read through `Cache._migrated_observation_count(conn)` so the
rollback test can force a mismatch.

### 3.4 Bad legacy rows

A payload that fails to JSON-decode is skipped and warned; it is NOT a
migration failure (one corrupt entry must not brick a user's cache). The
verification count (§3.3) therefore counts only **successfully decoded**
payloads. The skipped count is reported in the warning.

### 3.5 What migration preserves, by construction

- ND values: legacy `value=None` → `NULL` in `observations` with the row
  present → "queried, no data" survives.
- Metadata: legacy payload `metadata` → `series_meta`.
- Overlapping windows: newest `fetched_at` wins per `(series, date)`.
- Query windows: the legacy key → `fetched_windows`, so a query that hit the
  v0.2 cache keeps hitting after the upgrade (until its TTL runs out).

---

## 4. Freshness, staleness, and eviction

Three age thresholds (constructor params, all with defaults):

| Param | Default | Meaning |
|---|---|---|
| `ttl_seconds` | `86400` (24h) | Age ≤ TTL ⇒ **fresh**. |
| `max_stale_age` | `604800` (7 days) | TTL < age ≤ max_stale_age ⇒ **stale but servable**. |
| `max_purge_age` | `157784630` (5 years) | age > max_purge_age ⇒ deleted by `purge()`. |

For an observation (or fetched window) with `fetched_at = F` and
`now = datetime.now(timezone.utc)`:

- **fresh** if `now − F ≤ ttl_seconds`
- **stale** if `ttl_seconds < now − F ≤ max_stale_age`
- **expired** if `now − F > max_stale_age` (never served; kept on disk until
  it is older than `max_purge_age`, then purged)

### Serving rules (who may serve what)

| Path | Fresh | Stale | Expired |
|---|---|---|---|
| `BcchClient.get` (cache-first, normal read) | ✅ | ❌ (→ API) | ❌ (→ API) |
| `OfflineClient.get` fallback (API failed) | ✅ | ✅ (`stale=True`) | ❌ (→ error if nothing else) |

**Stale data is served ONLY in the offline fallback path. Normal cache reads
never serve stale or partial data** — they fall through to the API.

---

## 5. Cache resolution logic (public behaviour)

### 5.1 `BcchClient.get` (cache-first) — fresh only

1. Resolve series, validate dates (unchanged), get code.
2. If `use_cache`:
   - A **cache hit** requires the union of **fresh** `fetched_windows` for
     the series to cover every day of `[desde, hasta]` (windows that touch
     or overlap merge; adjacent days count as contiguous, e.g. a window
     ending `2022-12-31` and one starting `2023-01-01`).
   - Hit ⇒ reconstruct the result from **fresh** observations in
     `[desde, hasta]`, with `stale=False`, `missing_dates=[]`, and return.
   - Miss (any day of the window not covered by a fresh window) ⇒ proceed
     to API fetch.
3. API fetch (unchanged) ⇒ store observations AND the requested window (§6)
   ⇒ return fresh result (`stale=False`, `missing_dates=[]`).

Because the hit test is window-based, the normal read path never serves a
partial window: fetching 2020 and 2024 separately and then asking for
2020→2024 is a miss (2021–2023 were never asked), not a silent 3-year hole.
The original draft (one fresh observation on/before `desde` and one on/after
`hasta`) would have served that hole, and would never hit for monthly or
quarterly windows ending on a month end (see §2, §12).

### 5.2 `OfflineClient.get` (API-first) — fresh, else stale/partial

1. Resolve, validate, code.
2. Try `fetcher.fetch`:
   - **Success** ⇒ store observations + window (§6) ⇒ return fresh result
     (`stale=False`, `missing_dates=[]`).
   - **Failure** (`BcchApiError`, `requests.RequestException`, `OSError`) ⇒
     fallback, step 3.
3. Read usable observations: fresh + stale (exclude expired), in
   `[desde, hasta]`.
   - **No usable observations AND no usable window covers the range** ⇒
     `BcchOfflineError` (unchanged shape, `cache_had_data=False`).
   - **Some** ⇒ return with:
     - an empty observation list is a valid answer when a usable window
       covers every day of `[desde, hasta]` (e.g. a weekend for a daily
       series: the window was fetched and BCCh published nothing) — see
       §12 amendment 9.
     - `stale = any(served observation is stale)`
     - `missing_dates` per §5.3
     - `source = "api"` (data ultimately originates from the API — see §7)
4. `strict=True` (constructor flag, default `False`): if the served window is
   partial (usable windows do not cover every day of `[desde, hasta]`), raise
   `BcchOfflineError` (`cache_had_data=True`, plus `missing_dates` in the
   context) instead of returning partial data.

### 5.3 `missing_dates` definition

`missing_dates: list[str]` (sorted, ISO dates) reports **boundary coverage**
of the requested window against usable data. A bound is covered when a
usable fetched window contains it, or a usable observation lies on/beyond it:

- Include the `desde` date if no usable window contains `desde` and there is
  no usable observation with `date ≤ desde` (leading gap).
- Include the `hasta` date if no usable window contains `hasta` and there is
  no usable observation with `date ≥ hasta` (trailing gap).

So it is `[]`, `[desde]`, `[hasta]`, or `[desde, hasta]` — the actual ISO
date strings, e.g. `["2024-01-01"]`. Interior gaps are not enumerated
(that would need a frequency calendar); `strict=True` is the way to refuse
any partial window. It never contains dates whose value is NULL (those are
ND, not missing — §8).

---

## 6. New internal cache API

`Cache` is internal (not exported, not in `__all__`, undocumented). The
key-based methods are **deprecated, not deleted** (no dual-API maintenance;
they disappear in a future release).

New constructor:

```python
Cache(db_path=None, ttl_seconds=86400, max_stale_age=604800, max_purge_age=157784630)
```

| Method | Signature | Behaviour |
|---|---|---|
| `get_observations(series, desde, hasta, include_stale=False) -> list[Observation]` | code + window | Return rows in window, ordered by date. `include_stale=False` ⇒ fresh only; `True` ⇒ fresh + stale (never expired). |
| `put_observations(result, desde=None, hasta=None) -> None` | — | One transaction: upsert each observation `(series, date) → (value, fetched_at=now_utc)`; upsert `series_meta` from `result.metadata`; when `desde`/`hasta` are given, upsert the requested window into `fetched_windows`. |
| `covers(series, desde, hasta, include_stale=False) -> bool` | — | True when the union of usable (fresh, or fresh+stale) fetched windows covers every day of `[desde, hasta]` (§5.1). |
| `load(series, desde, hasta, include_stale=False) -> SeriesResult` | — | Reconstruct a result from usable observations in the window: `series` back to the enum member when indexed (raw `str` otherwise), `metadata` from `series_meta` (`{}` if none), `fetched_at` = newest served observation (newest overlapping window if there are none), `stale` and `missing_dates` per §5.2/§5.3. Observations may be empty. |
| `purge() -> int` | — | Delete `observations` and `fetched_windows` with `fetched_at < now_utc − max_purge_age`; delete orphaned `series_meta` (series with 0 observations); update `meta.last_purge_at`; return observation rows removed. Runs once per `__init__`. |
| `clear() -> int` | — | Delete all `observations`, `series_meta` and `fetched_windows`; return the number of **observation** rows removed (so `clear() == size()` beforehand, and `BcchClient.clear_cache()` keeps counting data points). |
| `size() -> int` | — | `COUNT(*)` of `observations` (semantic change: was query-entries, now observation rows). |

**Deprecated (emit `DeprecationWarning` on call), still functional:**
`get(key)`/`get_series(series, desde, hasta)` return `load(...)` when fresh
windows cover the key's window, else `None`; `set(key, result)`/
`set_series(...)` call `put_observations(result, desde, hasta)`;
`make_key(...)` still builds the old key string. They keep working so the
deprecation is non-breaking and Phase 1 can land with the clients untouched.
`client.py`/`offline.py` stop calling all of them in Phase 2; the shims are
removed in a later release.

---

## 7. `SeriesResult` additions (additive, defaulted)

```python
@dataclass(frozen=True)
class SeriesResult:
    series: Any
    observations: list
    fetched_at: datetime
    source: str = "api"
    metadata: dict[str, Any] = field(default_factory=dict)
    stale: bool = False
    missing_dates: list[str] = field(default_factory=list)
```

- Defaults mean every existing constructor call site and test keeps working.
- `source` **stays `"api"` everywhere** (unchanged semantics): cached data was
  originally fetched from the API, and today the field already reports `"api"`
  on cache hits. No caller detecting "fresh" via `source == "api"` breaks.
- `fetched_at` on a reconstructed (cache-served) result = the **maximum
  `fetched_at`** among the observations served (best freshness signal); on a
  live fetch = `now_utc` (as today).

---

## 8. ND vs missing (the crux distinction)

| Case | Storage | Served as | In `missing_dates`? |
|---|---|---|---|
| API returned `statusCode == "ND"` | row present, `value = NULL` | `Observation(date, value=None)` | **No** |
| Date never fetched (or expired) | no row | absent from result | **Yes** (if at a boundary) |

A NULL row **is** coverage: "this date was queried and BCCh has no value."
Deleting the row would erase that knowledge and reclassify it as "never
queried." Therefore **purge and clear must never be the only writers** — the
NULL row is real state.

---

## 9. Error handling

- SQLite I/O failure on any new method → `BcchCacheError` (unchanged type).
- Migration failure → `BcchCacheError` with the "original cache left intact"
  message (§3.2); the old `cache` table survives.
- `BcchOfflineError` unchanged in type and shape; only the offline fallback's
  served-window semantics change (§5.2).

## 10. Dependencies

- `sqlite3`, `json`, `datetime` (stdlib) — **no new dependency**.
- `warnings` (stdlib) for the deprecation and migration-skip warnings.

## 11. Test constraints

- Never touch `~/.econchile/` — always explicit `db_path` (`tmp_path`).
- Never `sleep()` — exercise age with `ttl_seconds=0` and an explicit old
  `fetched_at` string.
- Migration tests build a **legacy `cache` table by hand** (CREATE + INSERT a
  v0.2-shaped payload JSON) inside `tmp_path`, then open `Cache` on it and
  assert the migrated `observations`/`series_meta`/`meta` contents.
- Rollback test: inject a failure mid-migration (e.g. a payload that fails the
  verification count via a monkeypatched observation count) and assert the
  legacy `cache` table is unchanged afterward.

---

## 12. Amendments made during implementation

The draft above was approved with "adapt it where the spec contradicts
itself". These are the deliberate deviations from the first draft, each
with the reason. The sections above already reflect them.

| # | Draft said | Implemented | Why |
|---|---|---|---|
| 1 | Three tables | Four: + `fetched_windows` (§2) | Without it a monthly/quarterly/annual window ending on a month end (IPC 2020-01-01 → 2024-12-31) never hits the cache (observations are dated on the 1st of the period), a v0.2-repeatable query regresses to an API call every time, and `strict=True`/`missing_dates` flag complete data as partial. |
| 2 | Hit = fresh observation ≤ `desde` and ≥ `hasta` | Hit = fresh windows cover every day of the window (§5.1) | The bound rule served silent interior holes in a common flow (fetch 2020, fetch 2024, ask 2020→2024 ⇒ 3 years missing), contradicting "normal cache reads never serve partial data". Windows make the check exact without a frequency calendar. |
| 3 | Verification = plain sum of observation lists | DISTINCT `(series, date)` count (§3.3) | The plain sum fails every migration with overlapping legacy windows, which the draft itself requires to succeed (`test_overlapping_windows_newest_wins`). |
| 4 | Deprecated shims return `None` / no-op | Shims warn AND delegate to the new store (§6) | The execution plan requires Phase 1 to land with the clients unchanged and the suite green; no-op shims would break every cache hit. A deprecation should not change behaviour anyway. |
| 5 | `clear()` returns observations + `series_meta` rows | Returns observation rows only (§6) | Keeps `clear() == size()` and keeps `clear_cache()` counting data points, not internal bookkeeping rows. |
| 6 | `missing_dates == ["desde"]` (loose wording) | The ISO date itself, e.g. `["2024-01-01"]` (§5.3) | The field is typed "sorted, ISO dates". |
| 7 | Migration skips only JSON-decode failures | Skips any row that cannot be decoded into series + observations + a parseable `fetched_at` (§3.2) | A row with valid JSON but no `observations` key would otherwise crash the whole migration. |
| 8 | Unknown `schema_version` unspecified | Raise `BcchCacheError`, touch nothing (§3.1) | A cache written by a newer econchile must not be "re-migrated" by an older one. |
| 9 | Fallback: "**None** ⇒ `BcchOfflineError`" keyed on observations alone | Error only when no usable observations AND no usable covering window; a covered empty window is served empty (§5.2) | A weekend/holiday window is a real, fetched answer with no published values. Erroring on it would send a cron job down the "no data" path for a range that was in fact answered, and `strict=True` would refuse it too. |
| 10 | `to_dict()` unspecified | `to_dict()` also emits `stale` and `missing_dates` (§7) | The flags are the point of the offline path; dropping them on export loses the partial/stale signal for anyone serialising results. |
