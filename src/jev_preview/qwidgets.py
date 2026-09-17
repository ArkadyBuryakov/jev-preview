"""The structured questions editor.

The shape of a question is fixed by the API, so the editor gives each part a real
control: an id field, a type dropdown, and criteria widgets that follow the type.
Free text is the only thing you type — the structure itself is never hand-written.
"""

from __future__ import annotations

from collections.abc import Iterator

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from jev_preview.questions import TYPES, Question, new_question


class TypeSelect(Select[str]):
    """A Select that leaves the arrow keys to the form.

    Textual's default opens the overlay on up/down, which would make arrows mean
    something different on this one field. Here enter or space opens it instead.
    """

    BINDINGS = [
        Binding("up", "fields(-1)", show=False),
        Binding("down", "fields(1)", show=False),
    ]

    def action_fields(self, direction: int) -> None:
        pane = _owning(self, QuestionsPane)
        if isinstance(pane, QuestionsPane):
            pane.action_focus_field(direction)


class OptionRow(Horizontal):
    """One `criteria` entry of a choice question: option name and its description."""

    def __init__(self, option: str = "", description: str = "") -> None:
        super().__init__(classes="row")
        self.option, self.description = option, description

    def compose(self) -> ComposeResult:
        yield Input(
            self.option,
            placeholder="option",
            compact=True,
            select_on_focus=False,
            classes="opt-key",
        )
        yield Input(
            self.description,
            placeholder="what it means (optional)",
            compact=True,
            select_on_focus=False,
            classes="opt-desc",
        )
        yield Button("✕", compact=True, classes="row-del")


class LevelRow(Horizontal):
    """One level of a score question. The index is the value the API reports."""

    def __init__(self, index: int, description: str = "") -> None:
        super().__init__(classes="row")
        self.index, self.description = index, description

    def compose(self) -> ComposeResult:
        yield Label(str(self.index), classes="lvl-num")
        yield Input(
            self.description,
            placeholder="what this level means",
            compact=True,
            select_on_focus=False,
            classes="lvl-desc",
        )
        yield Button("✕", compact=True, classes="row-del")

    def set_index(self, index: int) -> None:
        self.index = index
        self.query_one(".lvl-num", Label).update(str(index))


class QuestionCard(Vertical):
    """One question: id, type, instructions, and criteria that follow the type."""

    class Rebuilt(Message):
        """The criteria widgets were replaced, so the model needs re-reading."""

    def __init__(self, model: Question) -> None:
        super().__init__(classes="qcard")
        self.model = model

    def compose(self) -> ComposeResult:
        with Horizontal(classes="qhead"):
            yield Input(
                self.model.qid,
                placeholder="question id",
                compact=True,
                select_on_focus=False,
                classes="qid",
            )
            if not self.model.is_advanced:
                yield TypeSelect(
                    [(t, t) for t in TYPES],
                    value=self.model.type,
                    allow_blank=False,
                    compact=True,
                    classes="qtype",
                )

        if self.model.is_advanced:
            yield Static("structured JSON — press E to edit this one", classes="advanced")
        else:
            yield Label("instructions", classes="field")
            yield TextArea(self.model.instructions, soft_wrap=True, classes="qinstr")
            yield Vertical(classes="criteria")

        # last, so tab runs through the fields before it reaches a destructive control
        with Horizontal(classes="qfoot"):
            yield Button("✕ remove", compact=True, classes="qdel")

    def on_mount(self) -> None:
        if not self.model.is_advanced:
            self._fill_criteria()

    # --- criteria, rebuilt whenever the type changes ----------------------
    def _criteria(self) -> Vertical:
        return self.query_one(".criteria", Vertical)

    def _fill_criteria(self) -> None:
        box, kind = self._criteria(), self.model.type
        if kind == "noul":
            box.mount(Label("criteria (optional)", classes="field"))
            for key, value in (("true", self.model.true_desc), ("false", self.model.false_desc)):
                row = Horizontal(classes="row")
                box.mount(row)
                row.mount(Label(key, classes="bool-key"))
                row.mount(
                    Input(
                        value,
                        placeholder=f"what {key} means",
                        compact=True,
                        select_on_focus=False,
                        classes=f"{key}-desc",
                    )
                )
        elif kind == "choice":
            box.mount(Label("options", classes="field"))
            for option, description in self.model.options or [["", ""], ["", ""]]:
                box.mount(OptionRow(option, description))
            box.mount(Button("+ option", compact=True, classes="add-opt"))
        elif kind == "score":
            box.mount(Label("levels, lowest first", classes="field"))
            for index, description in enumerate(self.model.levels or ["", ""]):
                box.mount(LevelRow(index, description))
            box.mount(Button("+ level", compact=True, classes="add-lvl"))

    async def retype(self, kind: str) -> None:
        self.model.type = kind
        await self._criteria().remove_children()
        self._fill_criteria()
        self.post_message(self.Rebuilt())

    async def add_option(self) -> None:
        await self._criteria().mount(OptionRow(), before=self.query_one(".add-opt", Button))

    async def add_level(self) -> None:
        rows = list(self.query(LevelRow))
        await self._criteria().mount(LevelRow(len(rows)), before=self.query_one(".add-lvl", Button))

    def renumber(self) -> None:
        for index, row in enumerate(self.query(LevelRow)):
            row.set_index(index)

    # --- read the widgets back into the model ----------------------------
    def read(self) -> Question:
        model = self.model
        try:
            model.qid = self.query_one(".qid", Input).value.strip()
            if model.is_advanced:
                return model
            model.instructions = self.query_one(".qinstr", TextArea).text
            if model.type == "noul":
                model.true_desc = self.query_one(".true-desc", Input).value
                model.false_desc = self.query_one(".false-desc", Input).value
            elif model.type == "choice":
                model.options = [
                    [
                        row.query_one(".opt-key", Input).value.strip(),
                        row.query_one(".opt-desc", Input).value,
                    ]
                    for row in self.query(OptionRow)
                ]
            elif model.type == "score":
                model.levels = [
                    row.query_one(".lvl-desc", Input).value for row in self.query(LevelRow)
                ]
        except NoMatches:
            pass  # mid-rebuild; the next sync picks it up
        return model


