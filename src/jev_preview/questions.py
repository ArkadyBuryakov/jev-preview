"""The questions map as an editable model.

The API's three question types each have a fixed shape (see
https://docs.typesafe.ai/api), so the editor can offer real fields instead of raw
JSON. Anything that does not fit those shapes — the structured `instructions` and
criteria the advanced docs allow — is carried through verbatim as `advanced`
rather than flattened or dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final

TYPES: Final = ("noul", "choice", "score")
TYPE_HELP: Final = {
    "noul": "yes/no — returns the probability the answer is yes",
    "choice": "pick one option — returns the choice and a distribution",
    "score": "rate against ordered levels — returns a weighted score",
}

_GENERATED_ID = re.compile(r"question_\d+")


@dataclass(slots=True)
class Question:
    """One entry of the questions map, in a shape the form can edit."""

    qid: str
    type: str = "noul"
    instructions: str = ""
    true_desc: str = ""  # noul criteria
    false_desc: str = ""  # noul criteria
    options: list[list[str]] = field(default_factory=list)  # choice: [option, description]
    levels: list[str] = field(default_factory=list)  # score: ordered descriptions
    advanced: dict[str, Any] | None = None  # a shape the form cannot represent

    @property
    def is_advanced(self) -> bool:
        return self.advanced is not None


def load(questions: dict[str, Any] | None) -> list[Question]:
    return [_load_one(str(qid), raw) for qid, raw in (questions or {}).items()]


def _load_one(qid: str, raw: Any) -> Question:
    if not isinstance(raw, dict):
        return Question(qid, advanced={"value": raw})

    qtype, instructions, criteria = raw.get("type"), raw.get("instructions"), raw.get("criteria")
    if qtype not in TYPES or not isinstance(instructions, (str, type(None))):
        return Question(qid, advanced=dict(raw))

    question = Question(qid, str(qtype), instructions or "")
    if qtype == "noul":
        if criteria is None:
            return question
        if (
            isinstance(criteria, dict)
            and set(criteria) <= {"true", "false"}
            and _strings_only(criteria.values())
        ):
            question.true_desc = criteria.get("true") or ""
            question.false_desc = criteria.get("false") or ""
            return question
    elif qtype == "choice":
        if isinstance(criteria, dict) and criteria and _strings_only(criteria.values()):
            question.options = [[str(k), v or ""] for k, v in criteria.items()]
            return question
    elif qtype == "score":
        if isinstance(criteria, list) and criteria and all(isinstance(v, str) for v in criteria):
            question.levels = list(criteria)
            return question
    return Question(qid, advanced=dict(raw))


def dump(questions: Iterable[Question]) -> dict[str, Any]:
    return {
        q.qid: (dict(q.advanced) if q.advanced is not None else _dump_one(q)) for q in questions
    }


def _dump_one(question: Question) -> dict[str, Any]:
    body: dict[str, Any] = {"type": question.type, "instructions": question.instructions}
    if question.type == "noul":
        criteria = {
            key: value
            for key, value in (("true", question.true_desc), ("false", question.false_desc))
            if value
        }
        if criteria:
            body["criteria"] = criteria
    elif question.type == "choice":
        # the API takes null for an option that needs no extra detail
        body["criteria"] = {opt: (desc or None) for opt, desc in question.options if opt}
    elif question.type == "score":
        body["criteria"] = list(question.levels)
    return body


def validate(questions: list[Question]) -> list[str]:
    """Problems that would make the request invalid, in the order they appear."""
    errors: list[str] = []
    seen: set[str] = set()
    for index, question in enumerate(questions, start=1):
        label = question.qid or f"question {index}"
        if not question.qid.strip():
            errors.append(f"{label}: needs an id")
        elif question.qid in seen:
            errors.append(f"“{question.qid}”: duplicate id")
        seen.add(question.qid)

        if question.is_advanced:
            continue
        if not question.instructions.strip():
            errors.append(f"{label}: instructions are required")
        if question.type == "choice":
            names = [opt.strip() for opt, _ in question.options if opt.strip()]
            if len(names) < 2:
                errors.append(f"{label}: a choice needs at least two options")
            elif len(set(names)) != len(names):
                errors.append(f"{label}: duplicate option")
        elif question.type == "score" and len([lv for lv in question.levels if lv.strip()]) < 2:
            errors.append(f"{label}: a score needs at least two levels")
    return errors


def is_untouched(question: Question) -> bool:
    """True for a question we created and the user never filled in."""
    if question.is_advanced or not _GENERATED_ID.fullmatch(question.qid):
        return False
    texts = [
        question.instructions,
        question.true_desc,
        question.false_desc,
        *(text for pair in question.options for text in pair),
        *question.levels,
    ]
    return not any(text.strip() for text in texts)


def new_question(existing: list[Question]) -> Question:
    taken = {q.qid for q in existing}
    number = len(existing) + 1
    while f"question_{number}" in taken:
        number += 1
    return Question(qid=f"question_{number}", type="noul")


def _strings_only(values: Iterable[Any]) -> bool:
    return all(v is None or isinstance(v, str) for v in values)
