"""Open a scratch buffer in $EDITOR and hand back what the user saved."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

QUESTIONS_TEMPLATE = """{
  "urgency": {
    "type": "noul",
    "instructions": "Does this message express urgency?"
  }
}
"""


class EditorError(RuntimeError):
    """The external editor could not be run."""


def editor_command() -> list[str]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if os.name == "nt":
        # A backslash is a path separator on Windows, not an escape character.
        parts = [part.strip('"') for part in shlex.split(editor, posix=False)]
    else:
        parts = shlex.split(editor)
    if not parts:
        raise EditorError("$EDITOR is set to an empty command")
    return parts


def edit_text(initial: str, suffix: str) -> str:
    """Round-trip `initial` through the user's editor.

    Raises:
        EditorError: the editor is missing, or the buffer could not be used.
    """
    command = editor_command()
    fd, name = tempfile.mkstemp(suffix=suffix, prefix="jev-")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(initial)
        try:
            subprocess.call([*command, str(path)])
        except OSError as exc:
            raise EditorError(f"cannot run {command[0]!r}: {exc}") from exc
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EditorError(str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


def dump_state(state: Any) -> tuple[str, str]:
    """Render `state` for editing. Plain strings stay plain; structures are JSON."""
    if isinstance(state, str):
        return state, ".txt"
    return json.dumps(state, indent=2, ensure_ascii=False), ".json"


def parse_state(text: str) -> Any:
    """JSON objects/arrays round-trip as structure; anything else stays text."""
    stripped = text.strip()
    if stripped[:1] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return text.rstrip("\n")
