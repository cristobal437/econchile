"""
Release-level contract tests (v0.1.3 launch hardening).

Guards the things that unit tests cannot see: CI workflow behavior,
version drift between pyproject.toml and the package, and README claims
that new users will actually copy-paste.

Run with:
    pytest tests/test_release.py -v
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import econchile  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel_path: str) -> str:
    with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


class TestPublishGuard:
    """The PyPI publish job must NEVER run on PRs or main pushes."""

    def test_publish_job_guarded_to_tags(self):
        """publish job has an `if: startsWith(github.ref, 'refs/tags/v')` guard."""
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        # Find the publish job block (from `publish:` to the next top-level key).
        publish_start = wf.index("  publish:")
        next_job = wf.find("\n  ", publish_start + 10)
        publish_block = wf[publish_start:next_job]
        assert "startsWith(github.ref, 'refs/tags/v')" in publish_block, (
            "publish job must be guarded with "
            "`if: startsWith(github.ref, 'refs/tags/v')` "
            "so PRs and main pushes never publish to PyPI"
        )

    def test_publish_job_still_needs_tests(self):
        """Guard added without breaking `needs: test`."""
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        assert "needs: test" in wf


class TestVersionConsistency:
    """pyproject.toml and econchile.__version__ must never drift."""

    def test_pyproject_version_matches_package_version(self):
        pyproject = _read("pyproject.toml")
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
        assert match, "no version = \"...\" found in pyproject.toml"
        assert match.group(1) == econchile.__version__, (
            f"pyproject.toml says {match.group(1)!r} but "
            f"econchile.__version__ is {econchile.__version__!r}"
        )


class TestReadmeClaims:
    """README copy-paste claims must match what the package does."""

    def test_quickstart_documents_powershell_token(self):
        """Windows users must see the PowerShell token variant."""
        readme = _read("README.md")
        assert '$env:BCCH_TOKEN="your-token-here"' in readme, (
            "README must show the PowerShell token setup "
            "($env:BCCH_TOKEN=...) next to the export line"
        )

    def test_no_unsupported_pandas_claims(self):
        """README must not claim pandas/DataFrame support (not shipped)."""
        readme = _read("README.md")
        assert "to_dataframe" not in readme
        assert "to_frame" not in readme

    def test_no_stale_token_requirement(self):
        """Token is optional at construction since 0.1.2 — README must not say otherwise."""
        readme = _read("README.md")
        assert "required for v0.1" not in readme


class TestPublicExports:
    """Users can import clients, data types and errors from the top level (v0.2.1)."""

    NAMES = [
        "BcchClient", "OfflineClient", "Series", "SeriesMeta", "SeriesResult",
        "Observation", "Frequency", "Representation",
        "BcchError", "BcchApiError", "BcchCacheError", "BcchOfflineError",
    ]

    def test_top_level_names_exported(self):
        for name in self.NAMES:
            assert hasattr(econchile, name), f"econchile.{name} missing"
            assert name in econchile.__all__, f"{name} not in __all__"
