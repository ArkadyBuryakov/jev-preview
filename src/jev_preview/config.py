"""User configuration: where it lives, what it holds, and how it is written.

The API key is the only secret here, so the file is written with owner-only
permissions and never echoed back in full.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from platformdirs import PlatformDirs

APP_NAME: Final = "jev-preview"
API_KEY_ENV: Final = "TYPESAFE_API_KEY"
BASE_URL_ENV: Final = "TYPESAFE_BASE_URL"
CONFIG_DIR_ENV: Final = "JEV_CONFIG_DIR"
REQUESTS_DIR_ENV: Final = "JEV_REQUESTS_DIR"

DEFAULT_BASE_URL: Final = "https://api.typesafe.ai/v1"
DEFAULT_MODELS: Final = ("jev-latest", "jev-preview", "jev-1.13.0")
DEFAULT_TIMEOUT: Final = 60.0

CONFIG_FILENAME: Final = "config.json"
_dirs: Final = PlatformDirs(appname=APP_NAME, appauthor=False, roaming=False)


def config_dir() -> Path:
    """The directory holding config.json — `JEV_CONFIG_DIR` wins if it is set."""
    override = os.environ.get(CONFIG_DIR_ENV)
    return Path(override).expanduser() if override else Path(_dirs.user_config_dir)


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def requests_dir() -> Path:
    """Where saved requests live — `JEV_REQUESTS_DIR` wins if it is set."""
    override = os.environ.get(REQUESTS_DIR_ENV)
    return Path(override).expanduser() if override else Path(_dirs.user_data_dir) / "requests"


def mask_key(key: str) -> str:
    """A key rendered for the screen: enough to recognise, not enough to use."""
    if not key:
        return ""
    return f"{key[:3]}…{key[-4:]}" if len(key) > 12 else "…" * 3


@dataclass(frozen=True, slots=True)
class Config:
    """The contents of config.json, with environment overrides already applied."""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    models: tuple[str, ...] = DEFAULT_MODELS
    timeout: float = DEFAULT_TIMEOUT
    path: Path = field(default_factory=config_path, compare=False)
    #: True when the key came from the environment, so we must not rewrite the file.
    key_from_env: bool = field(default=False, compare=False)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read the config file, falling back to defaults for anything missing.

        A malformed or unreadable file is not fatal: the app still starts, and
        the onboarding prompt asks for a key as it would on a fresh install.
        """
        path = path or config_path()
        data: dict[str, Any] = {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            data = loaded

        env_key = os.environ.get(API_KEY_ENV, "").strip()
        file_key = _as_str(data.get("api_key"))
        models = tuple(m for m in _as_list(data.get("models")) if m) or DEFAULT_MODELS

        return cls(
            api_key=env_key or file_key,
            base_url=(
                os.environ.get(BASE_URL_ENV, "").strip()
                or _as_str(data.get("base_url"))
                or DEFAULT_BASE_URL
            ).rstrip("/"),
            models=models,
            timeout=_as_float(data.get("timeout"), DEFAULT_TIMEOUT),
            path=path,
            key_from_env=bool(env_key),
        )

    def to_dict(self) -> dict[str, Any]:
        """What gets written — a key supplied by the environment stays out of the file."""
        data: dict[str, Any] = {}
        if self.api_key and not self.key_from_env:
            data["api_key"] = self.api_key
        if self.base_url != DEFAULT_BASE_URL:
            data["base_url"] = self.base_url
        if tuple(self.models) != DEFAULT_MODELS:
            data["models"] = list(self.models)
        if self.timeout != DEFAULT_TIMEOUT:
            data["timeout"] = self.timeout
        return data

    def save(self) -> Path:
        """Write config.json atomically, readable only by its owner."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
        return self.path

    def with_api_key(self, api_key: str) -> Config:
        """A copy carrying a key the user just typed — which is ours to persist."""
        return replace(self, api_key=api_key.strip(), key_from_env=False)


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_list(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _as_float(value: Any, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) and value > 0 else fallback
