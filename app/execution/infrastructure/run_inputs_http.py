"""Authenticated callback client for Run-scoped execution inputs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Callable

import httpx

from app.execution.application.run_interaction import (
    RunInputCommand,
    RunInputSnapshot,
)


RUN_INPUT_CALLBACK_PATH = "/api/ai/runtime/callbacks/inputs"
DEFAULT_RUN_INPUT_TIMEOUT_SECONDS = 10.0
# Allow the bounded four-question/answer batch even at maximum UTF-8 width.
MAX_RUN_INPUT_REQUEST_BYTES = 1024 * 1024
MAX_RUN_INPUT_RESPONSE_BYTES = 1024 * 1024
MAX_RUN_INPUT_CALLBACK_ATTEMPTS = 3


class RunInputCallbackError(RuntimeError):
    """A bounded failure from the authenticated Run input callback."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.retryable = retryable


class RunInputCallbackClient:
    """HTTP implementation of the Execution domain Run interaction contract."""

    def __init__(
        self,
        *,
        callback_url: str,
        callback_token: str,
        callback_token_id: str,
        run_id: str,
        attempt_id: str,
        timeout_seconds: float = DEFAULT_RUN_INPUT_TIMEOUT_SECONDS,
        max_attempts: int = MAX_RUN_INPUT_CALLBACK_ATTEMPTS,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        if not callback_url or not callback_token or not callback_token_id:
            raise ValueError("run_input_callback_configuration_invalid")
        if not run_id or not attempt_id:
            raise ValueError("run_input_run_identity_invalid")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise ValueError("run_input_timeout_invalid")
        if not 0.1 <= float(timeout_seconds) <= 30.0:
            raise ValueError("run_input_timeout_invalid")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError("run_input_attempts_invalid")
        self._callback_url = callback_url
        self._callback_token = callback_token
        self._callback_token_id = callback_token_id
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._timeout_seconds = float(timeout_seconds)
        self._max_attempts = max_attempts
        self._client_factory = client_factory or httpx.AsyncClient

    async def open(self) -> RunInputSnapshot:
        return await self._post("open")

    async def publish_question(
        self,
        *,
        question_id: str,
        questions: Sequence[Mapping[str, object]],
    ) -> RunInputSnapshot:
        return await self._post(
            "question",
            {"question_id": question_id, "questions": list(questions)},
        )

    async def poll(self, *, question_id: str | None = None) -> RunInputSnapshot:
        values = {"question_id": question_id} if question_id is not None else {}
        return await self._post("poll", values)

    async def acknowledge(self, *, input_ids: Sequence[str]) -> RunInputSnapshot:
        return await self._post("ack", {"input_ids": list(input_ids)})

    async def settle(self) -> RunInputSnapshot:
        return await self._post("settle")

    async def _post(self, op: str, fields: Mapping[str, object] | None = None) -> RunInputSnapshot:
        payload: dict[str, object] = {
            "operation": op,
            "run_id": self._run_id,
            "attempt_id": self._attempt_id,
            "callback_token_id": self._callback_token_id,
        }
        if fields:
            payload.update(fields)
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise RunInputCallbackError("run_input_request_invalid") from exc
        if len(encoded) > MAX_RUN_INPUT_REQUEST_BYTES:
            raise RunInputCallbackError("run_input_request_too_large")

        last_error: RunInputCallbackError | None = None
        for attempt in range(self._max_attempts):
            try:
                async with self._client_factory(
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        self._callback_url,
                        content=encoded,
                        headers={
                            "Content-Type": "application/json",
                            "X-AI-Platform-Callback-Token": self._callback_token,
                        },
                    ) as response:
                        if response.status_code >= 500:
                            raise RunInputCallbackError(
                                "run_input_callback_unavailable", retryable=True
                            )
                        if response.status_code >= 400:
                            raise RunInputCallbackError("run_input_callback_rejected")
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_RUN_INPUT_RESPONSE_BYTES:
                                raise RunInputCallbackError(
                                    "run_input_response_too_large"
                                )
                            chunks.append(chunk)
                body = json.loads(b"".join(chunks))
                return self._snapshot(body)
            except RunInputCallbackError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self._max_attempts:
                    raise
            except httpx.TimeoutException as exc:
                last_error = RunInputCallbackError(
                    "run_input_callback_timeout", retryable=True
                )
                if attempt + 1 >= self._max_attempts:
                    raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = RunInputCallbackError(
                    "run_input_callback_unavailable", retryable=True
                )
                if attempt + 1 >= self._max_attempts:
                    raise last_error from exc
            except (TypeError, ValueError, UnicodeDecodeError) as exc:
                raise RunInputCallbackError("run_input_response_invalid") from exc
            await asyncio.sleep(0.05 * (2**attempt))
        raise last_error or RunInputCallbackError("run_input_callback_unavailable")

    @staticmethod
    def _snapshot(value: object) -> RunInputSnapshot:
        if not isinstance(value, dict):
            raise RunInputCallbackError("run_input_response_invalid")
        state = value.get("state")
        if state not in {"open", "sealed", "inactive"}:
            raise RunInputCallbackError("run_input_response_invalid")
        raw_inputs = value.get("inputs")
        if not isinstance(raw_inputs, list) or len(raw_inputs) > 256:
            raise RunInputCallbackError("run_input_response_invalid")
        inputs = tuple(RunInputCallbackClient._command(item) for item in raw_inputs)
        question = value.get("question")
        questions = question.get("questions") if isinstance(question, dict) else None
        if questions is not None and (
            not isinstance(questions, list) or not all(isinstance(item, dict) for item in questions)
        ):
            raise RunInputCallbackError("run_input_response_invalid")
        return RunInputSnapshot(
            state=state, inputs=inputs,
            questions=tuple(questions) if questions is not None else None,
        )  # type: ignore[arg-type]

    @staticmethod
    def _command(value: object) -> RunInputCommand:
        if not isinstance(value, dict):
            raise RunInputCallbackError("run_input_response_invalid")
        input_id = value.get("input_id")
        kind = value.get("kind")
        if not isinstance(input_id, str) or not input_id or len(input_id) > 64:
            raise RunInputCallbackError("run_input_response_invalid")
        if kind == "text":
            text = value.get("text")
            if not isinstance(text, str) or not text or len(text) > 16_000:
                raise RunInputCallbackError("run_input_response_invalid")
            return RunInputCommand(input_id=input_id, kind="text", text=text)
        if kind != "answer":
            raise RunInputCallbackError("run_input_response_invalid")
        question_id = value.get("question_id")
        answers = value.get("answers")
        if not isinstance(question_id, str) or not question_id or len(question_id) > 128:
            raise RunInputCallbackError("run_input_response_invalid")
        if not isinstance(answers, dict) or len(answers) > 4:
            raise RunInputCallbackError("run_input_response_invalid")
        normalized_answers: dict[str, str | list[str]] = {}
        for question, answer in answers.items():
            if not isinstance(question, str) or not question or len(question) > 16_000:
                raise RunInputCallbackError("run_input_response_invalid")
            if isinstance(answer, str) and len(answer) <= 16_000:
                normalized_answers[question] = answer
            elif isinstance(answer, list) and len(answer) <= 8 and all(
                isinstance(item, str) and len(item) <= 256 for item in answer
            ):
                normalized_answers[question] = list(answer)
            else:
                raise RunInputCallbackError("run_input_response_invalid")
        return RunInputCommand(
            input_id=input_id,
            kind="answer",
            question_id=question_id,
            answers=normalized_answers,
        )
