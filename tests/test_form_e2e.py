"""End-to-end coverage of the structured questions form itself.

The form is where most of the app's interaction lives: switching types rebuilds
the criteria widgets, rows can be added and removed, and nothing typed under one
type may be lost by visiting another.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from textual.widgets import Button, Input, Select, TextArea

from jev_preview.app import JevApp
from jev_preview.config import Config
from jev_preview.qwidgets import LevelRow, OptionRow, QuestionCard, QuestionsPane
from jev_preview.store import SavedRequest

SIZE = (120, 48)


@pytest_asyncio.fixture
async def app(config: Config, requests_path: Path) -> AsyncIterator[JevApp]:
    yield JevApp(config=config, directory=requests_path)


async def settle(pilot: Any, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


async def open_form(app: JevApp, pilot: Any) -> QuestionsPane:
    """Focus the questions pane and open the editor on it."""
    await pilot.press("j", "e")
    await settle(pilot)
    return app.query_one("#questions", QuestionsPane)


def card(app: JevApp) -> QuestionCard:
    return app.query(QuestionCard).first()


async def press_button(app: JevApp, pilot: Any, css_class: str) -> None:
    app.query(f".{css_class}").first(Button).press()
    await settle(pilot)


class TestTypeSwitching:
    async def test_noul_shows_true_and_false_fields(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            assert app.query(".true-desc") and app.query(".false-desc")

    async def test_switching_to_choice_builds_two_option_rows(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qtype").first(Select).value = "choice"
            await settle(pilot)

            assert len(app.query(OptionRow)) == 2
            assert card(app).model.type == "choice"

    async def test_switching_to_score_builds_two_numbered_levels(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qtype").first(Select).value = "score"
            await settle(pilot)

            rows = list(app.query(LevelRow))
            assert [row.index for row in rows] == [0, 1]

    async def test_a_round_trip_through_another_type_loses_nothing(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qtype").first(Select).value = "choice"
            await settle(pilot)
            options = list(app.query(OptionRow))
            options[0].query_one(".opt-key", Input).value = "billing"
            options[1].query_one(".opt-key", Input).value = "sales"
            await settle(pilot)

            app.query(".qtype").first(Select).value = "score"
            await settle(pilot)
            app.query(".lvl-desc").first(Input).value = "Calm"
            await settle(pilot)

            app.query(".qtype").first(Select).value = "choice"
            await settle(pilot)

            keys = [row.query_one(".opt-key", Input).value for row in app.query(OptionRow)]
            assert keys == ["billing", "sales"]
            assert card(app).model.levels[0] == "Calm"


class TestRows:
    async def test_add_and_remove_an_option(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qtype").first(Select).value = "choice"
            await settle(pilot)

            await press_button(app, pilot, "add-opt")
            assert len(app.query(OptionRow)) == 3

            app.query(OptionRow).first().query_one(".row-del", Button).press()
            await settle(pilot)
            assert len(app.query(OptionRow)) == 2

    async def test_add_and_remove_a_level_renumbers(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qtype").first(Select).value = "score"
            await settle(pilot)

            await press_button(app, pilot, "add-lvl")
            assert [row.index for row in app.query(LevelRow)] == [0, 1, 2]

            app.query(LevelRow).first().query_one(".row-del", Button).press()
            await settle(pilot)
            assert [row.index for row in app.query(LevelRow)] == [0, 1]

    async def test_a_filled_choice_reaches_the_request_body(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qid").first(Input).value = "department"
            app.query(".qinstr").first(TextArea).text = "Which team?"
            app.query(".qtype").first(Select).value = "choice"
            await settle(pilot)

            rows = list(app.query(OptionRow))
            rows[0].query_one(".opt-key", Input).value = "billing"
            rows[0].query_one(".opt-desc", Input).value = "Payments"
            rows[1].query_one(".opt-key", Input).value = "sales"
            await settle(pilot)

            assert app.req.questions == {
                "department": {
                    "type": "choice",
                    "instructions": "Which team?",
                    "criteria": {"billing": "Payments", "sales": None},
                }
            }
            assert app.question_errors == []

    async def test_a_filled_score_reaches_the_request_body(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qid").first(Input).value = "frustration"
            app.query(".qinstr").first(TextArea).text = "How annoyed?"
            app.query(".qtype").first(Select).value = "score"
            await settle(pilot)

            rows = list(app.query(LevelRow))
            rows[0].query_one(".lvl-desc", Input).value = "Calm"
            rows[1].query_one(".lvl-desc", Input).value = "Angry"
            await settle(pilot)

            assert app.req.questions["frustration"]["criteria"] == ["Calm", "Angry"]


class TestCards:
    async def test_add_question_appends_a_card(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qid").first(Input).value = "first"
            await settle(pilot)

            await press_button(app, pilot, "add-q")
            assert len(app.query(QuestionCard)) == 2
            assert [q.qid for q in app.qmodels] == ["first", "question_2"]

    async def test_remove_deletes_a_card(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            app.query(".qid").first(Input).value = "first"
            await settle(pilot)
            await press_button(app, pilot, "add-q")

            app.query(QuestionCard).first().query_one(".qdel", Button).press()
            await settle(pilot)
            assert [q.qid for q in app.qmodels] == ["question_2"]

    async def test_an_advanced_card_is_read_only(self, config: Config, requests_path: Path) -> None:
        advanced = {"type": "noul", "instructions": {"system": "structured"}}
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(body={"model": "m", "state": "x", "questions": {"adv": advanced}})
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await open_form(app, pilot)

            assert app.query(".advanced")
            assert not app.query(".qtype")
            assert not app.query(".qinstr")


class TestFieldNavigation:
    async def test_tab_cycles_inside_the_form(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            first = app.focused

            await pilot.press("tab")
            await settle(pilot)
            assert app.focused is not first

            pane = app.focused_pane()
            assert pane is not None and pane.id == "questions"

    async def test_shift_tab_goes_back(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            first = app.focused
            await pilot.press("tab")
            await settle(pilot)
            await pilot.press("shift+tab")
            await settle(pilot)
            assert app.focused is first

    async def test_arrows_on_the_type_select_move_between_fields(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await open_form(app, pilot)
            select = app.query(".qtype").first(Select)
            select.focus()
            await settle(pilot)

            await pilot.press("down")
            await settle(pilot)
            assert app.focused is not select
            assert not select.expanded  # the overlay must stay shut


class TestDraftQuestions:
    async def test_the_form_refuses_to_open_over_unparsed_json(
        self, config: Config, requests_path: Path
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(body={"state": "x"}, draft_questions="{ broken")
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("j", "e")
            await settle(pilot)

            assert not app.query_one("#questions", QuestionsPane).editing

    async def test_the_pane_title_says_so(self, config: Config, requests_path: Path) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(body={"state": "x"}, draft_questions="{ broken")
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            assert "invalid JSON" in str(app.query_one("#questions", QuestionsPane).border_title)
