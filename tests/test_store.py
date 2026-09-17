"""Saved requests: naming, file moves, history, and hostile input."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_preview.store import (
    Attempt,
    SavedRequest,
    StoreError,
    list_requests,
    new_body,
    now_iso,
    slugify,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Help! My payouts failed", "help-my-payouts-failed"),
        ("  ...  ", "untitled"),
        ("", "untitled"),
        ("ÜBER", "ber"),
        ("a" * 80, "a" * 48),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_now_iso_is_utc_to_the_second() -> None:
    stamp = now_iso()
    assert stamp.endswith("+00:00")
    assert stamp.count(":") == 3


class TestAttempt:
    def test_ok_covers_2xx_only(self) -> None:
        assert Attempt("t", 200, 1).ok
        assert Attempt("t", 299, 1).ok
        assert not Attempt("t", 300, 1).ok
        assert not Attempt("t", 401, 1).ok
        assert not Attempt("t", None, 1, error="boom").ok

    def test_round_trip(self) -> None:
        attempt = Attempt("t", 200, 12, body={"a": 1})
        assert Attempt.from_dict(attempt.to_dict()) == attempt

    def test_error_only_present_when_set(self) -> None:
        assert "error" not in Attempt("t", 200, 1).to_dict()
        assert Attempt("t", None, 1, error="boom").to_dict()["error"] == "boom"

    def test_from_dict_tolerates_junk(self) -> None:
        attempt = Attempt.from_dict({"status": "nope", "duration_ms": None})
        assert attempt.status is None
        assert attempt.duration_ms == 0


class TestAccessors:
    def test_defaults(self) -> None:
        request = SavedRequest()
        assert request.model == "jev-latest"
        assert request.state == ""
        assert request.questions == {}
        assert request.is_empty

    def test_setters_write_through_to_body(self) -> None:
        request = SavedRequest()
        request.state = {"chat": []}
        request.questions = {"q": {"type": "noul"}}
        request.model = "jev-preview"
        assert request.body == {
            "model": "jev-preview",
            "state": {"chat": []},
            "questions": {"q": {"type": "noul"}},
        }
        assert not request.is_empty

    def test_corrupt_body_fields_fall_back(self) -> None:
        request = SavedRequest(body={"model": 7, "questions": "nope"})
        assert request.model == "jev-latest"
        assert request.questions == {}

    def test_questions_text_prefers_the_draft(self) -> None:
        request = SavedRequest()
        assert request.questions_text() == ""
        request.questions = {"q": {"type": "noul"}}
        assert json.loads(request.questions_text()) == {"q": {"type": "noul"}}
        request.draft_questions = "{ broken"
        assert request.questions_text() == "{ broken"


class TestDefaultName:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("Help me\nplease", "Help me"),
            ("\n\n  spaced  \n", "spaced"),
            ("", "untitled"),
            ("trailing---", "trailing"),
        ],
    )
    def test_from_text(self, state: str, expected: str) -> None:
        assert SavedRequest(body={"state": state}).default_name() == expected

    def test_from_structure(self) -> None:
        name = SavedRequest(body={"state": {"chat": ["hi"]}}).default_name()
        assert name.startswith('{"chat"')


class TestSave:
    def test_writes_a_slugged_file(self, requests_path: Path) -> None:
        request = SavedRequest(body={"state": "Hello there"})
        path = request.save(requests_path)
        assert path == requests_path / "hello-there.json"
        assert json.loads(path.read_text())["name"] == "Hello there"

    def test_creates_the_directory(self, tmp_path: Path) -> None:
        directory = tmp_path / "deep" / "nested"
        SavedRequest(body={"state": "x"}).save(directory)
        assert (directory / "x.json").exists()

    def test_auto_name_follows_the_context_and_moves_the_file(self, requests_path: Path) -> None:
        request = SavedRequest(body={"state": "First title"})
        first = request.save(requests_path)
        request.state = "Second title"
        second = request.save(requests_path)

        assert second == requests_path / "second-title.json"
        assert not first.exists()
        assert request.name == "Second title"

    def test_rename_pins_the_name(self, requests_path: Path) -> None:
        request = SavedRequest(body={"state": "First title"})
        request.save(requests_path)
        request.rename("My ticket", requests_path)
        request.state = "Something else entirely"
        path = request.save(requests_path)

        assert request.auto_name is False
        assert request.name == "My ticket"
        assert path == requests_path / "my-ticket.json"

    def test_rename_to_blank_is_untitled(self, requests_path: Path) -> None:
        request = SavedRequest(body={"state": "x"})
        request.rename("   ", requests_path)
        assert request.name == "untitled"

    def test_colliding_names_get_suffixes(self, requests_path: Path) -> None:
        paths = [SavedRequest(body={"state": "Same title"}).save(requests_path) for _ in range(3)]
        assert [p.name for p in paths] == [
            "same-title.json",
            "same-title-2.json",
            "same-title-3.json",
        ]

    def test_resaving_keeps_its_suffixed_path(self, requests_path: Path) -> None:
        SavedRequest(body={"state": "Same title"}).save(requests_path)
        second = SavedRequest(body={"state": "Same title"})
        first_path = second.save(requests_path)
        again = second.save(requests_path)
        assert first_path == again == requests_path / "same-title-2.json"

    def test_unwritable_directory_raises_store_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x")
        with pytest.raises(StoreError, match="cannot save"):
            SavedRequest(body={"state": "x"}).save(blocker)

    def test_auto_name_flag_only_serialised_while_true(self, requests_path: Path) -> None:
        request = SavedRequest(body={"state": "x"})
        assert request.to_dict()["auto_name"] is True
        request.rename("Pinned", requests_path)
        assert "auto_name" not in request.to_dict()


class TestLoad:
    def test_round_trip(self, requests_path: Path) -> None:
        original = SavedRequest(
            name="Ticket",
            auto_name=False,
            body=new_body("jev-preview"),
            responses=[Attempt("t", 200, 5, body={"answers": {}})],
        )
        path = original.save(requests_path)
        loaded = SavedRequest.load(path)

        assert loaded.name == "Ticket"
        assert loaded.auto_name is False
        assert loaded.model == "jev-preview"
        assert loaded.responses == original.responses
        assert loaded.path == path

    def test_missing_file(self, requests_path: Path) -> None:
        with pytest.raises(StoreError, match="cannot read"):
            SavedRequest.load(requests_path / "nope.json")

    def test_invalid_json(self, requests_path: Path) -> None:
        path = requests_path / "bad.json"
        path.write_text("{ oops")
        with pytest.raises(StoreError, match="not valid JSON"):
            SavedRequest.load(path)

    def test_not_an_object(self, requests_path: Path) -> None:
        path = requests_path / "list.json"
        path.write_text("[1, 2]")
        with pytest.raises(StoreError, match="not a request document"):
            SavedRequest.load(path)

    def test_partial_document_gets_defaults(self, write_request) -> None:
        path = write_request("partial.json", {"name": "Only a name"})
        loaded = SavedRequest.load(path)
        assert loaded.body == new_body()
        assert loaded.responses == []

    def test_junk_responses_are_skipped(self, write_request) -> None:
        path = write_request("junk.json", {"responses": ["nope", {"status": 200}]})
        assert len(SavedRequest.load(path).responses) == 1

    def test_nameless_file_falls_back_to_its_stem(self, write_request) -> None:
        assert SavedRequest.load(write_request("stem-name.json", {})).name == "stem-name"


class TestDelete:
    def test_removes_the_file(self, saved_request: SavedRequest) -> None:
        path = saved_request.path
        assert path is not None
        saved_request.delete()
        assert not path.exists()
        assert saved_request.path is None

    def test_unsaved_request_is_a_no_op(self) -> None:
        SavedRequest().delete()


class TestListRequests:
    def test_newest_first(self, requests_path: Path) -> None:
        import os
        import time

        for index, title in enumerate(["Older", "Newer"]):
            path = SavedRequest(body={"state": title}).save(requests_path)
            os.utime(path, (time.time() + index, time.time() + index))
        assert [p.stem for p in list_requests(requests_path)] == ["newer", "older"]

    def test_missing_directory_is_empty(self, tmp_path: Path) -> None:
        assert list_requests(tmp_path / "nope") == []

    def test_ignores_other_files(self, requests_path: Path) -> None:
        (requests_path / "notes.txt").write_text("x")
        SavedRequest(body={"state": "Kept"}).save(requests_path)
        assert [p.name for p in list_requests(requests_path)] == ["kept.json"]
