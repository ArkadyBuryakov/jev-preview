"""The rendered views: they must be readable, and must never raise on odd payloads."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console, RenderableType

from jev_preview.questions import Question
from jev_preview.render import render_attempt, render_questions
from jev_preview.store import Attempt


def text_of(renderable: RenderableType, width: int = 100) -> str:
    console = Console(width=width, record=True, force_terminal=False, legacy_windows=False)
    console.print(renderable)
    return console.export_text()


class TestRenderAttempt:
    def test_success_shows_status_answers_and_raw(self, answer_body: dict[str, Any]) -> None:
        attempt = Attempt("2026-01-01T00:00:00+00:00", 200, 979, body=answer_body)
        out = text_of(render_attempt(attempt, 0, 3))

        assert "[1/3]" in out
        assert "200" in out
        assert "979 ms" in out
        assert "jev-latest" in out
        assert "41" in out and "7" in out  # token usage
        assert "is_urgent" in out and "0.820" in out
        assert "department" in out and "billing" in out
        assert "frustration" in out and "Very angry" in out
        assert "── raw ──" in out

    def test_error_attempt_shows_the_error_and_stops(self) -> None:
        attempt = Attempt("t", None, 12, error="ConnectError: refused")
        out = text_of(render_attempt(attempt, 0, 1))
        assert "error" in out
        assert "ConnectError: refused" in out
        assert "raw" not in out

    def test_failure_status_falls_through_to_raw_json(self) -> None:
        attempt = Attempt("t", 401, 12, body={"error": "bad key"})
        out = text_of(render_attempt(attempt, 0, 1))
        assert "401" in out
        assert "bad key" in out

    def test_non_dict_body_is_still_rendered(self) -> None:
        out = text_of(render_attempt(Attempt("t", 200, 1, body="<html>"), 0, 1))
        assert "<html>" in out

    def test_confidence_is_shown_when_present(self) -> None:
        body = {"answers": {"q": {"type": "noul", "noul": 0.5, "confidence": 0.42}}}
        out = text_of(render_attempt(Attempt("t", 200, 1, body=body), 0, 1))
        assert "confidence" in out and "0.420" in out

    @pytest.mark.parametrize(
        "answer",
        [
            {"type": "noul"},  # no value at all
            {"type": "noul", "noul": "not a number"},
            {"type": "choice"},  # no probabilities
            {"type": "choice", "probabilities": {"a": None}},
            {"type": "score", "score": None, "probabilities": {}},
            {"type": "unheard-of", "whatever": [1, 2]},
            "a bare string answer",
        ],
    )
    def test_malformed_answers_never_raise(self, answer: Any) -> None:
        body = {"answers": {"q": answer}}
        assert text_of(render_attempt(Attempt("t", 200, 1, body=body), 0, 1))

    def test_probabilities_outside_zero_to_one_are_clamped(self) -> None:
        body = {"answers": {"q": {"type": "noul", "noul": 5.0}}}
        assert text_of(render_attempt(Attempt("t", 200, 1, body=body), 0, 1))

    def test_unserialisable_body_does_not_raise(self) -> None:
        out = text_of(render_attempt(Attempt("t", 200, 1, body={"when": object()}), 0, 1))
        assert "when" in out


class TestRenderQuestions:
    def test_empty(self) -> None:
        assert "no questions yet" in text_of(render_questions([], []))

    def test_draft_takes_over(self) -> None:
        out = text_of(render_questions([], [], draft="{ broken"))
        assert "does not parse" in out
        assert "{ broken" in out

    def test_noul_with_criteria(self) -> None:
        question = Question("urgent", "noul", "Is it urgent?", true_desc="yes it is")
        out = text_of(render_questions([question], []))
        assert "urgent" in out and "noul" in out
        assert "Is it urgent?" in out
        assert "yes it is" in out

    def test_choice_options(self) -> None:
        question = Question("dept", "choice", "Which?", options=[["billing", "Payments"]])
        out = text_of(render_questions([question], []))
        assert "billing" in out and "Payments" in out

    def test_score_levels_are_numbered(self) -> None:
        question = Question("f", "score", "How much?", levels=["Calm", "Angry"])
        out = text_of(render_questions([question], []))
        assert "0" in out and "Calm" in out
        assert "1" in out and "Angry" in out

    def test_missing_instructions_is_called_out(self) -> None:
        assert "(no instructions)" in text_of(render_questions([Question("q")], []))

    def test_advanced_is_shown_as_json(self) -> None:
        question = Question("q", advanced={"type": "noul", "instructions": {"system": "x"}})
        out = text_of(render_questions([question], []))
        assert "json" in out
        assert "system" in out

    def test_errors_are_listed(self) -> None:
        out = text_of(render_questions([Question("q", "noul", "i")], ["q: needs an id"]))
        assert "⚠ q: needs an id" in out

    def test_question_without_an_id(self) -> None:
        assert "(no id)" in text_of(render_questions([Question("", "noul", "i")], []))
