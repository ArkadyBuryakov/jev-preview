"""`python -m jev_preview` must reach the same entry point as the console script."""

from __future__ import annotations

import subprocess
import sys

from jev_preview import __version__


def test_module_entry_point_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jev_preview", "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert __version__ in result.stdout


def test_console_script_is_declared() -> None:
    from importlib.metadata import entry_points

    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts.get("jev-preview") == "jev_preview.cli:main"
    assert scripts.get("jev") == "jev_preview.cli:main"
