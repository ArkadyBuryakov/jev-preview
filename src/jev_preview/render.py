"""Rich renderables for a recorded response: a readable summary, then raw JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from jev_preview.questions import Question
from jev_preview.store import Attempt

BAR_WIDTH = 18


def json_block(value: Any) -> RenderableType:
    return Syntax(
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
        "json",
        theme="ansi_dark",
        word_wrap=True,
        background_color="default",
    )


def render_attempt(attempt: Attempt, index: int, total: int) -> RenderableType:
    """One recorded response: status line, decoded answers, then the raw body."""
    status = (
        Text("error", style="bold red")
        if attempt.status is None
        else Text(str(attempt.status), style="bold green" if attempt.ok else "bold red")
    )
    header = Text.assemble(
        (f"[{index + 1}/{total}] ", "grey62"),
        status,
        ("  ", ""),
        (attempt.timestamp, "grey62"),
        ("  ", ""),
        (f"{attempt.duration_ms} ms", "grey62"),
    )
    parts: list[RenderableType] = [header, Text("")]

    if attempt.error:
        parts.append(Text(attempt.error, style="red"))
        return Group(*parts)

    body = attempt.body
    if isinstance(body, dict) and attempt.ok and isinstance(body.get("answers"), dict):
        parts.append(_usage(body))
        parts.append(Text(""))
        for qid, answer in body["answers"].items():
            shaped = answer if isinstance(answer, dict) else {"value": answer}
            parts.append(_answer(str(qid), shaped))
        parts.append(Text("── raw ──", style="grey35"))

    parts.append(json_block(body))
    return Group(*parts)


def render_questions(
    models: Sequence[Question], errors: Sequence[str], draft: str | None = None
) -> RenderableType:
    """The read-only view of the questions pane."""
    if draft is not None:
        return Group(
            Text("this JSON does not parse — press E to fix it", style="red"),
            Text(""),
            Text(draft, style="grey62"),
        )
    if not models:
        return Text("no questions yet — press e to add one", style="dim")

    blocks: list[RenderableType] = []
    for model in models:
        blocks.extend(_question_block(model))
    if errors:
        blocks.append(Text("\n".join(f"⚠ {error}" for error in errors), style="red"))
    return Group(*blocks)


# --- pieces ---------------------------------------------------------------
def _usage(body: dict[str, Any]) -> RenderableType:
    usage = body.get("usage") or {}
    return Text.assemble(
        (str(body.get("model", "")), "bold white"),
        ("   in ", "grey62"),
        (str(usage.get("input_tokens", "?")), "white"),
        (" / out ", "grey62"),
        (str(usage.get("output_tokens", "?")), "white"),
        (" tokens", "grey62"),
    )


def _bar(probability: float, highlight: bool) -> Text:
    filled = max(0, min(BAR_WIDTH, round(probability * BAR_WIDTH)))
    style = "bold cyan" if highlight else "grey50"
    return Text("█" * filled + "·" * (BAR_WIDTH - filled), style=style)


def _distribution(probabilities: dict[Any, Any], winner: Any) -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="white")
    table.add_column()
    table.add_column(justify="right", style="grey62")
    for key, probability in sorted(probabilities.items(), key=lambda kv: -_number(kv[1])):
        value = _number(probability)
        is_winner = str(key) == str(winner)
        table.add_row(
            Text(str(key), style="bold cyan" if is_winner else "white"),
            _bar(value, is_winner),
            f"{value:.3f}",
        )
    return table


def _confidence(answer: dict[str, Any]) -> Text | None:
    if "confidence" not in answer:
        return None
    value = _number(answer["confidence"])
    style = "green" if value >= 0.75 else "yellow" if value >= 0.5 else "red"
    return Text.assemble(("confidence ", "grey62"), (f"{value:.3f}", style))


def _answer(qid: str, answer: dict[str, Any]) -> RenderableType:
    kind = answer.get("type", "?")
    parts: list[RenderableType] = [
        Text.assemble((qid, "bold magenta"), ("  ", ""), (str(kind), "grey62"))
    ]

    if kind == "noul":
        value = _number(answer.get("noul"))
        table = Table.grid(padding=(0, 1))
        table.add_row(_bar(value, True), Text(f"{value:.3f}", style="bold cyan"))
        parts.append(table)
    elif kind == "choice":
        parts.append(Text.assemble(("→ ", "grey62"), (str(answer.get("choice")), "bold cyan")))
        parts.append(_distribution(answer.get("probabilities") or {}, answer.get("choice")))
    elif kind == "score":
        parts.append(
            Text.assemble(("→ ", "grey62"), (f"{_number(answer.get('score')):.2f}", "bold cyan"))
        )
        parts.append(_levels(answer))
    else:
        parts.append(json_block(answer))

    confidence = _confidence(answer)
    if confidence is not None:
        parts.append(confidence)
    parts.append(Text(""))
    return Group(*parts)


def _levels(answer: dict[str, Any]) -> Table:
    legend = answer.get("legend") or {}
    probabilities = answer.get("probabilities") or {}
    top = max(probabilities, key=lambda k: _number(probabilities[k])) if probabilities else None
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="grey62")
    table.add_column(style="white")
    table.add_column()
    table.add_column(justify="right", style="grey62")
    for level, probability in sorted(probabilities.items(), key=lambda kv: str(kv[0])):
        value = _number(probability)
        table.add_row(
            str(level),
            str(legend.get(str(level), "")),
            _bar(value, str(level) == str(top)),
            f"{value:.3f}",
        )
    return table


def _question_block(model: Question) -> list[RenderableType]:
    head = Table.grid(expand=True)
    head.add_column(ratio=1)
    head.add_column(justify="right")
    head.add_row(
        Text(model.qid or "(no id)", style="bold magenta"),
        Text("json" if model.is_advanced else model.type, style="grey62"),
    )
    blocks: list[RenderableType] = [head]

    if model.is_advanced:
        blocks.append(json_block(model.advanced))
        blocks.append(Text(""))
        return blocks

    blocks.append(
        Text(
            f"  {model.instructions}" if model.instructions else "  (no instructions)",
            style="white" if model.instructions else "red",
        )
    )
    detail = Table.grid(padding=(0, 1))
    detail.add_column(justify="right", style="cyan")
    detail.add_column(style="grey62")
    if model.type == "noul":
        for key, value in (("true", model.true_desc), ("false", model.false_desc)):
            if value:
                detail.add_row(key, value)
    elif model.type == "choice":
        for option, description in model.options:
            detail.add_row(option or "—", description)
    elif model.type == "score":
        for index, description in enumerate(model.levels):
            detail.add_row(str(index), description)
    if detail.row_count:
        blocks.append(Padding(detail, (0, 0, 0, 2)))
    blocks.append(Text(""))
    return blocks


def _number(value: Any) -> float:
    """Probabilities arrive as JSON numbers; tolerate anything that is not one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
