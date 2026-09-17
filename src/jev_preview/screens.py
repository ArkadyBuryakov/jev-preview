"""Modal screens: onboarding, the catalogue, and the small prompts around it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from jev_preview.config import API_KEY_ENV, Config, mask_key
from jev_preview.store import SavedRequest, StoreError, list_requests


class ApiKeyScreen(ModalScreen[str | None]):
    """Ask for the API key. Dismissing without one is a refusal, not a retry."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="apikey"):
            yield Label("TypeSafe API key", id="apikey-title")
            yield Static(self._explanation(), id="apikey-help")
            yield Input(password=True, placeholder="sk-…", id="apikey-input")
            with Horizontal(id="apikey-buttons"):
                yield Button("Save", variant="primary", id="apikey-save")
                yield Button("Cancel", id="apikey-cancel")
            yield Static(self._footer(), id="apikey-footer")

    def _explanation(self) -> str:
        if self._config.has_api_key:
            return (
                f"A key is already configured ([cyan]{mask_key(self._config.api_key)}[/]).\n"
                "Enter a new one to replace it."
            )
        return (
            "jev-preview needs a key for the TypeSafe API.\n"
            "Get one at [cyan]https://docs.typesafe.ai[/], or set "
            f"[cyan]${API_KEY_ENV}[/] in your shell."
        )

    def _footer(self) -> str:
        stored = f"Stored in [dim]{self._config.path}[/], readable only by you."
        return f"{stored}\n[dim]enter save · esc cancel[/]"

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apikey-save":
            self._submit(self.query_one(Input).value)
        else:
            self.dismiss(None)

    def _submit(self, value: str) -> None:
        key = value.strip()
        if not key:
            self.notify("enter a key, or press esc to cancel", severity="warning")
            return
        self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass(slots=True)
class CatalogResult:
    """What the catalogue did: a request to open, plus anything deleted on the way."""

    open_path: Path | None = None
    deleted: list[Path] = field(default_factory=list)


