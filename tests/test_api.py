"""The HTTP client: what goes out, and how every outcome is recorded."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from jev_preview.api import JevClient, MissingAPIKey
from jev_preview.config import Config

URL = "https://api.example.test/v1/systemone"


@pytest.fixture
def client() -> JevClient:
    return JevClient(api_key="sk-test", base_url="https://api.example.test/v1", timeout=5.0)


def test_from_config_carries_everything(config: Config) -> None:
    client = JevClient.from_config(config)
    assert client.api_key == config.api_key
    assert client.base_url == config.base_url
    assert client.timeout == config.timeout


def test_url_tolerates_a_trailing_slash() -> None:
    assert JevClient("k", "https://x.test/v1/").url == "https://x.test/v1/systemone"


def test_headers_carry_the_bearer_token(client: JevClient) -> None:
    assert client.headers()["Authorization"] == "Bearer sk-test"


def test_headers_without_a_key_refuse() -> None:
    with pytest.raises(MissingAPIKey):
        JevClient(api_key="").headers()


@respx.mock
def test_success_records_status_body_and_duration(client: JevClient, answer_body: dict) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer_body))
    attempt = client.evaluate({"model": "jev-latest", "state": "hi", "questions": {}})

    assert attempt.ok
    assert attempt.status == 200
    assert attempt.body == answer_body
    assert attempt.error is None
    assert attempt.duration_ms >= 0
    assert attempt.timestamp.endswith("+00:00")

    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test"
    assert request.headers["content-type"] == "application/json"


@respx.mock
def test_body_is_posted_verbatim(client: JevClient) -> None:
    import json

    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    body: dict[str, Any] = {"model": "m", "state": {"chat": [1]}, "questions": {"q": {}}}
    client.evaluate(body)
    assert json.loads(route.calls.last.request.content) == body


@respx.mock
def test_http_error_is_recorded_not_raised(client: JevClient) -> None:
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    attempt = client.evaluate({})
    assert not attempt.ok
    assert attempt.status == 401
    assert attempt.body == {"error": "bad key"}


@respx.mock
def test_non_json_response_is_kept_as_text(client: JevClient) -> None:
    respx.post(URL).mock(return_value=httpx.Response(502, text="<html>gateway</html>"))
    attempt = client.evaluate({})
    assert attempt.body == "<html>gateway</html>"


@respx.mock
def test_transport_failure_becomes_an_error_attempt(client: JevClient) -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    attempt = client.evaluate({})
    assert attempt.status is None
    assert attempt.error is not None
    assert "ConnectError" in attempt.error
    assert not attempt.ok


@respx.mock
def test_timeout_becomes_an_error_attempt(client: JevClient) -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("too slow"))
    attempt = client.evaluate({})
    assert attempt.error is not None
    assert attempt.error.startswith("ReadTimeout")


def test_missing_key_never_reaches_the_network() -> None:
    with respx.mock:
        route = respx.post(URL)
        attempt = JevClient(api_key="", base_url="https://api.example.test/v1").evaluate({})
        assert not route.called
    assert attempt.status is None
    assert attempt.error is not None
    assert "MissingAPIKey" in attempt.error
