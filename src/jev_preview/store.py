"""On-disk format for saved requests: one JSON file per request."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "jev-latest"
MAX_SLUG = 48


class StoreError(Exception):
    """A saved request could not be read or written."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:MAX_SLUG].strip("-") or "untitled"


def new_body(model: str = DEFAULT_MODEL) -> dict[str, Any]:
    return {"model": model, "state": "", "questions": {}}


@dataclass(slots=True)
class Attempt:
    """One response (or error) recorded for a request."""

    timestamp: str
    status: int | None
    duration_ms: int
    body: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "timestamp": self.timestamp,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "body": self.body,
        }
        if self.error:
            data["error"] = self.error
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attempt:
        return cls(
            timestamp=str(data.get("timestamp", "")),
            status=data.get("status") if isinstance(data.get("status"), int) else None,
            duration_ms=int(data.get("duration_ms") or 0),
            body=data.get("body"),
            error=data.get("error"),
        )


@dataclass(slots=True)
class SavedRequest:
    """A request body plus everything the API ever answered for it."""

    name: str = ""
    created_at: str = field(default_factory=now_iso)
    body: dict[str, Any] = field(default_factory=new_body)
    responses: list[Attempt] = field(default_factory=list)
    #: buffer text kept verbatim while the questions JSON does not parse
    draft_questions: str | None = None
    #: while true the name tracks the context; a rename pins it
    auto_name: bool = True
    path: Path | None = None

    # --- convenience accessors -------------------------------------------
    @property
    def state(self) -> Any:
        return self.body.get("state", "")

    @state.setter
    def state(self, value: Any) -> None:
        self.body["state"] = value

    @property
    def questions(self) -> dict[str, Any]:
        found = self.body.get("questions")
        return found if isinstance(found, dict) else {}

    @questions.setter
    def questions(self, value: dict[str, Any]) -> None:
        self.body["questions"] = value

    @property
    def model(self) -> str:
        model = self.body.get("model")
        return model if isinstance(model, str) and model else DEFAULT_MODEL

    @model.setter
    def model(self, value: str) -> None:
        self.body["model"] = value

    @property
    def is_empty(self) -> bool:
        return not (self.state or self.questions or self.responses or self.draft_questions)

    def questions_text(self) -> str:
        """The questions buffer: the unparsed draft if one survived, else the JSON."""
        if self.draft_questions is not None:
            return self.draft_questions
        if not self.questions:
            return ""
        return json.dumps(self.questions, indent=2, ensure_ascii=False)

    def default_name(self) -> str:
        """Derive a human name from the first line of the context."""
        state = self.state
        text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return first_line[:MAX_SLUG].strip(" ,.;:-") or "untitled"

    # --- persistence ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "created_at": self.created_at,
            "body": self.body,
            "responses": [a.to_dict() for a in self.responses],
        }
        if self.draft_questions is not None:
            data["draft_questions"] = self.draft_questions
        if self.auto_name:
            data["auto_name"] = True
        return data

    @classmethod
    def load(cls, path: Path) -> SavedRequest:
        """Read one request file.

        Raises:
            StoreError: the file is unreadable or is not a request document.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StoreError(f"cannot read {path.name}: {exc}") from exc
        except ValueError as exc:
            raise StoreError(f"{path.name} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise StoreError(f"{path.name} is not a request document")

        body = raw.get("body")
        responses = raw.get("responses")
        return cls(
            name=str(raw.get("name") or path.stem),
            created_at=str(raw.get("created_at") or ""),
            body=body if isinstance(body, dict) else new_body(),
            responses=[
                Attempt.from_dict(a)
                for a in (responses if isinstance(responses, list) else [])
                if isinstance(a, dict)
            ],
            draft_questions=raw.get("draft_questions"),
            auto_name=bool(raw.get("auto_name", False)),
            path=path,
        )

    def save(self, directory: Path) -> Path:
        """Write the file, moving it if the name (and so the slug) has changed.

        Raises:
            StoreError: the directory or the file could not be written.
        """
        if self.auto_name:
            self.name = self.default_name()
        if not self.name:
            self.name = "untitled"

        slug = slugify(self.name)
        old = self.path
        path = old if old is not None and _matches_slug(old.stem, slug) else None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if path is None:
                path = _free_path(directory, slug, ignore=old)
            path.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            if old is not None and old != path:
                old.unlink(missing_ok=True)
        except OSError as exc:
            raise StoreError(f"cannot save {self.name!r}: {exc}") from exc
        self.path = path
        return path

    def rename(self, new_name: str, directory: Path) -> Path:
        """Name the request explicitly; it stops tracking the context from here on."""
        self.auto_name = False
        self.name = new_name.strip() or "untitled"
        return self.save(directory)

    def delete(self) -> None:
        """Remove the file backing this request, if it has one."""
        if self.path is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise StoreError(f"cannot delete {self.path.name}: {exc}") from exc
        self.path = None


def _matches_slug(stem: str, slug: str) -> bool:
    """True for "notes" and its de-duplicated siblings "notes-2", "notes-3", ..."""
    return stem == slug or re.fullmatch(rf"{re.escape(slug)}-\d+", stem) is not None


def _free_path(directory: Path, slug: str, ignore: Path | None = None) -> Path:
    candidate = directory / f"{slug}.json"
    n = 2
    while candidate.exists() and candidate != ignore:
        candidate = directory / f"{slug}-{n}.json"
        n += 1
    return candidate


def list_requests(directory: Path) -> list[Path]:
    """Every saved request, most recently modified first."""
    if not directory.is_dir():
        return []
    try:
        return sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
