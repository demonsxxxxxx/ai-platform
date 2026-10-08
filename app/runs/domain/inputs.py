"""Run-owned supplementary text and native question input rules."""

from __future__ import annotations

import re
from typing import Any, Callable
from uuid import UUID


MAX_RUN_INPUT_CHARS = 16_000
MAX_RUN_QUESTION_BATCH = 4
MAX_RUN_QUESTION_OPTIONS = 8
MAX_RUN_QUESTION_ID_CHARS = 128
MAX_RUN_ANSWER_CHARS = 16_000
_QUESTION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class RunInputError(ValueError):
    """Base class for stable Run input validation and lifecycle errors."""

    code = "run_input_invalid"

    def __init__(self, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(self.code)


class RunInputClosed(RunInputError):
    code = "run_input_closed"


class RunInputConflict(RunInputError):
    code = "run_input_id_conflict"


def normalize_input_id(value: UUID | str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RunInputError("run_input_id_invalid") from exc


def normalize_question_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _QUESTION_ID_PATTERN.fullmatch(value)
    ):
        raise RunInputError("run_input_question_id_invalid")
    return value


def sanitize_run_input_text(
    value: object,
    *,
    sanitize_text: Callable[[object], str],
    max_chars: int = MAX_RUN_INPUT_CHARS,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or len(value) > max_chars:
        raise RunInputError("run_input_text_invalid")
    result = sanitize_text(value)
    if not isinstance(result, str) or (not allow_empty and not result.strip()):
        raise RunInputError("run_input_text_invalid")
    if len(result) > max_chars:
        raise RunInputError("run_input_text_invalid")
    return result


def canonicalize_questions(
    values: object,
    *,
    sanitize_text: Callable[[object], str],
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values or len(values) > MAX_RUN_QUESTION_BATCH:
        raise RunInputError("run_input_question_invalid")
    canonical: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise RunInputError("run_input_question_invalid")
        question = sanitize_run_input_text(
            value.get("question"),
            sanitize_text=sanitize_text,
            max_chars=MAX_RUN_ANSWER_CHARS,
        )
        header = sanitize_run_input_text(
            value.get("header"),
            sanitize_text=sanitize_text,
            max_chars=128,
        )
        options_value = value.get("options")
        if (
            not isinstance(options_value, list)
            or len(options_value) > MAX_RUN_QUESTION_OPTIONS
        ):
            raise RunInputError("run_input_question_invalid")
        options: list[dict[str, str]] = []
        seen_labels: set[str] = set()
        for option in options_value:
            if not isinstance(option, dict):
                raise RunInputError("run_input_question_invalid")
            label = sanitize_run_input_text(
                option.get("label"),
                sanitize_text=sanitize_text,
                max_chars=256,
            )
            description = sanitize_run_input_text(
                option.get("description"),
                sanitize_text=sanitize_text,
                max_chars=2_000,
                allow_empty=True,
            )
            if label in seen_labels:
                raise RunInputError("run_input_question_invalid")
            seen_labels.add(label)
            options.append({"label": label, "description": description})
        multi_select = value.get("multiSelect")
        if not isinstance(multi_select, bool) or question in seen_questions:
            raise RunInputError("run_input_question_invalid")
        seen_questions.add(question)
        # Explicit projection prevents SDK tool arguments from becoming public fields.
        canonical.append(
            {
                "question": question,
                "header": header,
                "options": options,
                "multiSelect": multi_select,
            }
        )
    return canonical


def canonicalize_answers(
    values: object,
    *,
    questions: list[dict[str, Any]],
    sanitize_text: Callable[[object], str],
) -> dict[str, str | list[str]]:
    if not isinstance(values, dict) or not questions:
        raise RunInputError("run_input_answers_invalid")
    by_question = {str(item["question"]): item for item in questions}
    if set(values) != set(by_question):
        raise RunInputError("run_input_answers_invalid")
    canonical: dict[str, str | list[str]] = {}
    for question_text, answer in values.items():
        question = by_question[question_text]
        labels = {str(option["label"]) for option in question["options"]}
        if isinstance(answer, str):
            safe_answer = sanitize_run_input_text(
                answer,
                sanitize_text=sanitize_text,
                max_chars=MAX_RUN_ANSWER_CHARS,
            )
            canonical[question_text] = safe_answer
            continue
        if (
            not isinstance(answer, list)
            or not question["multiSelect"]
            or not answer
            or len(answer) > len(labels)
        ):
            raise RunInputError("run_input_answers_invalid")
        selected: list[str] = []
        for item in answer:
            safe_item = sanitize_run_input_text(
                item,
                sanitize_text=sanitize_text,
                max_chars=256,
            )
            if safe_item not in labels or safe_item in selected:
                raise RunInputError("run_input_answers_invalid")
            selected.append(safe_item)
        canonical[question_text] = selected
    return canonical


def public_question(
    questions: object,
    *,
    sanitize_text: Callable[[object], str],
) -> list[dict[str, Any]]:
    """Reapply the public question shape and text sanitizer on read."""

    try:
        return canonicalize_questions(questions, sanitize_text=sanitize_text)
    except RunInputError:
        return []