class CatalogScreen(ModalScreen[CatalogResult]):
    """Browse saved requests: type to filter, `d` to delete, enter to open."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("down", "cursor(1)", "down", show=False),
        Binding("up", "cursor(-1)", "up", show=False),
        # priority: the filter box binds ctrl+d to delete-character-right
        Binding("ctrl+d", "delete", "delete", show=False, priority=True),
    ]

    def __init__(self, directory: Path, current: Path | None = None) -> None:
        super().__init__()
        self.directory = directory
        self.current = current
        self.deleted: list[Path] = []
        self._entries: list[tuple[Path, str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="catalog"):
            yield Label("Requests", id="catalog-title")
            yield Input(placeholder="filter…", id="catalog-filter")
            yield OptionList(id="catalog-list")
            yield Label(
                "enter open · ↑/↓ move · ctrl+d delete · esc cancel",
                id="catalog-hint",
            )

    def on_mount(self) -> None:
        self._entries = list(self._scan())
        self._fill("")
        self.query_one("#catalog-filter", Input).focus()

    # --- data -------------------------------------------------------------
    def _scan(self) -> list[tuple[Path, str, str]]:
        """Every saved request as (path, prompt markup, searchable text)."""
        entries: list[tuple[Path, str, str]] = []
        for path in list_requests(self.directory):
            try:
                request = SavedRequest.load(path)
            except StoreError:
                entries.append((path, f"[red]{path.name} (unreadable)[/]", path.name))
                continue
            runs = len(request.responses)
            marker = "[cyan]▸[/] " if path == self.current else "  "
            entries.append(
                (
                    path,
                    f"{marker}[bold]{request.name}[/]\n"
                    f"    [dim]{request.created_at}  ·  {runs} response"
                    f"{'s' if runs != 1 else ''}  ·  {path.name}[/]",
                    f"{request.name}\n{path.name}",
                )
            )
        return entries

    def _fill(self, needle: str) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        needle = needle.strip().lower()
        matches = [
            Option(prompt, id=str(path))
            for path, prompt, haystack in self._entries
            if needle in haystack.lower()
        ]
        if matches:
            option_list.add_options(matches)
            option_list.highlighted = 0
        else:
            empty = "no requests match" if needle else "no saved requests yet"
            option_list.add_option(Option(f"[dim]{empty}[/]", disabled=True))

    def _highlighted_path(self) -> Path | None:
        option_list = self.query_one(OptionList)
        index = option_list.highlighted
        if index is None:
            return None
        try:
            option = option_list.get_option_at_index(index)
        except IndexError:
            return None
        return Path(option.id) if option.id else None

    # --- events -----------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        self._fill(event.value)

    def on_input_submitted(self) -> None:
        path = self._highlighted_path()
        if path is not None:
            self.dismiss(CatalogResult(open_path=path, deleted=self.deleted))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(CatalogResult(open_path=Path(event.option.id), deleted=self.deleted))

    def action_cursor(self, direction: int) -> None:
        option_list = self.query_one(OptionList)
        if direction > 0:
            option_list.action_cursor_down()
        else:
            option_list.action_cursor_up()

    @work
    async def action_delete(self) -> None:
        """Delete the highlighted request; a worker, because it awaits a screen."""
        path = self._highlighted_path()
        if path is None:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(f"Delete “{path.name}”?", confirm_label="Delete")
        )
        if not confirmed:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.notify(f"could not delete: {exc}", severity="error")
            return
        self.deleted.append(path)
        self._entries = [entry for entry in self._entries if entry[0] != path]
        self._fill(self.query_one("#catalog-filter", Input).value)
        self.notify(f"deleted {path.name}")

    def action_cancel(self) -> None:
        self.dismiss(CatalogResult(deleted=self.deleted))


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question; anything but an explicit yes is a no."""

    BINDINGS = [
        Binding("escape,n", "answer(False)", "no"),
        Binding("y", "answer(True)", "yes"),
    ]

    def __init__(self, question: str, confirm_label: str = "OK") -> None:
        super().__init__()
        self.question = question
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Label(self.question, id="confirm-question")
            with Horizontal(id="confirm-buttons"):
                yield Button(self.confirm_label, variant="error", id="confirm-yes")
                yield Button("Cancel", id="confirm-no")
            yield Static("[dim]y confirm · n / esc cancel[/]", id="confirm-hint")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


class PromptScreen(ModalScreen[str | None]):
    """A single-line question, e.g. the name of a request."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, prompt: str, value: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt"):
            yield Label(self.prompt)
            yield Input(value=self.value, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """The key map, as it is actually bound."""

    BINDINGS = [Binding("escape,q,question_mark,f1", "close", "close")]

    HELP = """\
[bold]normal mode[/]
  h j k l        move between panes (arrow keys too)
  e              edit the focused pane here
  E              edit it in $EDITOR instead
  enter          send the request
  [ / ]          previous / next response in history
  o              open requests catalogue      n   new (empty) request
  c              copy request                 r   rename request
  d              delete this request          m   cycle model
  ctrl+k         change the API key           ?   this help
  q              quit

[bold]edit mode[/] [dim](e)[/]
  esc            back to normal mode, saving as you go
  context        a plain text buffer — everything types
  questions      a form: tab or arrows between fields, enter/space on a button
                 type picks the criteria shape; only free text is typed
  E              raw JSON in $EDITOR, for structures the form does not model

[bold]catalogue[/] [dim](o)[/]
  type           filter by name or filename
  ↑ / ↓          move            enter   open           ctrl+d  delete

[bold]response pane[/]
  j / k          scroll          g / G   top / bottom      pgup / pgdn

[dim]ctrl+h/j/k/l are left alone, so they still reach your terminal's splits
navigator. Edits are saved automatically.[/]
"""

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static(self.HELP)

    def action_close(self) -> None:
        self.dismiss(None)
