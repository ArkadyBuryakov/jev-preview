"""The $EDITOR escape hatch and the state text/JSON boundary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jev_preview import editor
from jev_preview.editor import EditorError


class TestParseState:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"a": 1}', {"a": 1}),
            ("[1, 2]", [1, 2]),
            ('  {\n  "a": 1\n}  ', {"a": 1}),
        ],
    )
    def test_json_structures_round_trip(self, text: str, expected: object) -> None:
        assert editor.parse_state(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["a support ticket", "{ not json", '"a bare string"', "42", "", "{"],
    )
    def test_everything_else_stays_text(self, text: str) -> None:
        assert editor.parse_state(text) == text.rstrip("\n")

    def test_trailing_newlines_are_trimmed(self) -> None:
        assert editor.parse_state("hello\n\n") == "hello"


class TestDumpState:
    def test_strings_stay_plain(self) -> None:
        assert editor.dump_state("hello") == ("hello", ".txt")

    def test_structures_become_json(self) -> None:
        text, suffix = editor.dump_state({"a": 1})
        assert suffix == ".json"
        assert text == '{\n  "a": 1\n}'

    def test_round_trip_through_parse(self) -> None:
        for state in ["plain text", {"a": 1}, [1, 2]]:
            assert editor.parse_state(editor.dump_state(state)[0]) == state


class TestEditorCommand:
    def test_visual_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VISUAL", "code --wait")
        monkeypatch.setenv("EDITOR", "vim")
        assert editor.editor_command() == ["code", "--wait"]

    def test_editor_is_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "nano")
        assert editor.editor_command() == ["nano"]

    def test_vi_when_nothing_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        assert editor.editor_command() == ["vi"]

    def test_empty_editor_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDITOR", "   ")
        with pytest.raises(EditorError):
            editor.editor_command()


class TestEditText:
    def test_round_trips_through_a_real_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        script = tmp_path / "fake_editor.py"
        script.write_text(
            "import pathlib, sys\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "p.write_text(p.read_text() + ' edited')\n"
        )
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
        assert editor.edit_text("original", ".txt") == "original edited"

    def test_temp_file_is_cleaned_up(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        seen: list[str] = []
        monkeypatch.setenv("EDITOR", "true")
        monkeypatch.setattr(editor.subprocess, "call", lambda argv: seen.append(argv[-1]))
        editor.edit_text("x", ".json")
        assert seen and seen[0].endswith(".json")
        assert not Path(seen[0]).exists()

    def test_missing_editor_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDITOR", "definitely-not-a-real-editor-xyz")

        def boom(argv: list[str]) -> int:
            raise OSError("No such file or directory")

        monkeypatch.setattr(editor.subprocess, "call", boom)
        with pytest.raises(EditorError, match="cannot run"):
            editor.edit_text("x", ".txt")

    def test_editor_exit_code_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDITOR", "false")
        monkeypatch.setattr(editor.subprocess, "call", lambda _argv: 1)
        assert editor.edit_text("kept", ".txt") == "kept"

    def test_template_is_valid_json(self) -> None:
        import json

        assert "urgency" in json.loads(editor.QUESTIONS_TEMPLATE)


def test_unicode_survives_the_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = tmp_path / "echo_editor.py"
    script.write_text("import sys\n")  # leaves the file untouched
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    assert editor.edit_text("héllo — ünïcode ✓", ".txt") == "héllo — ünïcode ✓"


def test_temp_files_land_in_the_system_temp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    captured: list[str] = []
    monkeypatch.setenv("EDITOR", "true")
    monkeypatch.setattr(editor.subprocess, "call", lambda argv: captured.append(argv[-1]))
    editor.edit_text("x", ".txt")
    assert captured[0].startswith(str(Path(tempfile.gettempdir()) / "jev-"))
