"""End-to-end: drive the real TUI through Textual's Pilot.

These cover what unit tests cannot — that keys are bound where the README says,
that edit mode is genuinely modal, and that a send round-trips into a saved file.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx

from jev_preview.app import EditPane, JevApp, ResponsePane
from jev_preview.config import Config
from jev_preview.qwidgets import QuestionsPane
from jev_preview.screens import ApiKeyScreen, CatalogScreen, ConfirmScreen, HelpScreen, PromptScreen
from jev_preview.store import SavedRequest

URL = "https://api.example.test/v1/systemone"
SIZE = (120, 40)


@pytest_asyncio.fixture
async def app(config: Config, requests_path: Path) -> AsyncIterator[JevApp]:
    """A configured app; the key is already set, so no onboarding screen appears."""
    yield JevApp(config=config, directory=requests_path)


async def settle(pilot: Any, times: int = 2) -> None:
    """Let workers, timers and mounts finish before asserting."""
    for _ in range(times):
        await pilot.pause()


def widget_text(widget: Any) -> str:
    """What a widget actually paints, as plain text."""
    from textual.geometry import Region

    width = max(widget.size.width, widget.virtual_size.width)
    height = max(widget.size.height, widget.virtual_size.height)
    return "\n".join(strip.text for strip in widget.render_lines(Region(0, 0, width, height)))


def status_text(app: JevApp) -> str:
    from textual.widgets import Static

    return widget_text(app.query_one("#statusbar", Static))


def response_text(app: JevApp) -> str:
    return widget_text(app.query_one("#response", ResponsePane).body)


class TestLayout:
    async def test_three_panes_and_initial_focus(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            assert isinstance(app.focused, EditPane)
            assert app.focused is not None and app.focused.id == "context"
            assert app.query_one("#questions", QuestionsPane)
            assert app.query_one("#response", ResponsePane)

    async def test_status_bar_shows_name_model_and_file(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            assert "new request" in status_text(app)
            assert "jev-latest" in status_text(app)
            assert "unsaved" in status_text(app)

    async def test_response_pane_starts_empty(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            assert "no responses yet" in response_text(app)


class TestNavigation:
    @pytest.mark.parametrize(
        ("keys", "expected"),
        [
            (["j"], "questions"),
            (["l"], "response"),
            (["j", "l"], "response"),
            (["j", "k"], "context"),
            (["l", "h"], "context"),
            (["down"], "questions"),
            (["right"], "response"),
        ],
    )
    async def test_directional_moves(self, app: JevApp, keys: list[str], expected: str) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            for key in keys:
                await pilot.press(key)
            await settle(pilot)
            pane = app.focused_pane()
            assert pane is not None and pane.id == expected

    async def test_moves_with_no_target_are_ignored(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("h")  # nothing to the left of context
            await settle(pilot)
            pane = app.focused_pane()
            assert pane is not None and pane.id == "context"


class TestContextEditing:
    async def test_e_enters_edit_mode_and_typing_reaches_the_model(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e")
            await settle(pilot)
            pane = app.query_one("#context", EditPane)
            assert pane.editing
            assert app.is_editing()

            await pilot.press(*"hello")
            await settle(pilot)
            assert app.req.state == "hello"

    async def test_navigation_keys_type_while_editing(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"jkl")
            await settle(pilot)
            assert app.req.state == "jkl"
            assert app.focused_pane() is not None
            assert app.focused_pane().id == "context"  # type: ignore[union-attr]

    async def test_escape_leaves_edit_mode_and_saves(
        self, app: JevApp, requests_path: Path
    ) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"Hi there", "escape")
            await settle(pilot, 4)
            assert not app.query_one("#context", EditPane).editing
            assert (requests_path / "hi-there.json").exists()

    async def test_json_context_is_parsed_as_structure(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e")
            app.query_one("#context", EditPane).text = '{"chat": []}'
            await settle(pilot)
            assert app.req.state == {"chat": []}

    async def test_name_follows_the_context_until_renamed(
        self, app: JevApp, requests_path: Path
    ) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e")
            app.query_one("#context", EditPane).text = "First title"
            await pilot.press("escape")
            await settle(pilot, 4)
            assert app.req.name == "First title"

            app.query_one("#context", EditPane).text = "Second title"
            await settle(pilot, 4)
            app.persist()
            assert app.req.name == "Second title"
            assert not (requests_path / "first-title.json").exists()


class TestQuestionsForm:
    async def test_e_on_an_empty_pane_starts_a_question(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j", "e")
            await settle(pilot, 3)
            pane = app.query_one("#questions", QuestionsPane)
            assert pane.editing
            assert len(app.qmodels) == 1
            assert app.qmodels[0].qid == "question_1"

    async def test_an_untouched_question_is_discarded_on_escape(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j", "e")
            await settle(pilot, 3)
            await pilot.press("escape")
            await settle(pilot, 3)
            assert app.qmodels == []
            assert app.req.questions == {}

    async def test_typing_an_id_and_instructions_builds_the_body(self, app: JevApp) -> None:
        from textual.widgets import Input, TextArea

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j", "e")
            await settle(pilot, 3)

            app.query_one(".qid", Input).value = "is_urgent"
            app.query_one(".qinstr", TextArea).text = "Is this urgent?"
            await settle(pilot, 3)

            assert app.req.questions == {
                "is_urgent": {"type": "noul", "instructions": "Is this urgent?"}
            }
            assert app.question_errors == []

    async def test_validation_errors_reach_the_pane_title(self, app: JevApp) -> None:
        from textual.widgets import Input

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j", "e")
            await settle(pilot, 3)
            app.query_one(".qid", Input).value = "q"  # instructions still blank
            await settle(pilot, 3)
            await pilot.press("escape")
            await settle(pilot, 3)

            assert any("instructions are required" in e for e in app.question_errors)
            assert "to fix" in str(app.query_one("#questions", QuestionsPane).border_title)

    async def test_existing_questions_load_into_the_form(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        from textual.widgets import Input

        app = JevApp(config=config, directory=requests_path)
        app.req = saved_request
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("j", "e")
            await settle(pilot, 3)
            assert app.query_one(".qid", Input).value == "is_urgent"

    async def test_advanced_questions_survive_a_form_visit(
        self, config: Config, requests_path: Path
    ) -> None:
        advanced = {"type": "noul", "instructions": {"system": "structured"}}
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(
            body={"model": "jev-latest", "state": "x", "questions": {"q": advanced}}
        )
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("j", "e")
            await settle(pilot, 3)
            await pilot.press("escape")
            await settle(pilot, 3)
            assert app.req.questions == {"q": advanced}


class TestSending:
    @pytest.fixture
    def ready(self, config: Config, requests_path: Path) -> JevApp:
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(
            body={
                "model": "jev-latest",
                "state": "My payouts keep failing",
                "questions": {"is_urgent": {"type": "noul", "instructions": "Urgent?"}},
            }
        )
        return app

    @respx.mock
    async def test_enter_sends_and_records_the_response(
        self, ready: JevApp, answer_body: dict[str, Any], requests_path: Path
    ) -> None:
        route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer_body))
        async with ready.run_test(size=SIZE) as pilot:
            await settle(pilot)
            ready.load_into_panes()
            await pilot.press("enter")
            await settle(pilot, 6)

            assert route.called
            assert json.loads(route.calls.last.request.content) == ready.req.body
            assert len(ready.req.responses) == 1
            assert ready.req.responses[0].status == 200

            saved = json.loads(ready.req.path.read_text())  # type: ignore[union-attr]
            assert saved["responses"][0]["body"] == answer_body

    @respx.mock
    async def test_the_response_pane_renders_the_answers(
        self, ready: JevApp, answer_body: dict[str, Any]
    ) -> None:
        respx.post(URL).mock(return_value=httpx.Response(200, json=answer_body))
        async with ready.run_test(size=SIZE) as pilot:
            await settle(pilot)
            ready.load_into_panes()
            await pilot.press("enter")
            await settle(pilot, 6)

            pane = ready.query_one("#response", ResponsePane)
            assert "response (1/1)" in str(pane.border_title)
            assert isinstance(ready.focused, ResponsePane)

    @respx.mock
    async def test_history_keys_walk_the_responses(
        self, ready: JevApp, answer_body: dict[str, Any]
    ) -> None:
        respx.post(URL).mock(return_value=httpx.Response(200, json=answer_body))
        async with ready.run_test(size=SIZE) as pilot:
            await settle(pilot)
            ready.load_into_panes()
            await pilot.press("enter")
            await settle(pilot, 6)
            await pilot.press("h", "enter")
            await settle(pilot, 6)

            assert len(ready.req.responses) == 2
            assert ready.history_index == 1
            await pilot.press("[")
            await settle(pilot)
            assert ready.history_index == 0
            await pilot.press("]")
            await settle(pilot)
            assert ready.history_index == 1
            await pilot.press("]")  # already at the end
            await settle(pilot)
            assert ready.history_index == 1

    @respx.mock
    async def test_transport_errors_are_recorded(self, ready: JevApp) -> None:
        respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
        async with ready.run_test(size=SIZE) as pilot:
            await settle(pilot)
            ready.load_into_panes()
            await pilot.press("enter")
            await settle(pilot, 6)

            assert len(ready.req.responses) == 1
            assert ready.req.responses[0].error is not None

    async def test_send_refuses_without_questions(self, app: JevApp) -> None:
        with respx.mock:
            route = respx.post(URL)
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                await pilot.press("enter")
                await settle(pilot, 3)
            assert not route.called

    async def test_send_refuses_while_questions_are_invalid(
        self, config: Config, requests_path: Path
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(
            body={"model": "m", "state": "x", "questions": {"q": {"type": "noul"}}}
        )
        with respx.mock:
            route = respx.post(URL)
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                app.load_into_panes()
                await pilot.press("enter")
                await settle(pilot, 3)
            assert not route.called
            assert app.question_errors

    async def test_send_refuses_without_an_api_key(self, requests_path: Path) -> None:
        app = JevApp(
            config=Config(api_key="", path=requests_path / "c.json"), directory=requests_path
        )
        app.req = SavedRequest(
            body={
                "model": "m",
                "state": "x",
                "questions": {"q": {"type": "noul", "instructions": "i"}},
            }
        )
        with respx.mock:
            route = respx.post(URL)
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot, 3)
                app.screen.dismiss(None) if isinstance(app.screen, ApiKeyScreen) else None
                await settle(pilot, 3)
            assert not route.called


class TestRequestManagement:
    async def test_n_starts_a_blank_request(self, app: JevApp, requests_path: Path) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"Something", "escape")
            await settle(pilot, 4)
            first = app.req.path

            await pilot.press("n")
            await settle(pilot, 3)
            assert app.req.path is None
            assert app.req.state == ""
            assert app.req.is_empty
            assert first is not None and first.exists()

    async def test_c_copies_without_the_response_history(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = saved_request
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("c")
            await settle(pilot, 3)

            assert app.req.name == "Copy of A saved ticket"
            assert app.req.auto_name is False
            assert app.req.responses == []
            assert app.req.body["state"] == "My payouts keep failing"
            assert (requests_path / "copy-of-a-saved-ticket.json").exists()

    async def test_c_on_an_empty_request_does_nothing(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("c")
            await settle(pilot, 3)
            assert app.req.name == ""

    async def test_m_cycles_the_models(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            models = app.config.models
            for expected in [*models[1:], models[0]]:
                await pilot.press("m")
                await settle(pilot)
                assert app.req.model == expected
            assert app.req.model in status_text(app)

    async def test_r_renames_through_the_prompt(self, app: JevApp, requests_path: Path) -> None:
        from textual.widgets import Input

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"Original", "escape")
            await settle(pilot, 4)

            await pilot.press("r")
            await settle(pilot, 3)
            assert isinstance(app.screen, PromptScreen)
            app.screen.query_one("#prompt-input", Input).value = "Pinned name"
            await pilot.press("enter")
            await settle(pilot, 4)

            assert app.req.name == "Pinned name"
            assert app.req.auto_name is False
            assert (requests_path / "pinned-name.json").exists()

    async def test_rename_cancelled_changes_nothing(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"Original", "escape")
            await settle(pilot, 4)
            await pilot.press("r", "escape")
            await settle(pilot, 3)
            assert app.req.name == "Original"
            assert app.req.auto_name is True

    async def test_d_deletes_after_confirmation(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = saved_request
        path = saved_request.path
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("d")
            await settle(pilot, 3)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await settle(pilot, 4)

            assert path is not None and not path.exists()
            assert app.req.is_empty

    async def test_d_cancelled_keeps_the_file(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = saved_request
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("d", "n")
            await settle(pilot, 4)
            assert saved_request.path is not None and saved_request.path.exists()
            assert app.req is saved_request


class TestCatalog:
    async def test_o_lists_and_opens_a_saved_request(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot, 3)
            assert isinstance(app.screen, CatalogScreen)

            await pilot.press("enter")
            await settle(pilot, 4)
            assert app.req.name == "A saved ticket"
            assert app.req.path == saved_request.path
            assert len(app.req.responses) == 1

    async def test_filtering_narrows_the_list(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        from textual.widgets import Input, OptionList

        SavedRequest(name="Another one", auto_name=False, body={"state": "x"}).save(requests_path)
        app = JevApp(config=config, directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot, 3)
            assert app.screen.query_one(OptionList).option_count == 2

            app.screen.query_one("#catalog-filter", Input).value = "Another"
            await settle(pilot, 3)
            assert app.screen.query_one(OptionList).option_count == 1

            await pilot.press("enter")
            await settle(pilot, 4)
            assert app.req.name == "Another one"

    async def test_escape_leaves_the_current_request_alone(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o", "escape")
            await settle(pilot, 3)
            assert app.req.name == ""

    async def test_unreadable_files_are_listed_not_fatal(
        self, config: Config, requests_path: Path
    ) -> None:
        from textual.widgets import OptionList

        (requests_path / "broken.json").write_text("{ not json")
        app = JevApp(config=config, directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot, 3)
            assert app.screen.query_one(OptionList).option_count == 1

    async def test_empty_catalogue_says_so(self, app: JevApp) -> None:
        from textual.widgets import OptionList

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot, 3)
            option = app.screen.query_one(OptionList).get_option_at_index(0)
            assert "no saved requests yet" in str(option.prompt)


class TestApiKeyOnboarding:
    async def test_prompt_appears_when_no_key_is_configured(self, requests_path: Path) -> None:
        config = Config(api_key="", path=requests_path / "config.json")
        app = JevApp(config=config, directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot, 4)
            assert isinstance(app.screen, ApiKeyScreen)

    async def test_entering_a_key_stores_it(self, requests_path: Path) -> None:
        from textual.widgets import Input

        path = requests_path / "config.json"
        app = JevApp(config=Config(api_key="", path=path), directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot, 4)
            app.screen.query_one("#apikey-input", Input).value = "sk-typed-in-the-tui"
            await pilot.press("enter")
            await settle(pilot, 4)

            assert app.config.api_key == "sk-typed-in-the-tui"
            assert json.loads(path.read_text())["api_key"] == "sk-typed-in-the-tui"
            assert not isinstance(app.screen, ApiKeyScreen)

    async def test_declining_exits_the_app(self, requests_path: Path) -> None:
        path = requests_path / "config.json"
        app = JevApp(config=Config(api_key="", path=path), directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot, 4)
            await pilot.press("escape")
            await settle(pilot, 6)
        assert not path.exists()
        assert app.return_code == 0

    async def test_a_blank_key_is_refused(self, requests_path: Path) -> None:
        app = JevApp(
            config=Config(api_key="", path=requests_path / "config.json"), directory=requests_path
        )
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot, 4)
            await pilot.press("enter")  # empty input
            await settle(pilot, 3)
            assert isinstance(app.screen, ApiKeyScreen)

    async def test_ctrl_k_changes_an_existing_key(self, app: JevApp, requests_path: Path) -> None:
        from textual.widgets import Input

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+k")
            await settle(pilot, 3)
            assert isinstance(app.screen, ApiKeyScreen)

            app.screen.query_one("#apikey-input", Input).value = "sk-replacement-key"
            await pilot.press("enter")
            await settle(pilot, 4)
            assert app.config.api_key == "sk-replacement-key"

    async def test_status_bar_warns_when_no_key_is_set(self, requests_path: Path) -> None:
        app = JevApp(
            config=Config(api_key="", path=requests_path / "config.json"), directory=requests_path
        )
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot, 4)
            assert "no API key" in status_text(app)


class TestHelpAndQuit:
    async def test_question_mark_opens_help(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("question_mark")
            await settle(pilot, 3)
            assert isinstance(app.screen, HelpScreen)

            await pilot.press("escape")
            await settle(pilot, 3)
            assert not isinstance(app.screen, HelpScreen)

    async def test_q_saves_and_quits(self, app: JevApp, requests_path: Path) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"Quitting now", "escape")
            await settle(pilot, 4)
            await pilot.press("q")
            await settle(pilot, 3)
        assert (requests_path / "quitting-now.json").exists()

    async def test_ctrl_q_quits_from_inside_edit_mode(
        self, app: JevApp, requests_path: Path
    ) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"Mid edit")
            await settle(pilot, 3)
            assert app.is_editing()
            await pilot.press("ctrl+q")
            await settle(pilot, 3)
        assert (requests_path / "mid-edit.json").exists()


class TestModality:
    """Edit mode must own the keyboard; normal mode must own the commands."""

    @staticmethod
    def actions(app: JevApp) -> set[str]:
        return {
            binding.action
            for _, binding, enabled, _ in app.screen.active_bindings.values()
            if enabled
        }

    async def test_normal_mode_offers_the_commands(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            actions = self.actions(app)
            assert {"send", "catalog", "new_request", "save_quit", "help"} <= actions
            assert "leave_edit" not in actions

    async def test_edit_mode_hides_them(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e")
            await settle(pilot)

            actions = self.actions(app)
            assert "leave_edit" in actions
            assert not {"send", "catalog", "new_request", "save_quit", "help"} & actions
            assert "force_quit" in actions  # ctrl+q must still work mid-edit

    async def test_the_pane_title_marks_edit_mode(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            pane = app.query_one("#context", EditPane)
            assert "✎" not in str(pane.border_title)

            await pilot.press("e")
            await settle(pilot)
            assert "✎ esc" in str(pane.border_title)

            await pilot.press("escape")
            await settle(pilot, 4)
            assert "✎" not in str(pane.border_title)
