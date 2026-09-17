"""Shared fixtures.

Every test runs against a temporary config directory and requests directory, so a
test run can never read or write the developer's own key or saved requests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from jev_preview.config import (
    API_KEY_ENV,
    BASE_URL_ENV,
    CONFIG_DIR_ENV,
    REQUESTS_DIR_ENV,
    Config,
)
from jev_preview.store import Attempt, SavedRequest

#: An editor command that exists, does nothing, and can never block a test run.
_NO_EDITOR = "true"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point config and storage at tmp_path, and clear inherited environment.

    VISUAL and EDITOR are cleared too: a test that forgets to set them must fail
    fast rather than launch the developer's real editor and hang the run.
    """
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "requests"
    monkeypatch.setenv(CONFIG_DIR_ENV, str(config_dir))
    monkeypatch.setenv(REQUESTS_DIR_ENV, str(data_dir))
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    monkeypatch.setenv("VISUAL", "")
    monkeypatch.setenv("EDITOR", _NO_EDITOR)
    yield tmp_path


@pytest.fixture
def requests_path(tmp_path: Path) -> Path:
    directory = tmp_path / "requests"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """A configured install: a key is present, so no onboarding prompt appears."""
    return Config(
        api_key="sk-test-key-123456",
        base_url="https://api.example.test/v1",
        path=tmp_path / "config" / "config.json",
    )


@pytest.fixture
def answer_body() -> dict[str, Any]:
    """A representative System One success payload."""
    return {
        "model": "jev-latest",
        "usage": {"input_tokens": 41, "output_tokens": 7},
        "answers": {
            "is_urgent": {"type": "noul", "noul": 0.82, "confidence": 0.91},
            "department": {
                "type": "choice",
                "choice": "billing",
                "probabilities": {"billing": 0.7, "technical": 0.2, "sales": 0.1},
            },
            "frustration": {
                "type": "score",
                "score": 1.4,
                "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
                "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
            },
        },
    }


@pytest.fixture
def saved_request(requests_path: Path) -> SavedRequest:
    """A request already on disk, with one recorded response."""
    request = SavedRequest(
        name="A saved ticket",
        auto_name=False,
        body={
            "model": "jev-latest",
            "state": "My payouts keep failing",
            "questions": {"is_urgent": {"type": "noul", "instructions": "Urgent?"}},
        },
        responses=[
            Attempt(
                timestamp="2026-01-01T00:00:00+00:00",
                status=200,
                duration_ms=120,
                body={"answers": {"is_urgent": {"type": "noul", "noul": 0.5}}},
            )
        ],
    )
    request.save(requests_path)
    return request


@pytest.fixture
def write_request(requests_path: Path):
    """Drop a raw document into the requests directory."""

    def _write(name: str, payload: dict[str, Any]) -> Path:
        path = requests_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    return _write
