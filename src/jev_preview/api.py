"""HTTP client for the TypeSafe System One endpoint.

Deliberately raw: the sandbox saves exactly what went over the wire, so the
request body is posted as-is rather than through a typed SDK, and every
outcome — including transport failures — comes back as an `Attempt`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from jev_preview.config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, Config
from jev_preview.store import Attempt, now_iso

ENDPOINT = "/systemone"


class MissingAPIKey(RuntimeError):
    """No API key was available when a request was attempted."""


@dataclass(frozen=True, slots=True)
class JevClient:
    """Posts request bodies to System One and records what came back."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_config(cls, config: Config) -> JevClient:
        return cls(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout)

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}{ENDPOINT}"

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MissingAPIKey("no API key configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def evaluate(self, body: dict[str, Any]) -> Attempt:
        """POST the body and record the outcome, errors included."""
        started = time.monotonic()
        try:
            response = httpx.post(self.url, json=body, headers=self.headers(), timeout=self.timeout)
        except (httpx.HTTPError, MissingAPIKey) as exc:
            return Attempt(
                timestamp=now_iso(),
                status=None,
                duration_ms=_elapsed_ms(started),
                error=_describe(exc),
            )

        elapsed = _elapsed_ms(started)
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        return Attempt(
            timestamp=now_iso(),
            status=response.status_code,
            duration_ms=elapsed,
            body=payload,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _describe(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
