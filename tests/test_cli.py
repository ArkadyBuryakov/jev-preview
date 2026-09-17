"""The command line: flags that must work without ever starting the TUI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_preview import __version__, cli
from jev_preview.config import API_KEY_ENV, Config


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_mentions_the_environment(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert API_KEY_ENV in out
    assert "--requests-dir" in out


class TestSetKey:
    def test_writes_the_key(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["--set-key", "sk-from-the-flag"]) == 0
        stored = json.loads((tmp_path / "config" / "config.json").read_text())
        assert stored["api_key"] == "sk-from-the-flag"
        assert "sk-…flag" in capsys.readouterr().out

    def test_reads_stdin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr(cli.sys, "stdin", io.StringIO("sk-piped-in\n"))
        assert cli.main(["--set-key", "-"]) == 0
        stored = json.loads((tmp_path / "config" / "config.json").read_text())
        assert stored["api_key"] == "sk-piped-in"

    def test_blank_key_is_an_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["--set-key", "   "]) == 2
        assert "no key given" in capsys.readouterr().err

    def test_keeps_other_settings(self, tmp_path: Path) -> None:
        path = tmp_path / "config" / "config.json"
        Config(api_key="old", base_url="https://x.test/v1", path=path).save()
        cli.main(["--set-key", "sk-new"])
        stored = json.loads(path.read_text())
        assert stored == {"api_key": "sk-new", "base_url": "https://x.test/v1"}


class TestShowConfig:
    def test_reports_where_things_live(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--show-config"]) == 0
        out = capsys.readouterr().out
        assert str(tmp_path / "config" / "config.json") in out
        assert str(tmp_path / "requests") in out
        assert "not set" in out

    def test_reports_an_environment_key_as_such(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv(API_KEY_ENV, "sk-1234567890abcd")
        cli.main(["--show-config"])
        out = capsys.readouterr().out
        assert "sk-…abcd" in out
        assert "(environment)" in out

    def test_honours_requests_dir_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--show-config", "--requests-dir", str(tmp_path / "elsewhere")])
        assert str(tmp_path / "elsewhere") in capsys.readouterr().out


class TestRun:
    def test_starts_the_app_with_config_and_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jev_preview.app as app_module

        started: dict[str, object] = {}

        class FakeApp:
            def __init__(self, config: Config, directory: Path) -> None:
                started["config"] = config
                started["directory"] = directory

            def run(self) -> None:
                started["ran"] = True

        monkeypatch.setattr(app_module, "JevApp", FakeApp)
        assert cli.main([]) == 0
        assert started["ran"] is True
        assert started["directory"] == tmp_path / "requests"
        assert isinstance(started["config"], Config)

    def test_requests_dir_flag_reaches_the_app(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jev_preview.app as app_module

        seen: dict[str, object] = {}

        class FakeApp:
            def __init__(self, config: Config, directory: Path) -> None:
                seen["directory"] = directory

            def run(self) -> None:
                pass

        monkeypatch.setattr(app_module, "JevApp", FakeApp)
        cli.main(["-d", str(tmp_path / "custom")])
        assert seen["directory"] == tmp_path / "custom"
