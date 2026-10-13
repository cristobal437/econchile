# v0.3 — Minor updates: 2 new series, cleanup, py.typed

**Status:** draft for approval.
**Scope:** everything in v0.3 that is NOT the observation-level cache. Cheap,
low-risk work done after the cache architecture is settled (see the execution
plan for phase ordering).

---

## 1. Two new series (28 → 30)

Both codes were live-verified (return data, not `Codigo -50`). Names follow
the existing catalog convention: an enum member + a `_META_MAP` entry, labels
in BCCh's own wording.

| Enum member | BCCh code | Frequency | Representation | What it is |
|---|---|---|---|---|
| `CLP_EUR` | `F072.CLP.EUR.N.O.D` | `DAILY` | `LEVEL` | Euro exchange rate in Chilean pesos (CLP per EUR) |
| `TPM_11M` | `F089.TPM.TAS.14.M` | `MONTHLY` | `LEVEL` | Expected TPM 11 months ahead (EEE survey, median) |

### 1.1 Naming decisions (must be approved)

- **`CLP_EUR`, not `EURO`** — `EURO` already exists and is `F072.EUR.USD.N.O.D`
  (EUR **per USD**). `CLP_EUR` is the distinct series "CLP per EUR". Do not
  confuse the two.
- **`TPM_11M`, not `CLP_TPM_11M` and not `TPM_EXPECTED_11M`** — the `CLP_`
  prefix is wrong here (a TPM rate is not CLP-denominated). `TPM_11M` reads as
  "11 months ahead" and cannot collide with `TPM_EXPECTED`
  (`F089.TPM.TAS.11.M`, the **current-month** expectation). The `11M` suffix
  is what disambiguates it.

### 1.2 Labels (BCCh wording, mandatory)

Labels MUST be taken from the live API `descripEsp` / `descripIng` for these
exact codes (catalog audit rule, `econchile-bcch-api` skill §7b), never
invented from the code string. The live `descripEsp` text is pinned in the
test contract before the enum entry is added.

### 1.3 Files touched

- `econchile/series_map.py`: add the two enum members + `_META_MAP` entries.
- `data/indexed_series.json`: regenerate from the enum (never hand-edit).
- `tests/test_series_map.py`: mirror the two constants.
- `tests/test_catalog_labels.py`: pin the two new labels.
- `README.md`: bump "28 series" → "30 series".

---

## 2. Dead code and legacy file cleanup

Remove only what is provably unused. None of these are exported, documented,
or in `__all__`.

### 2.1 `econchile/converters.py`

Delete three functions; keep `safe_float` (still used by `parsers.py`) and
`NULL_SENTINELS` (used by `safe_float`):

- `safe_date` — unused.
- `normalize_series` — unused.
- `count_valid` — unused.

Verify with a repo-wide grep before deleting; if any call site exists, keep
it and remove it from this spec's scope instead.

### 2.2 `econchile/parsers.py`

Delete `SeriesNotFoundError` (subclass of `ParsingError`, never raised). Keep
`ParsingError`.

### 2.3 Legacy `data/` files

Delete (git `rm`, so they leave the repo, not just the working tree):

- `data/chile-macro.json` (96 KB)
- `data/macro-chile-lean.csv` (68 KB)
- `data/archived/` (332 KB, contains `total-macro-chile.csv`)

**Keep** `data/indexed_series.json` (generated from the enum; enforced by
`tests/test_catalog_labels.py` and needed in the sdist).

Confirm nothing imports or references these paths (grep `chile-macro`,
`macro-chile-lean`, `total-macro-chile`, `archived`) before deleting.

**Known dependents to handle first:**
- `examples/charts_demo.py` reads `data/macro-chile-lean.csv` (line 30). Either
  repoint it at the API/client (preferred) or delete the example. Do not leave
  a broken example referencing a deleted file.
- `tests/test_converters.py` — `TestRealDataIntegration` reads both legacy
  files; `TestSafeDate` tests the deleted `safe_date`; the density report uses
  `normalize_series`/`count_valid`. All of these sections are removed in the
  cleanup phase (see execution plan), leaving only `TestSafeFloat` and
  `TestNullSentinels`.
