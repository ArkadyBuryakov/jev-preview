"""The config file: precedence, resilience, and not leaking the key."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from jev_preview.config import (
    API_KEY_ENV,
    BASE_URL_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODELS,
    DEFAULT_TIMEOUT,
    Config,
    config_dir,
    config_path,
    mask_key,
    requests_dir,
)


def test_defaults_when_no_file(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "missing.json")
    assert not config.has_api_key
    assert config.base_url == DEFAULT_BASE_URL
    assert config.models == DEFAULT_MODELS
    assert config.timeout == DEFAULT_TIMEOUT


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    Config(api_key="sk-abc", base_url="https://x.test/v1", models=("a", "b"), path=path).save()
    reloaded = Config.load(path)
    assert reloaded.api_key == "sk-abc"
    assert reloaded.base_url == "https://x.test/v1"
    assert reloaded.models == ("a", "b")


def test_save_is_owner_only(tmp_path: Path) -> None:
    path = Config(api_key="sk-abc", path=tmp_path / "c" / "config.json").save()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.parent.is_dir()


def test_save_omits_defaults(tmp_path: Path) -> None:
    path = Config(api_key="sk-abc", path=tmp_path / "config.json").save()
    assert json.loads(path.read_text()) == {"api_key": "sk-abc"}


def test_env_key_wins_and_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    Config(api_key="sk-from-file", path=path).save()
    monkeypatch.setenv(API_KEY_ENV, "sk-from-env")

    config = Config.load(path)
    assert config.api_key == "sk-from-env"
    assert config.key_from_env is True

    config.save()
    assert "api_key" not in json.loads(path.read_text())


def test_with_api_key_makes_it_ours_to_persist(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    typed = Config.load(path).with_api_key("  sk-typed  ")
    assert typed.api_key == "sk-typed"
    assert typed.key_from_env is False
    typed.save()
    assert json.loads(path.read_text())["api_key"] == "sk-typed"


def test_base_url_env_override_and_trailing_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BASE_URL_ENV, "https://local.test/v1/")
    assert Config.load(tmp_path / "c.json").base_url == "https://local.test/v1"


@pytest.mark.parametrize(
    "content",
    ["not json at all", "[]", '"a string"', '{"models": "nope", "timeout": -1}'],
)
def test_malformed_file_is_survivable(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(content)
    config = Config.load(path)
    assert config.models == DEFAULT_MODELS
    assert config.timeout == DEFAULT_TIMEOUT
    assert not config.has_api_key


def test_unreadable_file_is_survivable(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"api_key": "sk-abc"}')
    path.chmod(0o000)
    try:
        assert not Config.load(path).has_api_key
    finally:
        path.chmod(0o600)


def test_save_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    Config(api_key="sk-one", path=path).save()
    Config(api_key="sk-two", path=path).save()
    assert json.loads(path.read_text())["api_key"] == "sk-two"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    ("key", "expected"),
    [("", ""), ("short", "…" * 3), ("sk-1234567890abcd", "sk-…abcd")],
)
def test_mask_key(key: str, expected: str) -> None:
    assert mask_key(key) == expected


def test_directory_overrides_are_honoured(tmp_path: Path) -> None:
    # set by the autouse isolation fixture
    assert config_dir() == tmp_path / "config"
    assert config_path() == tmp_path / "config" / "config.json"
    assert requests_dir() == tmp_path / "requests"


def test_directories_fall_back_to_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_CONFIG_DIR", raising=False)
    monkeypatch.delenv("JEV_REQUESTS_DIR", raising=False)
    assert "jev-preview" in str(config_dir())
    assert requests_dir().name == "requests"