class QuestionsEditor(Vertical):
    """The stack of question cards, plus the control that adds another."""

    class Changed(Message):
        """The form was edited; `questions` is the current model."""

        def __init__(self, questions: list[Question]) -> None:
            super().__init__()
            self.questions = questions

    def __init__(self, models: list[Question]) -> None:
        super().__init__(id="qeditor")
        self.models = models

    def compose(self) -> ComposeResult:
        for model in self.models:
            yield QuestionCard(model)
        yield Button("+ question", compact=True, variant="primary", classes="add-q")

    def sync(self) -> None:
        self.models = [card.read() for card in self.query(QuestionCard)]
        self.post_message(self.Changed(self.models))

    async def add_question(self) -> None:
        model = new_question(self.models)
        card = QuestionCard(model)
        await self.mount(card, before=self.query_one(".add-q", Button))
        field = card.query_one(".qid", Input)
        field.focus()
        field.select_all()  # the generated id is a placeholder: typing should replace it
        self.sync()

    # --- every edit funnels through sync ---------------------------------
    @on(Input.Changed)
    @on(TextArea.Changed)
    def _edited(self) -> None:
        self.sync()

    @on(QuestionCard.Rebuilt)
    def _rebuilt(self) -> None:
        self.sync()

    @on(Select.Changed, ".qtype")
    async def _type_changed(self, event: Select.Changed) -> None:
        event.stop()
        card = _owning(event.select, QuestionCard)
        if isinstance(card, QuestionCard) and event.value != card.model.type:
            await card.retype(str(event.value))

    @on(Button.Pressed)
    async def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button
        card = _owning(button, QuestionCard)
        if button.has_class("add-q"):
            await self.add_question()
            return
        if isinstance(card, QuestionCard):
            if button.has_class("qdel"):
                await card.remove()
            elif button.has_class("add-opt"):
                await card.add_option()
            elif button.has_class("add-lvl"):
                await card.add_level()
        if button.has_class("row-del"):
            row = _owning(button, OptionRow) or _owning(button, LevelRow)
            if row is not None:
                await row.remove()
            if isinstance(card, QuestionCard):
                card.renumber()
        self.sync()


class QuestionsPane(VerticalScroll):
    """Shows the questions; `e` swaps the summary for the editor."""

    can_focus = True

    BINDINGS = [
        Binding("escape", "leave_edit", "done"),
        Binding("tab,down", "focus_field(1)", show=False),
        Binding("shift+tab,up", "focus_field(-1)", show=False),
    ]

    def __init__(self, title: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._title = title
        self.summary = Static()
        self.editor: QuestionsEditor | None = None

    def compose(self) -> ComposeResult:
        yield self.summary

    def on_mount(self) -> None:
        self._sync_border()

    @property
    def editing(self) -> bool:
        return self.editor is not None

    def check_action(self, action: str, _parameters: tuple[object, ...]) -> bool | None:
        if action == "leave_edit":
            return True if self.editing else None
        if action == "focus_field":
            return self.editing or False
        return True

    def action_leave_edit(self) -> None:
        self.app.action_leave_edit()  # type: ignore[attr-defined]

    def action_focus_field(self, direction: int) -> None:
        """Cycle the form's own fields; tab should not wander out of the pane."""
        if direction > 0:
            self.screen.focus_next("#qeditor *")
        else:
            self.screen.focus_previous("#qeditor *")

    def set_title(self, title: str) -> None:
        self._title = title
        self._sync_border()

    def _sync_border(self) -> None:
        self.border_title = f"{self._title}  ✎ esc" if self.editing else self._title
        self.refresh_bindings()

    async def enter_edit(self, models: list[Question]) -> None:
        if self.editing:
            return
        self.summary.display = False
        self.editor = QuestionsEditor(models)
        await self.mount(self.editor)
        self.add_class("editing")
        self._sync_border()
        if not models:
            # an empty form has nothing to type into, which reads as a frozen app
            await self.editor.add_question()
        else:
            self.focus_first_field()

    def focus_first_field(self) -> None:
        fields = self.query(Input)
        if fields:
            fields.first().focus()
        else:
            buttons = self.query(".add-q")
            (buttons.first() if buttons else self).focus()

    def close_now(self) -> None:
        """Tear the editor down without awaiting — the pane is about to be refilled."""
        if self.editor is not None:
            self.editor.remove()
            self.editor = None
        self._show_summary()

    async def leave_edit(self) -> None:
        if self.editor is not None:
            await self.editor.remove()
            self.editor = None
        self._show_summary()
        self.focus()

    def _show_summary(self) -> None:
        self.summary.display = True
        self.remove_class("editing")
        self._sync_border()


def _owning(widget: Widget, kind: type[Widget]) -> Widget | None:
    """The nearest ancestor (or the widget itself) of the given type."""
    nodes: Iterator[Widget] = iter(widget.ancestors_with_self)  # type: ignore[arg-type]
    return next((node for node in nodes if isinstance(node, kind)), None)