- `tests/test_parsers.py` imports `SeriesNotFoundError` (line 20) — drop it
  from the import list.

### 2.4 Stale local branches (one-time git task, outside feature scope)

9 merged local branches exist. Delete them in a single housekeeping step
(`git branch -d <name>` for each; they are merged, so `-d` succeeds). This is
a local-only cleanup — no commit, no push.

---

## 3. `py.typed` marker (PEP 561)

Declare the package ships inline type hints so type checkers (mypy, pyright,
IDE) resolve `import econchile` types without a stubs package.

### 3.1 Changes

1. Add an **empty** file `econchile/py.typed`.
2. `pyproject.toml` — add package-data so the marker ships in the wheel:
   ```toml
   [tool.setuptools.package-data]
   econchile = ["py.typed"]
   ```
3. `MANIFEST.in` — add `include econchile/py.typed` so it also ships in the
   sdist.

### 3.2 Verification

- `pip install` the built wheel into a clean venv and confirm
  `econchile/py.typed` is present in `site-packages/econchile/`.
- Add a packaging test to `tests/test_release.py` asserting the marker file
  exists in the source tree and is declared in `pyproject.toml` package-data.

---

## 4. What is NOT in this spec

- The observation-level cache (see `v0_3_cache_architecture.md`).
- The weekly live-series health-check workflow: a **separate monitoring**
  workflow that opens an issue when a code dies, not a merge-blocking CI job.
  It needs `BCCH_TOKEN` as a GitHub secret → requires explicit approval; it is
  NOT part of v0.3 scope here.
- Versioning the official `series.xlsx` in the repo (separate decision).

## 5. Test constraints

- No live API calls in the test suite (labels pinned from a captured
  `descripEsp`, not a network round-trip).
- Cleanup is tested by absence: after deletion, grep-based assertions or
  import checks confirm the removed symbols raise `AttributeError`/`ImportError`
  and the deleted paths are gone.
- Full suite must stay green: 341 existing tests, minus any tests that
  asserted on the now-deleted symbols (see execution plan for the exact edits).

---

## 6. Amendments made during implementation

§2 scopes this phase to "remove only what is **provably unused**", and §2.1 says
"if any call site exists, keep it and remove it from this spec's scope instead".
A repo-wide grep before deleting found call sites the original dependent list
missed, so those deletions moved out of scope.

| # | Spec said | Done | Why |
|---|---|---|---|
| 1 | §2.1 delete `safe_date`, `normalize_series` | **Kept** | `examples/demo.py` imports both (line 27, used at line 82). §2.1's own rule applies: a call site exists, so it stays. |
| 2 | §2.3 delete `data/chile-macro.json`, `data/macro-chile-lean.csv`, `data/archived/` | **Kept** | `examples/demo.py` reads the JSON *and* the CSV; `examples/charts_tutorial.ipynb` reads the CSV; `demo.py` runs today without a token. These files are the demo's inputs, not dead weight. |
| 3 | §2.3 "known dependents" list | `examples/demo.py` and `examples/charts_tutorial.ipynb` added | The list named only `charts_demo.py` and the two test files; the biggest dependent was missing. |
| 4 | §2.3 delete `examples/charts_demo.py` | **Kept** | Its only problem was the CSV, which is staying. It works today (writes `examples/dolar_2000_2010.png`). |
| 5 | §2.1 delete `count_valid` | Deleted | Genuinely unused outside its own definition and one line of the test report (the report now counts inline). |
| 6 | §2.2 delete `SeriesNotFoundError` | Deleted | Never raised, not exported in `econchile/__init__.py`; only its own definition and one test import referenced it. |
| 7 | §3 `py.typed` | As specced | Empty `econchile/py.typed`, declared in `[tool.setuptools.package-data]`, shipped via `MANIFEST.in`. |
| 8 | §2.4 delete 9 merged local branches | Not done | Local housekeeping only, no commit; the count is 12, not 9. Left for you to run when you want. |

`tests/test_release.py::TestV03DeadCodeRemoved` now pins this: the demo and its
data files must keep existing, and the two deleted names must stay deleted.
