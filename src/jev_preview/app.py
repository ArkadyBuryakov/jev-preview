"""Three-pane TUI sandbox for the TypeSafe (Jev) System One API."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Final, Literal

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Static, TextArea

from jev_preview import __version__, editor, questions
from jev_preview.api import JevClient
from jev_preview.config import Config, mask_key, requests_dir
from jev_preview.qwidgets import QuestionsEditor, QuestionsPane
from jev_preview.render import render_attempt, render_questions
from jev_preview.screens import (
    ApiKeyScreen,
    CatalogResult,
    CatalogScreen,
    ConfirmScreen,
    HelpScreen,
    PromptScreen,
)
from jev_preview.store import Attempt, SavedRequest, StoreError, new_body

PANE_IDS: Final = frozenset({"context", "questions", "response"})
SAVE_DEBOUNCE: Final = 1.5  # seconds of quiet before an edit is written to disk
# bindings that must survive edit mode: leaving it, moving between fields, quitting
ALWAYS_ACTIVE: Final = frozenset({"force_quit", "focus_next", "focus_previous", "command_palette"})

# Directional moves between panes; anything not listed falls through to scrolling.
NAV: Final = {
    ("context", "l"): "response",
    ("context", "j"): "questions",
    ("questions", "l"): "response",
    ("questions", "k"): "context",
    ("response", "h"): "context",
}

NAV_BINDINGS: Final = [
    Binding("h,left", "app.nav('h')", "←", show=False),
    Binding("j,down", "app.nav('j')", "↓", show=False),
    Binding("k,up", "app.nav('k')", "↑", show=False),
    Binding("l,right", "app.nav('l')", "→", show=False),
]


class EditPane(TextArea):
    """A pane that is a plain view until `e` turns it into an editor.

    Read-only is what makes the modality work: in that state TextArea lets
    printable keys through to the app's bindings, so h/j/k/l still navigate.
    """

    # Disabled while read-only so these keys reach the app's own bindings instead:
    # the arrows navigate between panes, and ctrl+k is the app's API-key shortcut.
    VIEW_MODE_DISABLED: Final = frozenset(
        {
            "cursor_up",
            "cursor_down",
            "cursor_left",
            "cursor_right",
            "delete_to_end_of_line_or_delete_line",
        }
    )

    BINDINGS = [Binding("escape", "leave_edit", "done")]

    def __init__(self, title: str, **kwargs: Any) -> None:
        super().__init__(read_only=True, **kwargs)
        self._title = title

    @property
    def editing(self) -> bool:
        return not self.read_only

    def on_mount(self) -> None:
        self._sync_border()

    def check_action(self, action: str, _parameters: tuple[object, ...]) -> bool | None:
        if action == "leave_edit":
            return True if self.editing else None
        return not (action in self.VIEW_MODE_DISABLED and self.read_only)

    def set_title(self, title: str) -> None:
        self._title = title
        self._sync_border()

    def _sync_border(self) -> None:
        self.border_title = f"{self._title}  ✎ esc" if self.editing else self._title
        self.refresh_bindings()  # the footer's key list depends on the mode

    def enter_edit(self) -> None:
        self.read_only = False
        self.add_class("editing")
        self._sync_border()

    def action_leave_edit(self) -> None:
        self.leave_edit()
        self.app.persist()  # type: ignore[attr-defined]

    def leave_edit(self) -> None:
        self.read_only = True
        self.remove_class("editing")
        self._sync_border()


class ResponsePane(VerticalScroll):
    """Read-only, so it keeps the bare vim keys for scrolling and history."""

    can_focus = True

    BINDINGS = [
        *NAV_BINDINGS,
        Binding("g", "scroll_home", "top", show=False),
        Binding("G", "scroll_end", "bottom", show=False),
    ]

    def __init__(self, title: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self.body = Static()

    def compose(self) -> ComposeResult:
        yield self.body

    def on_mount(self) -> None:
        self.border_title = self._title

    def set_title(self, title: str) -> None:
        self._title = title
        self.border_title = title


class JevApp(App[None]):
    """The application: one request in three panes, saved as you go."""

    CSS_PATH = "app.tcss"
    TITLE = "jev-preview"
    SUB_TITLE = __version__

    BINDINGS = [
        *NAV_BINDINGS,
        Binding("e", "edit", "edit"),
        Binding("E", "external_edit", "$EDITOR"),
        Binding("enter", "send", "send"),
        Binding("o", "catalog", "open"),
        Binding("n", "new_request", "new"),
        Binding("c", "copy_request", "copy"),
        Binding("r", "rename", "rename"),
        Binding("d", "delete_request", "delete", show=False),
        Binding("m", "cycle_model", "model"),
        Binding("ctrl+k", "change_key", "api key", show=False),
        Binding("left_square_bracket", "history(-1)", "prev", show=False),
        Binding("right_square_bracket", "history(1)", "next", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "save_quit", "quit"),
        Binding("ctrl+q", "force_quit", "quit", show=False),
    ]

    def __init__(self, config: Config | None = None, directory: Path | None = None) -> None:
        super().__init__()
        self.config = config or Config.load()
        self.directory = directory or requests_dir()
        self.req = SavedRequest(body=new_body(self.config.models[0]))
        self.history_index = 0
        self.sending = False
        self.qmodels: list[questions.Question] = []
        self.question_errors: list[str] = []
        self._loading = False  # suppresses Changed while we fill the buffers
        self._dirty = False

    # --- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="statusbar")
        with Horizontal(id="panes"):
            with Vertical(id="left"):
                yield EditPane("context", id="context", soft_wrap=True)
                yield QuestionsPane("questions", id="questions")
            yield ResponsePane("response", id="response")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#context", EditPane).theme = "vscode_dark"
        self.load_into_panes()
        self.query_one("#context", EditPane).focus()
        self._save_timer = self.set_interval(SAVE_DEBOUNCE, self.autosave)
        if not self.config.has_api_key:
            self.ask_for_key(first_run=True)

    # --- API key ----------------------------------------------------------
    @work
    async def ask_for_key(self, first_run: bool = False) -> None:
        """Onboarding, and the same screen behind ctrl+k later on."""
        key = await self.push_screen_wait(ApiKeyScreen(self.config))
        if key is None:
            if first_run:
                self.exit(
                    message="No API key — nothing to talk to. Run jev-preview again to set one."
                )
            return
        self.config = self.config.with_api_key(key)
        try:
            path = self.config.save()
        except OSError as exc:
            self.notify(f"could not save the key: {exc}", severity="error")
        else:
            self.notify(f"key {mask_key(self.config.api_key)} saved to {path}")
        self.refresh_status()

    def action_change_key(self) -> None:
        self.ask_for_key()

    # --- syncing model <-> buffers ----------------------------------------
    def load_into_panes(self) -> None:
        """Push the current request into the panes (on open/new/copy)."""
        self._close_editor()
        self._loading = True
        try:
            self.query_one("#context", EditPane).text = editor.dump_state(self.req.state)[0]
        finally:
            self._loading = False
        self.qmodels = questions.load(self.req.questions)
        self.refresh_questions()
        self.refresh_status()
        self.refresh_response()

    def _close_editor(self) -> None:
        """Drop any open editor without awaiting; the panes are about to be refilled."""
        for pane in self.query(EditPane):
            if pane.editing:
                pane.leave_edit()
        qpane = self.query_one("#questions", QuestionsPane)
        if qpane.editing:
            qpane.close_now()

    def refresh_questions(self) -> None:
        """Re-validate and redraw the read-only view of the questions."""
        pane = self.query_one("#questions", QuestionsPane)
        draft = self.req.draft_questions
        self.question_errors = [] if draft is not None else questions.validate(self.qmodels)
        pane.summary.update(render_questions(self.qmodels, self.question_errors, draft))

        if draft is not None:
            pane.set_title("questions — invalid JSON")
        elif self.question_errors:
            pane.set_title(f"questions ({len(self.qmodels)}) — {len(self.question_errors)} to fix")
        else:
            count = len(self.qmodels)
            pane.set_title(f"questions ({count})" if count else "questions")
        pane.set_class(bool(draft or self.question_errors), "invalid")

    @on(QuestionsEditor.Changed)
    def _questions_edited(self, event: QuestionsEditor.Changed) -> None:
        self.qmodels = event.questions
        self.req.questions = questions.dump(self.qmodels)
        self.req.draft_questions = None
        self._touch()
        self.refresh_questions()
        self.refresh_status()

    @on(TextArea.Changed)
    def _buffer_changed(self, event: TextArea.Changed) -> None:
        if self._loading or event.text_area.id != "context":
            return
        self.req.state = editor.parse_state(event.text_area.text)
        self._touch()
        self.refresh_status()

    def _touch(self) -> None:
        """Mark unsaved work and push the write out until typing pauses."""
        self._dirty = True
        self._save_timer.reset()

    # --- rendering --------------------------------------------------------
    def refresh_status(self) -> None:
        name = self.req.name or "new request"
        if len(name) > 40:
            name = f"{name[:39]}…"
        where = self.req.path.name if self.req.path else "unsaved"
        key = "" if self.config.has_api_key else "  [red]no API key (ctrl+k)[/]"
        self.query_one("#statusbar", Static).update(
            f" [bold]{name}[/]  [dim]·[/]  [cyan]{self.req.model}[/]  [dim]·  {where}[/]{key}"
        )

    def refresh_response(self) -> None:
        pane = self.query_one("#response", ResponsePane)
        total = len(self.req.responses)
        if self.sending:
            pane.set_title("response")
            pane.body.update("[yellow]sending…[/]")
            return
        if not total:
            pane.set_title("response")
            pane.body.update("[dim]no responses yet — press enter to send[/]")
            return
        self.history_index = max(0, min(self.history_index, total - 1))
        pane.set_title(f"response ({self.history_index + 1}/{total})")
        pane.body.update(
            render_attempt(self.req.responses[self.history_index], self.history_index, total)
        )
        pane.scroll_home(animate=False)

    # --- persistence ------------------------------------------------------
    def persist(self) -> None:
        self._dirty = False
        if self.req.is_empty:
            return
        try:
            self.req.save(self.directory)
        except StoreError as exc:
            self.notify(str(exc), severity="error")

    def autosave(self) -> None:
        if self._dirty:
            self.persist()
            self.refresh_status()

    # --- pane helpers -----------------------------------------------------
    def focused_pane(self) -> EditPane | QuestionsPane | ResponsePane | None:
        node = self.focused
        while node is not None and node.id not in PANE_IDS:
            node = node.parent  # type: ignore[assignment]
        return node  # type: ignore[return-value]

    def is_editing(self) -> bool:
        return any(pane.editing for pane in self.query(EditPane)) or any(
            pane.editing for pane in self.query(QuestionsPane)
        )

    def check_action(self, action: str, _parameters: tuple[object, ...]) -> bool | None:
        """In edit mode every key belongs to the editor, so hide the command keys."""
        if action not in ALWAYS_ACTIVE and self.is_editing():
            return None
        return True

    def action_nav(self, direction: str) -> None:
        pane = self.focused_pane()
        if pane is None:
            return
        target = NAV.get((pane.id or "", direction))
        if target:
            self.query_one(f"#{target}").focus()

    # --- editing ----------------------------------------------------------
    @work
    async def action_edit(self) -> None:
        pane = self.focused_pane()
        if isinstance(pane, EditPane):
            pane.enter_edit()
        elif isinstance(pane, QuestionsPane):
            if self.req.draft_questions is not None:
                self.notify("questions do not parse as JSON — press E to fix", severity="error")
                return
            try:
                await pane.enter_edit(self.qmodels)
            except Exception as exc:  # never strand the user in a dead editor
                await pane.leave_edit()
                self.notify(f"could not open the editor: {exc}", severity="error")
        else:
            self.notify("nothing to edit here", severity="warning")

    @work
    async def action_leave_edit(self) -> None:
        await self.leave_edit_mode()
        self.persist()

    async def leave_edit_mode(self) -> None:
        for pane in self.query(EditPane):
            if pane.editing:
                pane.leave_edit()
        qpane = self.query_one("#questions", QuestionsPane)
        if qpane.editing:
            await qpane.leave_edit()
            kept = [q for q in self.qmodels if not questions.is_untouched(q)]
            if len(kept) != len(self.qmodels):
                self.qmodels = kept
                self.req.questions = questions.dump(kept)
            self.refresh_questions()

    def action_external_edit(self) -> None:
        pane = self.focused_pane()
        if isinstance(pane, EditPane):
            self._edit_context_externally(pane)
        elif isinstance(pane, QuestionsPane):
            self._edit_questions_externally()
        else:
            self.notify("nothing to edit here", severity="warning")

    def _run_editor(self, initial: str, suffix: str) -> str | None:
        """Suspend the app for $EDITOR; None means it could not be run."""
        try:
            with self.suspend():
                return editor.edit_text(initial, suffix)
        except editor.EditorError as exc:
            self.notify(str(exc), severity="error")
            return None

    def _edit_context_externally(self, pane: EditPane) -> None:
        text = self._run_editor(pane.text, editor.dump_state(self.req.state)[1])
        if text is None:
            return
        pane.text = text  # Changed fires and syncs the model
        pane.leave_edit()  # back to normal mode; $EDITOR was the edit
        pane.focus()
        self.persist()

    def _edit_questions_externally(self) -> None:
        """The escape hatch: raw JSON, for shapes the form deliberately does not model."""
        initial = self.req.draft_questions
        if initial is None:
            initial = (
                json.dumps(questions.dump(self.qmodels), indent=2, ensure_ascii=False) + "\n"
                if self.qmodels
                else editor.QUESTIONS_TEMPLATE
            )
        text = self._run_editor(initial, ".json")
        if text is None:
            return
        self._apply_questions_text(text)
        self.refresh_questions()
        self.refresh_status()
        self.query_one("#questions", QuestionsPane).focus()
        self.persist()

    def _apply_questions_text(self, text: str) -> None:
        """Take raw JSON back from $EDITOR, keeping it verbatim if it does not parse."""
        stripped = text.strip()
        if not stripped:
            self.qmodels, self.req.questions, self.req.draft_questions = [], {}, None
            self._dirty = True
            return
        try:
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict):
                raise json.JSONDecodeError("questions must be a JSON object", stripped, 0)
        except json.JSONDecodeError as exc:
            self.req.draft_questions = text
            self.notify(f"invalid JSON: {exc.msg} — press E to fix", severity="error")
        else:
            self.req.questions = parsed
            self.qmodels = questions.load(parsed)
            self.req.draft_questions = None
        self._dirty = True

    # --- sending ----------------------------------------------------------
    def action_send(self) -> None:
        if self.sending:
            return
        problem = self._why_not_send()
        if problem is not None:
            self.notify(problem[0], severity=problem[1])
            return
        self.persist()
        self.sending = True
        self.refresh_response()
        self._send_worker(copy.deepcopy(self.req.body))

    def _why_not_send(self) -> tuple[str, Literal["warning", "error"]] | None:
        """The first reason this request cannot go out, with how loudly to say it."""
        if not self.config.has_api_key:
            return ("no API key — press ctrl+k to set one", "error")
        if self.req.draft_questions is not None:
            return ("questions do not parse as JSON — press E to fix", "error")
        if not self.qmodels:
            return ("add at least one question first", "warning")
        if self.question_errors:
            return (self.question_errors[0], "error")
        return None

    @work(thread=True, exclusive=True, group="send")
    def _send_worker(self, body: dict[str, Any]) -> None:
        attempt = JevClient.from_config(self.config).evaluate(body)
        self.call_from_thread(self._receive, attempt)

    def _receive(self, attempt: Attempt) -> None:
        self.sending = False
        self.req.responses.append(attempt)
        self.history_index = len(self.req.responses) - 1
        self.persist()
        self.refresh_status()
        self.refresh_response()
        self.query_one("#response", ResponsePane).focus()
        if not attempt.ok:
            self.notify(attempt.error or f"HTTP {attempt.status}", severity="error")

    def action_history(self, delta: int) -> None:
        if not self.req.responses:
            return
        self.history_index = max(0, min(self.history_index + delta, len(self.req.responses) - 1))
        self.refresh_response()

    # --- requests ---------------------------------------------------------
    def action_new_request(self) -> None:
        self.persist()
        self._replace(self._blank())
        self.query_one("#context", EditPane).focus()

    def action_copy_request(self) -> None:
        """Branch off the current request: body only, no responses, fresh timestamp."""
        if self.req.is_empty:
            self.notify("nothing to copy", severity="warning")
            return
        self.persist()
        source = self.req
        self._replace(
            SavedRequest(
                name=f"Copy of {source.name}" if source.name else "",
                body=copy.deepcopy(source.body),
                draft_questions=source.draft_questions,
                auto_name=False,  # "Copy of …" is a real name, not one tracking the context
            )
        )
        self.persist()
        self.refresh_status()
        self.notify(f"copied to “{self.req.name}”")

    @work
    async def action_delete_request(self) -> None:
        if self.req.path is None:
            self.notify("nothing to delete — this request was never saved", severity="warning")
            return
        name = self.req.name or self.req.path.name
        if not await self.push_screen_wait(
            ConfirmScreen(f"Delete “{name}” and its responses?", confirm_label="Delete")
        ):
            return
        try:
            self.req.delete()
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self._replace(self._blank())
        self.notify(f"deleted “{name}”")

    def _blank(self) -> SavedRequest:
        """An empty request that keeps the model currently in use."""
        return SavedRequest(body=new_body(self.req.model))

    def _replace(self, request: SavedRequest) -> None:
        """Make `request` the one on screen, dropping any pending save for the old one."""
        self.req = request
        self.history_index = max(0, len(request.responses) - 1)
        self._dirty = False
        self.load_into_panes()

    def action_cycle_model(self) -> None:
        models = self.config.models
        current = self.req.model
        index = models.index(current) + 1 if current in models else 0
        self.req.model = models[index % len(models)]
        self.persist()
        self.refresh_status()

    @work
    async def action_catalog(self) -> None:
        result: CatalogResult = await self.push_screen_wait(
            CatalogScreen(self.directory, self.req.path)
        )
        if self.req.path is not None and self.req.path in result.deleted:
            # the request on screen no longer exists on disk; start clean
            self._replace(self._blank())
        if result.open_path is None:
            return
        try:
            loaded = SavedRequest.load(result.open_path)
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.persist()
        self._replace(loaded)

    @work
    async def action_rename(self) -> None:
        name = await self.push_screen_wait(PromptScreen("Request name", self.req.name))
        if name is None or not name.strip():
            return
        try:
            self.req.rename(name, self.directory)
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.refresh_status()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_save_quit(self) -> None:
        self.persist()
        self.exit()

    def action_force_quit(self) -> None:
        """ctrl+q also works mid-edit, when `q` itself is just a character."""
        self.action_save_quit()

    def on_unmount(self) -> None:
        if self._dirty:
            self.persist()
