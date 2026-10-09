"""Translate Run input commands into one interactive Claude SDK session."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, Callable

from app.execution.application.run_interaction import (
    RunInputCommand,
    RunInteractionProtocol,
)
from app.platform.public_payload import sanitize_public_text


QUESTION_POLL_INTERVAL_SECONDS = 0.25


class ClaudeRunInteractionError(RuntimeError):
    """A bounded failure while continuing or closing an interactive Run."""


class ClaudeRunInteractionActor:
    def __init__(
        self,
        port: RunInteractionProtocol,
        *,
        run_id: str,
        attempt_id: str,
        sanitize_text: Callable[[object], str] = sanitize_public_text,
    ) -> None:
        if not run_id or not attempt_id:
            raise ValueError("run_interaction_identity_invalid")
        self._port = port
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._input_stream_finished = asyncio.Event()
        self._closed = asyncio.Event()
        self._question_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._question_results: dict[str, dict[str, Any]] = {}
        self._question_inputs: dict[str, str] = {}
        self._opened = False
        self._sanitize_text = sanitize_text

    async def open(self) -> None:
        snapshot = await self._port.open()
        if snapshot.state != "open":
            raise ClaudeRunInteractionError("run_input_session_not_open")
        self._opened = True

    async def initial_prompt_stream(
        self,
        initial_message: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if not self._opened:
            raise ClaudeRunInteractionError("run_input_session_not_open")
        yield dict(initial_message)
        await self._input_stream_finished.wait()

    async def wait_for_input_stream_end(self) -> None:
        await self._input_stream_finished.wait()

    async def settle_at_result(self) -> RunInputCommand | None:
        snapshot = await self._port.settle()
        if snapshot.state == "sealed":
            if snapshot.inputs:
                raise ClaudeRunInteractionError("run_input_settle_invalid")
            self._input_stream_finished.set()
            return None
        if snapshot.state != "open":
            raise ClaudeRunInteractionError("run_input_session_inactive")
        text_commands = [item for item in snapshot.inputs if item.kind == "text"]
        if len(text_commands) != 1 or text_commands[0].text is None:
            raise ClaudeRunInteractionError("run_input_settle_invalid")
        return text_commands[0]

    async def apply_text_at_result(
        self,
        client: Any,
        command: RunInputCommand,
        *,
        session_id: str | None,
    ) -> None:
        if command.kind != "text" or not command.text:
            raise ClaudeRunInteractionError("run_input_text_invalid")
        # A successful public query is never repeated. An uncertain ACK only
        # retries this idempotent receipt until it succeeds or the Run stops.
        await client.query(command.text, session_id=session_id or "default")
        await self._acknowledge_until_confirmed(command.input_id)

    async def resolve_native_question(
        self,
        *,
        tool_call_id: str,
        tool_input: object,
    ) -> dict[str, Any]:
        if not tool_call_id:
            raise ClaudeRunInteractionError("run_question_identity_invalid")
        cached = self._question_results.get(tool_call_id)
        if cached is not None:
            return {key: value for key, value in cached.items()}
        task = self._question_tasks.get(tool_call_id)
        if task is None:
            native_questions = _native_questions(tool_input)
            questions = _project_questions(native_questions, self._sanitize_text)
            question_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ai-platform:run-question:{self._run_id}:{self._attempt_id}:{tool_call_id}",
                )
            )
            task = asyncio.create_task(
                self._publish_and_wait(tool_call_id=tool_call_id, question_id=question_id, questions=questions, native_questions=native_questions)
            )
            self._question_tasks[tool_call_id] = task
        updated_input = await asyncio.shield(task)
        self._question_results[tool_call_id] = updated_input
        return {key: value for key, value in updated_input.items()}

    async def acknowledge_native_question(self, tool_call_id: str) -> None:
        input_id = self._question_inputs.get(tool_call_id)
        if input_id is not None:
            await self._acknowledge_until_confirmed(input_id)

    async def cancel(self) -> None:
        first_close = not self._closed.is_set()
        self._closed.set()
        self._input_stream_finished.set()
        active = [task for task in self._question_tasks.values() if not task.done()]
        if first_close:
            for task in active:
                task.cancel()
        if active:
            # Closing waiters may themselves be interrupted. Preserve the
            # question finalizers and let a later close await the same work.
            await asyncio.shield(asyncio.gather(*active, return_exceptions=True))

    async def close(self) -> None:
        await self.cancel()

    async def _publish_and_wait(
        self,
        *,
        tool_call_id: str,
        question_id: str,
        questions: list[dict[str, Any]],
        native_questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = await self._port.publish_question(
            question_id=question_id,
            questions=questions,
        )
        if snapshot.state != "open":
            raise ClaudeRunInteractionError("run_question_session_inactive")
        while not self._closed.is_set():
            snapshot = await self._port.poll(question_id=question_id)
            if snapshot.state != "open":
                raise ClaudeRunInteractionError("run_question_session_inactive")
            for command in snapshot.inputs:
                if (
                    command.kind != "answer"
                    or command.question_id != question_id
                ):
                    continue
                if command.answers is None:
                    raise ClaudeRunInteractionError("run_question_answers_invalid")
                answers = _native_answers(command.answers, native_questions)
                # The SDK post-tool hook confirms that the native question
                # consumed its answer. A denied/stopped question stays closed
                # and unprocessed rather than reporting a premature receipt.
                self._question_inputs[tool_call_id] = command.input_id
                return {"questions": native_questions, "answers": answers}
            try:
                await asyncio.wait_for(
                    self._closed.wait(), timeout=QUESTION_POLL_INTERVAL_SECONDS
                )
            except TimeoutError:
                pass
        raise asyncio.CancelledError

    async def _acknowledge_until_confirmed(self, input_id: str) -> None:
        delay = 0.1
        while not self._closed.is_set():
            try:
                snapshot = await self._port.acknowledge(input_ids=[input_id])
                if snapshot.state != "open":
                    raise ClaudeRunInteractionError("run_input_ack_session_inactive")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry only uncertain ACKs.
                if not bool(getattr(exc, "retryable", False)):
                    raise ClaudeRunInteractionError("run_input_ack_failed") from exc
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=delay)
                except TimeoutError:
                    delay = min(delay * 2, 2.0)
        raise asyncio.CancelledError


def _native_questions(tool_input: object) -> list[dict[str, Any]]:
    # This interaction's raw identity stays local to the attempt rather than
    # entering the new input tables/callback projection. Existing SDK transcript
    # persistence retains its own contract. Ordinal keys survive display redaction.
    if not isinstance(tool_input, Mapping) or not isinstance(tool_input.get("questions"), list):
        raise ClaudeRunInteractionError("run_question_input_invalid")
    try:
        questions = [{
            "question": item["question"], "header": item.get("header") or "Question",
            "multiSelect": item.get("multiSelect", False),
            "options": [{"label": option["label"], "description": option.get("description") or ""}
                        for option in item["options"]],
        } for item in tool_input["questions"]]
        if (not 1 <= len(questions) <= 4
            or any(not isinstance(item["question"], str) for item in questions)
            or len({item["question"] for item in questions}) != len(questions)):
            raise ValueError("invalid_questions")
        for item in questions:
            labels = [option["label"] for option in item["options"]]
            if any(not isinstance(label, str) for label in labels) or len(set(labels)) != len(labels):
                raise ValueError("invalid_options")
        return questions
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ClaudeRunInteractionError("run_question_input_invalid") from exc


def _project_questions(
    questions: list[dict[str, Any]], sanitize_text: Callable[[object], str],
) -> list[dict[str, Any]]:
    return [{
        "key": f"q{index}", "question": sanitize_text(item["question"]) or f"Question {index + 1}",
        "header": sanitize_text(item["header"]) or "Question", "multiSelect": item["multiSelect"],
        "options": [{"key": f"o{option_index}", "label": sanitize_text(option["label"]) or f"Option {option_index + 1}",
                     "description": sanitize_text(option["description"])}
                    for option_index, option in enumerate(item["options"])],
    } for index, item in enumerate(questions)]


def _native_answers(
    answers: Mapping[str, object], questions: list[dict[str, Any]],
) -> dict[str, str | list[str]]:
    if set(answers) != {f"q{index}" for index in range(len(questions))}:
        raise ClaudeRunInteractionError("run_question_answers_invalid")
    result: dict[str, str | list[str]] = {}
    for index, question in enumerate(questions):
        answer = answers[f"q{index}"]
        options = {f"o{i}": option["label"] for i, option in enumerate(question["options"])}
        if isinstance(answer, dict) and set(answer) == {"text"} and isinstance(answer["text"], str) and answer["text"].strip():
            result[question["question"]] = answer["text"]
        elif isinstance(answer, str) and not question["multiSelect"] and answer in options:
            result[question["question"]] = options[answer]
        elif (isinstance(answer, list) and question["multiSelect"] and answer
              and all(isinstance(key, str) and key in options for key in answer)
              and len(set(answer)) == len(answer)):
            result[question["question"]] = [options[key] for key in answer]
        else:
            raise ClaudeRunInteractionError("run_question_answers_invalid")
    return result
