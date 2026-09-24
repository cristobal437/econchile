# v0.2.2 — Packaging & CI hardening — Spec

Patch release. No library behaviour changes.

1. **sdist tests pass.** Tests needing files absent from the sdist skip
   themselves (`.github/workflows/workflow.yml`, `examples/`, legacy
   `data/chile-macro.json` + `data/macro-chile-lean.csv`).
   `data/indexed_series.json` ships (MANIFEST.in `include`).
2. **Build requirement** `setuptools>=77` (PEP 639 `license = "MIT"`).
3. **CI matrix** 3.10–3.14, `fail-fast: false`; classifiers match.
4. **PR build check**: `python -m build` + `twine check --strict` (3.12 leg).
5. **Tag guard**: publish job fails if `v<tag>` ≠ `pyproject.toml` version
   (no more silent `skip-existing` no-op on a forgotten bump).
6. **Actions off Node 20**: `checkout@v5`, `setup-python@v6`; top-level
   `permissions: contents: read` (publish keeps `id-token: write`).
7. **Walkthrough** executed in place (pre-rendered), no "ALL 7 SERIES";
   outputs must not contain the token or local paths.
8. `specs/README.md`: how specs are organised + known drift.

## Tests (contract) — `tests/test_release.py`

`TestCiHardening` (skips without `.github/`), `TestPackaging` (always),
`TestWalkthrough` (skips without `examples/`).
