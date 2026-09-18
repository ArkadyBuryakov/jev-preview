"""The release-notes extractor release.yml feeds the GitHub release.

It lives in packaging/, outside the importable package, so it is loaded by
path rather than imported. The tests that matter are the two the workflow
depends on: the real CHANGELOG.md has a section for the current version, and
a version it does not know about fails rather than printing something empty.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from jev_preview import __version__

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "changelog" / "release-notes"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_loader(
        "release_notes", importlib.machinery.SourceFileLoader("release_notes", str(SCRIPT))
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = module
    spec.loader.exec_module(module)
    return module


release_notes = _load()


def test_the_changelog_documents_the_current_version() -> None:
    """A release without notes falls back to a commit list, so catch it here."""
    assert release_notes.section(release_notes.CHANGELOG.read_text(), __version__)


def test_an_unknown_version_has_no_section() -> None:
    assert release_notes.section("## [1.0.0] - 2026-01-01\n\nHi.\n", "2.0.0") is None


def test_the_section_stops_at_the_next_release() -> None:
    text = "## [2.0.0] - 2026-02-01\n\nNewer.\n\n## [1.0.0] - 2026-01-01\n\nOlder.\n"
    assert release_notes.section(text, "2.0.0") == "Newer."
    assert release_notes.section(text, "1.0.0") == "Older."


def test_link_reference_definitions_are_left_out() -> None:
    """They sit inside the last section but belong to the document."""
    text = "## [1.0.0] - 2026-01-01\n\nHi.\n\n[1.0.0]: https://example.invalid/v1.0.0\n"
    assert release_notes.section(text, "1.0.0") == "Hi."


def test_an_unreleased_heading_bounds_the_section_below_it() -> None:
    text = "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\nHi.\n"
    assert release_notes.section(text, "Unreleased") == ""
    assert release_notes.section(text, "1.0.0") == "Hi."


@pytest.mark.parametrize("heading", ["## [1.0.0] - 2026-01-01", "## [1.0.0]", "## 1.0.0"])
def test_the_heading_may_be_unlinked_or_undated(heading: str) -> None:
    assert release_notes.section(f"{heading}\n\nHi.\n", "1.0.0") == "Hi."
