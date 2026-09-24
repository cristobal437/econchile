# specs/

Specs are the source of truth for behaviour. There are two kinds:

- **Module specs** (`client_spec.md`, `fetcher_spec.md`, `cache_spec.md`,
  `offline_spec.md`, `parsers_spec.md`, `types_spec.md`, ...): the v0.1
  baseline, one per module.
- **Release delta specs** (`v011_*`, `v012_*`, `v02_*`, `v021_*`, `v022_*`):
  what each release changed on top of the baseline.

**When they disagree, the newest delta spec wins.** The tests in `tests/`
are the executable contract and always reflect the current behaviour.

## Known drift in the baseline module specs

| Baseline says | Current behaviour | Changed in |
|---|---|---|
| Missing token raises `ValueError` at construction (`client_spec.md`) | Token optional at construction; `BcchApiError` at fetch time, before any network I/O | v0.1.2 |
| Decode order UTF-16 BOM → UTF-8 (`fetcher_spec.md`) | UTF-16 BOM → UTF-8 → latin-1 | v0.1.1 |
| HTTP 4xx never retried | HTTP 429 is retried; other 4xx are not | v0.1.1 |
| "v0.1 catalog" of 7 series | 28 indexed series + any raw BCCh code | v0.2.0 / v0.2.1 |
| Unknown series → `KeyError` | Code-shaped strings pass through as raw codes; only unknown names raise `KeyError` | v0.2.1 |
| `desde > hasta` unspecified | `ValueError` before any I/O | v0.2.1 |
| `source` may be `"cache"` / `"partial"` | Always `"api"` (cache returns the stored result unchanged) | — |
