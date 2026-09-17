"""The questions model: what the form can represent, and what it carries through."""

from __future__ import annotations

import pytest

from jev_preview import questions
from jev_preview.questions import Question


class TestLoad:
    def test_noul_without_criteria(self) -> None:
        [q] = questions.load({"urgent": {"type": "noul", "instructions": "Urgent?"}})
        assert (q.qid, q.type, q.instructions) == ("urgent", "noul", "Urgent?")
        assert not q.is_advanced

    def test_noul_criteria(self) -> None:
        [q] = questions.load(
            {"u": {"type": "noul", "instructions": "i", "criteria": {"true": "yes", "false": "no"}}}
        )
        assert (q.true_desc, q.false_desc) == ("yes", "no")

    def test_choice_criteria(self) -> None:
        [q] = questions.load(
            {
                "dept": {
                    "type": "choice",
                    "instructions": "which",
                    "criteria": {"billing": "Payments", "sales": None},
                }
            }
        )
        assert q.options == [["billing", "Payments"], ["sales", ""]]

    def test_score_criteria(self) -> None:
        [q] = questions.load(
            {"f": {"type": "score", "instructions": "i", "criteria": ["Calm", "Angry"]}}
        )
        assert q.levels == ["Calm", "Angry"]

    def test_missing_instructions_is_still_editable(self) -> None:
        [q] = questions.load({"u": {"type": "noul"}})
        assert q.instructions == ""
        assert not q.is_advanced

    @pytest.mark.parametrize(
        "raw",
        [
            {"type": "mystery", "instructions": "i"},
            {"type": "noul", "instructions": {"nested": "object"}},
            {"type": "noul", "instructions": "i", "criteria": {"true": {"deep": 1}}},
            {"type": "noul", "instructions": "i", "criteria": {"other": "key"}},
            {"type": "choice", "instructions": "i", "criteria": []},
            {"type": "choice", "instructions": "i", "criteria": {"a": {"nested": 1}}},
            {"type": "score", "instructions": "i", "criteria": [1, 2, 3]},
            {"type": "score", "instructions": "i", "criteria": []},
        ],
    )
    def test_shapes_the_form_cannot_model_become_advanced(self, raw: dict) -> None:
        [q] = questions.load({"q": raw})
        assert q.is_advanced
        assert q.advanced == raw

    def test_non_object_question_becomes_advanced(self) -> None:
        [q] = questions.load({"q": "just a string"})
        assert q.advanced == {"value": "just a string"}

    def test_empty_and_none(self) -> None:
        assert questions.load({}) == []
        assert questions.load(None) == []


class TestDump:
    def test_noul_criteria_omitted_when_blank(self) -> None:
        dumped = questions.dump([Question("u", "noul", "Urgent?")])
        assert dumped == {"u": {"type": "noul", "instructions": "Urgent?"}}

    def test_noul_partial_criteria(self) -> None:
        dumped = questions.dump([Question("u", "noul", "i", true_desc="yes")])
        assert dumped["u"]["criteria"] == {"true": "yes"}

    def test_choice_blank_descriptions_become_null(self) -> None:
        question = Question("d", "choice", "i", options=[["a", "A"], ["b", ""], ["", "orphan"]])
        assert questions.dump([question])["d"]["criteria"] == {"a": "A", "b": None}

    def test_score_levels(self) -> None:
        question = Question("f", "score", "i", levels=["Calm", "Angry"])
        assert questions.dump([question])["f"]["criteria"] == ["Calm", "Angry"]

    def test_advanced_is_returned_byte_for_byte(self) -> None:
        raw = {"type": "noul", "instructions": {"system": "x"}, "extra": [1, 2]}
        dumped = questions.dump(questions.load({"q": raw}))
        assert dumped == {"q": raw}

    @pytest.mark.parametrize(
        "raw",
        [
            {"q": {"type": "noul", "instructions": "i"}},
            {"q": {"type": "noul", "instructions": "i", "criteria": {"true": "t", "false": "f"}}},
            {"q": {"type": "choice", "instructions": "i", "criteria": {"a": "A", "b": "B"}}},
            {"q": {"type": "score", "instructions": "i", "criteria": ["one", "two"]}},
        ],
    )
    def test_round_trip(self, raw: dict) -> None:
        assert questions.dump(questions.load(raw)) == raw


class TestValidate:
    def test_a_good_set_has_no_errors(self) -> None:
        models = [
            Question("a", "noul", "i"),
            Question("b", "choice", "i", options=[["x", ""], ["y", ""]]),
            Question("c", "score", "i", levels=["low", "high"]),
        ]
        assert questions.validate(models) == []

    def test_missing_id(self) -> None:
        assert "question 1: needs an id" in questions.validate([Question("", "noul", "i")])

    def test_duplicate_id(self) -> None:
        errors = questions.validate([Question("a", "noul", "i"), Question("a", "noul", "i")])
        assert any("duplicate id" in e for e in errors)

    def test_missing_instructions(self) -> None:
        assert "a: instructions are required" in questions.validate([Question("a", "noul", "  ")])

    @pytest.mark.parametrize("options", [[], [["only", ""]], [["a", ""], ["  ", ""]]])
    def test_choice_needs_two_options(self, options: list[list[str]]) -> None:
        errors = questions.validate([Question("a", "choice", "i", options=options)])
        assert any("at least two options" in e for e in errors)

    def test_duplicate_options(self) -> None:
        question = Question("a", "choice", "i", options=[["x", ""], ["x", ""]])
        assert any("duplicate option" in e for e in questions.validate([question]))

    @pytest.mark.parametrize("levels", [[], ["one"], ["one", "  "]])
    def test_score_needs_two_levels(self, levels: list[str]) -> None:
        errors = questions.validate([Question("a", "score", "i", levels=levels)])
        assert any("at least two levels" in e for e in errors)

    def test_advanced_questions_are_only_checked_for_an_id(self) -> None:
        question = Question("", advanced={"anything": True})
        assert questions.validate([question]) == ["question 1: needs an id"]


class TestScaffolding:
    def test_new_question_avoids_taken_ids(self) -> None:
        existing = [Question("question_1"), Question("other")]
        assert questions.new_question(existing).qid == "question_3"

        collision = [Question("question_1"), Question("question_2")]
        assert questions.new_question(collision).qid == "question_3"

    def test_is_untouched_only_for_generated_empty_questions(self) -> None:
        assert questions.is_untouched(Question("question_1"))
        assert not questions.is_untouched(Question("urgency"))
        assert not questions.is_untouched(Question("question_1", instructions="typed"))
        assert not questions.is_untouched(Question("question_1", options=[["a", ""]]))
        assert not questions.is_untouched(Question("question_1", levels=["a"]))
        assert not questions.is_untouched(Question("question_1", advanced={}))
