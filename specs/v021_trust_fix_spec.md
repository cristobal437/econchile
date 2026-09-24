# v0.2.1 — Trust fix — Spec

Patch release. No new series, no value changes: fix what the library
*says* so it matches what it *does* and what BCCh publishes.

## 1. Series labels (verified live 2026-09-24 + official `series.xlsx`)

| Member | Wrong (v0.2.0) | Correct (API `descripEsp` / catalog) |
|---|---|---|
| EURO | USD per EUR | **EUR per USD** ("Euro por dólar de EEUU"; USD/EURO = official CLP/EUR 1081.49) |
| TPM_EXPECTED | 11 months ahead | **current month** ("en el mes, mediana"; catalog: "en la siguiente reunión"). 11 months is `F089.TPM.TAS.14.M` (not added in 0.2.1) |
| IPC_SAE | seasonally adjusted | **sin alimentos ni energía** (core CPI) |
| TCM | "tipo de cambio medio", base 199502=1 | **nominal multilateral**, index 2 Jan 1998=100 |
| TCR | base 199101=1 | **average 1986=100** |
| IVP | Índice de Valor Real | **Índice de valor promedio** |

Codes are unchanged. Fix in `series_map.py`, `data/indexed_series.json`
(regenerated from the enum), README table.

## 2. Raw BCCh codes work in both clients

`BcchClient.get` / `OfflineClient.get` accept any string that looks like a
BCCh code (`^[A-Za-z]\d{3}(\.[A-Za-z0-9_]+)+$`) even if it is not in the
indexed enum. It passes through as a plain string: `result.series` is the
code string, cache key is the code. Unknown human names still raise
`KeyError`; the message now mentions raw codes. `search()` stays
indexed-only (documented).

## 3. `desde > hasta` → `ValueError` before any I/O

## 4. Top-level exports

`from econchile import OfflineClient, Observation, Frequency,
Representation, BcchError, BcchApiError, BcchCacheError, BcchOfflineError`.

## 5. Docs sync

README (token line, raw codes, `source`/`metadata` truth, where errors
live, charts tutorial link), AGENTS.md (version, test count, 429 retry),
SECURITY.md (0.2.x), stale docstrings (`v0.1`, DataFrame, `partial`),
stale test names, CHANGELOG, version 0.2.1.

## Tests (contract)

- `tests/test_catalog_labels.py`: the 6 labels; README says EUR per USD;
  `data/indexed_series.json` equals the enum (code, freq, repr, titles).
- `test_client.py` / `test_offline.py`: raw code outside the catalog passes
  through and is cached under the code; KeyError hint; `desde > hasta`.
- `test_fetcher.py`: `desde > hasta` → `ValueError`.
- `test_release.py`: top-level exports; no "required for v0.1" in README.

## Out of scope (v0.2.2 / v0.3)

New series (CLP per EUR, TPM 11m), packaging/CI hardening, walkthrough
re-render, observation-level offline cache.
