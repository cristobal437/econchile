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

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import econchile  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "workflow.yml")
WALKTHROUGH_PATH = os.path.join(REPO_ROOT, "examples", "econchile_walkthrough.ipynb")

# The sdist ships tests/ but not .github/ or examples/ — skip the checks that
# need those files there, so `pytest` passes inside the sdist (v0.2.2).
needs_workflow = pytest.mark.skipif(
    not os.path.exists(WORKFLOW_PATH), reason="CI workflow not shipped in the sdist"
)
needs_examples = pytest.mark.skipif(
    not os.path.exists(WALKTHROUGH_PATH), reason="examples/ not shipped in the sdist"
)


def _read(rel_path: str) -> str:
    with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


@needs_workflow
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


@needs_workflow
class TestCiHardening:
    """CI covers supported Pythons and cannot publish a mismatched tag (v0.2.2)."""

    def test_matrix_covers_310_to_314(self):
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        for v in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            assert f'"{v}"' in wf, f"Python {v} missing from the CI matrix"

    def test_publish_checks_tag_matches_version(self):
        """A forgotten version bump must fail loudly, not skip silently."""
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        publish_block = wf[wf.index("  publish:"):]
        assert "GITHUB_REF_NAME" in publish_block
        assert "pyproject.toml" in publish_block

    def test_package_is_built_and_checked_on_prs(self):
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        test_block = wf[wf.index("  test:"):wf.index("  publish:")]
        assert "twine check" in test_block

    def test_no_node20_actions(self):
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        assert "actions/checkout@v4" not in wf
        assert "actions/setup-python@v5" not in wf

    def test_default_permissions_read_only(self):
        wf = _read(os.path.join(".github", "workflows", "workflow.yml"))
        top = wf[:wf.index("jobs:")]
        assert re.search(r"^permissions:\s*\n\s+contents:\s*read", top, re.MULTILINE)


class TestPackaging:
    """Build metadata that ships in every sdist (v0.2.2)."""

    def test_setuptools_supports_spdx_license(self):
        """`license = "MIT"` (PEP 639) needs setuptools >= 77."""
        pyproject = _read("pyproject.toml")
        match = re.search(r'"setuptools>=(\d+)', pyproject)
        assert match and int(match.group(1)) >= 77

    def test_classifiers_cover_ci_matrix(self):
        pyproject = _read("pyproject.toml")
        for v in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            assert f"Programming Language :: Python :: {v}" in pyproject

    def test_sdist_ships_indexed_catalog(self):
        """tests/test_catalog_labels.py needs data/indexed_series.json in the sdist."""
        assert "include data/indexed_series.json" in _read("MANIFEST.in")


@needs_examples
class TestWalkthrough:
    """The walkthrough ships pre-rendered and current (v0.2.2)."""

    def test_walkthrough_is_executed(self):
        assert '"output_type"' in _read(os.path.join("examples", "econchile_walkthrough.ipynb"))

    def test_walkthrough_not_stale(self):
        nb = _read(os.path.join("examples", "econchile_walkthrough.ipynb"))
        assert "ALL 7 SERIES" not in nb


class TestV03Packaging:
    """v0.3: the package ships PEP 561 type information."""

    def test_py_typed_marker_exists(self):
        assert os.path.exists(os.path.join(REPO_ROOT, "econchile", "py.typed")), (
            "econchile/py.typed is missing - type checkers cannot see the inline hints"
        )

    def test_py_typed_declared_as_package_data(self):
        pyproject = _read("pyproject.toml")
        assert "[tool.setuptools.package-data]" in pyproject, (
            "py.typed must be declared or the wheel drops it"
        )
        # Take the block up to the next section header (a line starting with "["),
        # not up to the first "[" - that one is the list bracket in ["py.typed"].
        section = pyproject.split("[tool.setuptools.package-data]", 1)[1]
        section = section.split("\n[", 1)[0]
        assert "econchile" in section, section
        assert "py.typed" in section, section

    def test_sdist_ships_py_typed(self):
        assert "econchile/py.typed" in _read("MANIFEST.in")

    def test_readme_no_longer_claims_28_series(self):
        assert "28 series" not in _read("README.md")


class TestV03DeadCodeRemoved:
    """v0.3 deleted only what was provably unused - and must not creep further.

    examples/demo.py is the documented one-command offline demo (AGENTS.md).
    It imports safe_date/normalize_series and reads the bundled data files, so
    those are load-bearing and a future "cleanup" must not delete them.
    """

    def test_count_valid_is_gone(self):
        from econchile import converters

        assert not hasattr(converters, "count_valid")

    def test_series_not_found_error_is_gone(self):
        from econchile import parsers

        assert not hasattr(parsers, "SeriesNotFoundError")

    def test_safe_date_and_normalize_series_stay(self):
        from econchile import converters

        assert hasattr(converters, "safe_date")
        assert hasattr(converters, "normalize_series")

    @needs_examples
    def test_offline_demo_still_exists(self):
        """Only the demo's existence is pinned, not its inputs.

        Its bundled data files are not a permanent constraint: if a cleanup
        wants them gone, update examples/demo.py in the same commit. The tests
        are the contract; the demo follows them, never the other way round.
        """
        assert os.path.exists(os.path.join(REPO_ROOT, "examples", "demo.py"))


class TestV031SecurityFloor:
    """v0.3.1: the certifi floor for the eMudhra/emSign CA compromise.

    requests verifies TLS with certifi's bundle and nothing else
    (requests/certs.py is `from certifi import where`), so declaring the floor
    is what keeps an installed econchile off a trust store that still contains
    the compromised emSign root (GHSA-6q48-6c7w-9gw2).
    """

    FLOOR = (2026, 10, 19)

    def _dependencies_line(self) -> str:
        pyproject = _read("pyproject.toml")
        match = re.search(r"^dependencies\s*=\s*(.+)$", pyproject, re.MULTILINE)
        assert match, "no dependencies = [...] found in pyproject.toml"
        return match.group(1)

    def test_certifi_is_a_direct_dependency(self):
        """A transitive-only certifi can be satisfied by a stale bundle."""
        assert "certifi" in self._dependencies_line(), (
            "certifi must be declared in [project] dependencies, not left to "
            "requests to pull in, so the floor is enforced at install time"
        )

    def test_certifi_floor_is_at_least_the_patched_release(self):
        line = self._dependencies_line()
        match = re.search(r"certifi>=(\d{4})\.(\d{1,2})\.(\d{1,2})", line)
        assert match, f"no parsable certifi floor in: {line}"
        floor = tuple(int(part) for part in match.groups())
        assert floor >= self.FLOOR, (
            f"certifi floor {match.group(0)} is older than the emSign fix; "
            "the compromised emSign ECC Root CA - G3 is still trusted by "
            "certifi before 2026.10.19"
        )
