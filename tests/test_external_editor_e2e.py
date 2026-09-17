"""The `E` escape hatch and the catalogue's destructive corners.

`$EDITOR` is stubbed rather than launched: `App.suspend()` is not available inside
a headless test, so `_run_editor` is replaced with the text the editor would have
returned.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jev_preview.app import JevApp
from jev_preview.config import Config
from jev_preview.editor import EditorError
from jev_preview.screens import CatalogScreen, ConfirmScreen
from jev_preview.store import SavedRequest

SIZE = (120, 40)


@pytest_asyncio.fixture
async def app(config: Config, requests_path: Path) -> AsyncIterator[JevApp]:
    yield JevApp(config=config, directory=requests_path)


async def settle(pilot: Any, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


def stub_editor(app: JevApp, returns: str | None, seen: list[str] | None = None) -> None:
    """Replace the $EDITOR round trip with a canned answer."""

    def fake(initial: str, suffix: str) -> str | None:
        if seen is not None:
            seen.append(initial)
        return returns

    app._run_editor = fake  # type: ignore[method-assign]


class TestExternalContextEdit:
    async def test_the_edited_text_becomes_the_state(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            stub_editor(app, "edited elsewhere")
            await pilot.press("E")
            await settle(pilot)

            assert app.req.state == "edited elsewhere"
            assert not app.query_one("#context").has_class("editing")  # type: ignore[attr-defined]

    async def test_json_written_externally_is_parsed(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            stub_editor(app, '{"chat": ["hi"]}')
            await pilot.press("E")
            await settle(pilot)
            assert app.req.state == {"chat": ["hi"]}

    async def test_a_failed_editor_changes_nothing(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"typed here", "escape")
            await settle(pilot)

            stub_editor(app, None)  # the editor could not be run
            await pilot.press("E")
            await settle(pilot)
            assert app.req.state == "typed here"

    async def test_the_current_text_is_handed_to_the_editor(self, app: JevApp) -> None:
        seen: list[str] = []
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e", *"existing", "escape")
            await settle(pilot)

            stub_editor(app, "existing", seen)
            await pilot.press("E")
            await settle(pilot)
            assert seen == ["existing"]


class TestExternalQuestionsEdit:
    async def test_valid_json_replaces_the_questions(self, app: JevApp) -> None:
        payload = {"urgency": {"type": "noul", "instructions": "Urgent?"}}
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            stub_editor(app, json.dumps(payload))
            await pilot.press("j", "E")
            await settle(pilot)

            assert app.req.questions == payload
            assert [q.qid for q in app.qmodels] == ["urgency"]
            assert app.req.draft_questions is None

    async def test_invalid_json_is_kept_as_a_draft(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            stub_editor(app, "{ definitely not json")
            await pilot.press("j", "E")
            await settle(pilot)

            assert app.req.draft_questions == "{ definitely not json"
            assert "invalid JSON" in str(app.query_one("#questions").border_title)  # type: ignore[attr-defined]

    async def test_a_json_array_is_refused(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            stub_editor(app, "[1, 2]")
            await pilot.press("j", "E")
            await settle(pilot)
            assert app.req.draft_questions == "[1, 2]"

    async def test_an_empty_buffer_clears_the_questions(
        self, config: Config, requests_path: Path
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(
            body={
                "model": "m",
                "state": "x",
                "questions": {"q": {"type": "noul", "instructions": "i"}},
            }
        )
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            stub_editor(app, "   \n")
            await pilot.press("j", "E")
            await settle(pilot)

            assert app.req.questions == {}
            assert app.qmodels == []

    async def test_a_draft_is_reopened_for_fixing(
        self, config: Config, requests_path: Path
    ) -> None:
        seen: list[str] = []
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(body={"state": "x"}, draft_questions="{ broken")
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            stub_editor(app, '{"q": {"type": "noul", "instructions": "fixed"}}', seen)
            await pilot.press("j", "E")
            await settle(pilot)

            assert seen == ["{ broken"]
            assert app.req.draft_questions is None
            assert app.req.questions["q"]["instructions"] == "fixed"

    async def test_an_empty_pane_offers_the_template(self, app: JevApp) -> None:
        from jev_preview.editor import QUESTIONS_TEMPLATE

        seen: list[str] = []
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            stub_editor(app, QUESTIONS_TEMPLATE, seen)
            await pilot.press("j", "E")
            await settle(pilot)
            assert seen == [QUESTIONS_TEMPLATE]

    async def test_existing_questions_are_handed_over_as_json(
        self, config: Config, requests_path: Path
    ) -> None:
        seen: list[str] = []
        payload = {"q": {"type": "noul", "instructions": "i"}}
        app = JevApp(config=config, directory=requests_path)
        app.req = SavedRequest(body={"model": "m", "state": "x", "questions": payload})
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            stub_editor(app, json.dumps(payload), seen)
            await pilot.press("j", "E")
            await settle(pilot)
            assert json.loads(seen[0]) == payload

    async def test_e_on_the_response_pane_does_nothing(self, app: JevApp) -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("l", "E")
            await settle(pilot)
            assert app.req.state == ""


class TestRunEditor:
    async def test_editor_errors_are_reported_not_raised(
        self, app: JevApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import jev_preview.app as app_module

        def boom(initial: str, suffix: str) -> str:
            raise EditorError("cannot run 'nope'")

        monkeypatch.setattr(app_module.editor, "edit_text", boom)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            # suspend() needs a real terminal, so exercise the failure path directly
            monkeypatch.setattr(app, "suspend", _null_context)
            assert app._run_editor("x", ".txt") is None


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


class TestCatalogDelete:
    async def test_ctrl_d_deletes_after_confirmation(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        from textual.widgets import OptionList

        app = JevApp(config=config, directory=requests_path)
        path = saved_request.path
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot)
            assert isinstance(app.screen, CatalogScreen)

            await pilot.press("ctrl+d")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await settle(pilot, 4)

            assert path is not None and not path.exists()
            assert app.screen.query_one(OptionList).option_count == 1  # the "none left" row

    async def test_deleting_the_open_request_resets_the_panes(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        app.req = saved_request
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.load_into_panes()
            await pilot.press("o")
            await settle(pilot)
            await pilot.press("ctrl+d", "y")
            await settle(pilot, 4)
            await pilot.press("escape")
            await settle(pilot, 4)

            assert app.req.is_empty
            assert app.req.path is None

    async def test_cancelling_the_confirmation_keeps_the_file(
        self, config: Config, requests_path: Path, saved_request: SavedRequest
    ) -> None:
        app = JevApp(config=config, directory=requests_path)
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot)
            await pilot.press("ctrl+d", "n")
            await settle(pilot, 4)

            assert saved_request.path is not None and saved_request.path.exists()
