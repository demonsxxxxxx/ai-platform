"""Application service for durable Run-scoped text and question inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.runs.domain.inputs import (
    MAX_RUN_ANSWER_CHARS,
    RunInputClosed,
    RunInputConflict,
    RunInputError,
    canonicalize_answers,
    canonicalize_questions,
    normalize_input_id,
    normalize_question_id,
    public_question,
    sanitize_run_input_text,
)


class RunInputsPersistence(Protocol):
    async def get_owner_session(
        self, conn: Any, *, tenant_id: str, user_id: str, session_id: str
    ) -> dict[str, Any] | None: ...

    async def list_owner_session_runs(
        self, conn: Any, *, tenant_id: str, user_id: str, session_id: str,
        before_run_id: str | None, limit: int,
    ) -> list[dict[str, Any]]: ...

    async def get_owner_run(
        self, conn: Any, *, tenant_id: str, user_id: str, run_id: str, for_update: bool = False
    ) -> dict[str, Any] | None: ...

    async def get_current_attempt_id(
        self, conn: Any, *, tenant_id: str, run_id: str
    ) -> str | None: ...

    async def get_session(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str
    ) -> dict[str, Any] | None: ...

    async def open_session(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str
    ) -> dict[str, Any]: ...

    async def get_input(
        self, conn: Any, *, tenant_id: str, run_id: str, input_id: str
    ) -> dict[str, Any] | None: ...

    async def insert_input(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        input_id: str,
        kind: str,
        text: str | None,
        question_id: str | None,
        answers: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def get_question(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str, question_id: str
    ) -> dict[str, Any] | None: ...

    async def publish_question(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        question_id: str,
        questions: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    async def set_question_status(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        question_id: str,
        status: str,
    ) -> bool: ...

    async def list_inputs(
        self, conn: Any, *, tenant_id: str, run_id: str
    ) -> list[dict[str, Any]]: ...

    async def list_questions(
        self, conn: Any, *, tenant_id: str, run_id: str
    ) -> list[dict[str, Any]]: ...

    async def list_queued_inputs(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        question_id: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    async def acknowledge_inputs(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        input_ids: list[str],
    ) -> None: ...

    async def seal_session(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RunInputsService:
    persistence: RunInputsPersistence
    sanitize_text: Callable[[object], str]

    async def get_projection(
        self,
        conn: Any,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        redact_public: bool = True,
    ) -> dict[str, Any] | None:
        run = await self.persistence.get_owner_run(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
        )
        if run is None:
            return None
        current_attempt_id = await self._active_attempt_id(
            conn, run=run, tenant_id=tenant_id, run_id=run_id
        )
        session = (
            await self.persistence.get_session(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=current_attempt_id,
            )
            if current_attempt_id is not None
            else None
        )
        state = str(session.get("state") or "inactive") if session else "inactive"
        input_rows = await self.persistence.list_inputs(
            conn, tenant_id=tenant_id, run_id=run_id
        )
        question_rows = await self.persistence.list_questions(
            conn, tenant_id=tenant_id, run_id=run_id
        )
        return {
            "run_id": run_id,
            "state": state,
            "inputs": [
                self._public_input(
                    row,
                    redact_public=redact_public,
                    closed=(
                        current_attempt_id is None
                        or str(row.get("attempt_id") or "") != current_attempt_id
                        or state != "open"
                    ),
                )
                for row in input_rows
            ],
            "questions": [
                self._public_question_row(
                    row,
                    close_pending=(
                        current_attempt_id is None
                        or str(row.get("attempt_id") or "") != current_attempt_id
                        or state != "open"
                    ),
                )
                for row in question_rows
            ],
        }

    async def get_session_history(
        self, conn: Any, *, tenant_id: str, user_id: str, session_id: str,
        before_run_id: str | None = None, limit: int = 20, redact_public: bool = True,
    ) -> dict[str, Any] | None:
        if await self.persistence.get_owner_session(
            conn, tenant_id=tenant_id, user_id=user_id, session_id=session_id,
        ) is None:
            return None
        limit = max(1, min(limit, 100))
        rows = await self.persistence.list_owner_session_runs(
            conn, tenant_id=tenant_id, user_id=user_id, session_id=session_id,
            before_run_id=before_run_id, limit=limit + 1,
        )
        projections = []
        for row in rows[:limit]:
            projection = await self.get_projection(
                conn, tenant_id=tenant_id, user_id=user_id, run_id=str(row["id"]),
                redact_public=redact_public,
            )
            if projection is not None:
                projections.append(projection)
        has_more = len(rows) > limit
        return {"session_id": session_id, "runs": projections, "has_more": has_more,
                "next_before_run_id": str(rows[limit - 1]["id"]) if has_more else None}

    async def submit(
        self,
        conn: Any,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        input_id: str,
        text: str | None = None,
        question_id: str | None = None,
        answers: dict[str, str | list[str] | dict[str, str]] | None = None,
        redact_public: bool = True,
    ) -> dict[str, str] | None:
        input_sanitizer = self.sanitize_text if redact_public else lambda value: value
        run = await self.persistence.get_owner_run(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
            for_update=True,
        )
        if run is None:
            return None
        is_text = text is not None and question_id is None and answers is None
        is_answer = text is None and question_id is not None and answers is not None
        if not (is_text or is_answer):
            raise RunInputError("run_input_shape_invalid")
        input_id = normalize_input_id(input_id)
        existing = await self.persistence.get_input(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            input_id=input_id,
        )
        if existing is not None:
            await self._require_same_submission(
                conn,
                existing=existing,
                sanitize_text=input_sanitizer,
                text=text,
                question_id=question_id,
                answers=answers,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            return {"input_id": input_id, "status": str(existing["status"])}

        attempt_id = await self._active_attempt_id(
            conn, run=run, tenant_id=tenant_id, run_id=run_id
        )
        if attempt_id is None:
            raise RunInputClosed()
        session = await self.persistence.get_session(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        if session is None or str(session.get("state") or "") != "open":
            raise RunInputClosed()

        kind = "text" if is_text else "answer"
        stored_text = None
        stored_question_id = None
        stored_answers: dict[str, Any] = {}
        if kind == "text":
            stored_text = sanitize_run_input_text(
                text,
                sanitize_text=input_sanitizer,
            )
        else:
            assert question_id is not None and answers is not None
            stored_question_id = normalize_question_id(question_id)
            question = await self.persistence.get_question(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                question_id=stored_question_id,
            )
            if question is None:
                raise RunInputConflict("run_input_question_not_found")
            canonical_questions = self._canonical_stored_questions(question.get("questions"))
            stored_answers = canonicalize_answers(
                answers,
                questions=canonical_questions,
                sanitize_text=input_sanitizer,
            )
            if str(question.get("status") or "") != "pending":
                raise RunInputConflict("run_input_question_already_answered")

        row = await self.persistence.insert_input(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            input_id=input_id,
            kind=kind,
            text=stored_text,
            question_id=stored_question_id,
            answers=stored_answers,
        )
        if stored_question_id is not None:
            changed = await self.persistence.set_question_status(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                question_id=stored_question_id,
                status="answered",
            )
            if not changed:
                raise RunInputConflict("run_input_question_already_answered")
        return {"input_id": input_id, "status": str(row["status"])}

    async def callback(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run: dict[str, Any],
        run_id: str,
        attempt_id: str,
        operation: str,
        question_id: str | None = None,
        questions: list[dict[str, Any]] | None = None,
        input_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if await self._active_attempt_id(
            conn, run=run, tenant_id=tenant_id, run_id=run_id
        ) != attempt_id:
            raise RunInputClosed()

        if operation == "open":
            session = await self.persistence.open_session(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
            )
            return {"state": str(session["state"]), "inputs": []}

        session = await self.persistence.get_session(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        if session is None:
            raise RunInputClosed()
        state = str(session.get("state") or "")

        if operation == "question":
            if state != "open":
                raise RunInputClosed()
            assert question_id is not None and questions is not None
            question_id = normalize_question_id(question_id)
            canonical_questions = canonicalize_questions(
                questions,
                sanitize_text=self.sanitize_text,
            )
            question = await self.persistence.publish_question(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                question_id=question_id,
                questions=canonical_questions,
            )
            if question.get("questions") != canonical_questions:
                raise RunInputConflict("run_input_question_id_conflict")
            return {
                "state": state,
                "inputs": [],
                "question": self._public_question_row(question),
            }

        if operation == "poll":
            if state != "open":
                return {"state": state, "inputs": []}
            question_id = (
                normalize_question_id(question_id)
                if question_id is not None
                else None
            )
            rows = await self.persistence.list_queued_inputs(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                question_id=question_id,
            )
            return {"state": state, "inputs": [self._public_input(row, redact_public=False) for row in rows]}

        if operation == "ack":
            assert input_ids is not None
            await self.persistence.acknowledge_inputs(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                input_ids=[normalize_input_id(value) for value in input_ids],
            )
            return {"state": state, "inputs": []}

        if operation == "settle":
            if state == "sealed":
                return {"state": state, "inputs": []}
            queued_text = await self.persistence.list_queued_inputs(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                kind="text",
                limit=1,
            )
            if queued_text:
                return {
                    "state": "open",
                    "inputs": [self._public_input(queued_text[0], redact_public=False)],
                }
            session = await self.persistence.seal_session(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
            )
            return {"state": str(session["state"]), "inputs": []}

        raise RunInputConflict("run_input_callback_operation_invalid")

    async def _active_attempt_id(
        self,
        conn: Any,
        *,
        run: dict[str, Any],
        tenant_id: str,
        run_id: str,
    ) -> str | None:
        if (
            str(run.get("status") or "").lower() != "running"
            or run.get("cancel_requested_at") is not None
        ):
            return None
        return await self.persistence.get_current_attempt_id(
            conn, tenant_id=tenant_id, run_id=run_id
        )

    async def _require_same_submission(
        self,
        conn: Any,
        *,
        existing: dict[str, Any],
        sanitize_text: Callable[[object], str],
        text: str | None,
        question_id: str | None,
        answers: dict[str, str | list[str] | dict[str, str]] | None,
        tenant_id: str,
        run_id: str,
    ) -> None:
        expected_kind = "text" if text is not None else "answer"
        if str(existing.get("kind") or "") != expected_kind:
            raise RunInputConflict()
        if expected_kind == "text":
            safe_text = sanitize_run_input_text(
                text,
                sanitize_text=sanitize_text,
            )
            if existing.get("text") != safe_text:
                raise RunInputConflict()
            return
        if question_id is None or answers is None:
            raise RunInputConflict()
        question_id = normalize_question_id(question_id)
        if str(existing.get("question_id") or "") != question_id:
            raise RunInputConflict()
        question = await self.persistence.get_question(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=str(existing.get("attempt_id") or ""),
            question_id=question_id,
        )
        if question is None:
            raise RunInputConflict()
        canonical_questions = self._canonical_stored_questions(question.get("questions"))
        safe_answers = canonicalize_answers(
            answers,
            questions=canonical_questions,
            sanitize_text=sanitize_text,
        )
        if existing.get("answers") != safe_answers:
            raise RunInputConflict()

    def _canonical_stored_questions(self, value: object) -> list[dict[str, Any]]:
        return public_question(value, sanitize_text=self.sanitize_text)

    def _public_input(
        self,
        row: dict[str, Any],
        *,
        closed: bool = False,
        redact_public: bool = True,
    ) -> dict[str, Any]:
        sanitizer = self.sanitize_text if redact_public else lambda value: value
        kind = str(row.get("kind") or "")
        text = row.get("text")
        safe_text = (
            sanitize_run_input_text(
                text,
                sanitize_text=sanitizer,
                allow_empty=True,
            )
            if isinstance(text, str)
            else None
        )
        raw_answers = row.get("answers")
        answers: dict[str, Any] = {}
        if isinstance(raw_answers, dict):
            for key, value in raw_answers.items():
                safe_key = sanitizer(key) if isinstance(key, str) else None
                if not isinstance(safe_key, str):
                    continue
                if isinstance(value, str):
                    answers[safe_key] = sanitize_run_input_text(
                        value,
                        sanitize_text=sanitizer,
                        max_chars=MAX_RUN_ANSWER_CHARS,
                        allow_empty=True,
                    )
                elif isinstance(value, dict) and set(value) == {"text"}:
                    answers[safe_key] = {"text": sanitize_run_input_text(
                        value["text"], sanitize_text=sanitizer,
                        max_chars=MAX_RUN_ANSWER_CHARS, allow_empty=True,
                    )}
                elif isinstance(value, list):
                    answers[safe_key] = [
                        sanitize_run_input_text(
                            item,
                            sanitize_text=sanitizer,
                            max_chars=MAX_RUN_ANSWER_CHARS,
                            allow_empty=True,
                        )
                        for item in value
                        if isinstance(item, str)
                    ]
        question_id = row.get("question_id")
        return {
            "input_id": str(row.get("input_id") or ""),
            "kind": kind,
            "text": safe_text,
            "question_id": str(question_id) if question_id is not None else None,
            "answers": answers,
            "status": (
                "closed"
                if closed and str(row.get("status") or "queued") == "queued"
                else str(row.get("status") or "queued")
            ),
            "created_at": row.get("created_at"),
        }

    def _public_question_row(
        self,
        row: dict[str, Any],
        *,
        close_pending: bool = False,
    ) -> dict[str, Any]:
        question_status = str(row.get("status") or "pending")
        if close_pending and question_status in {"pending", "answered"}:
            question_status = "closed"
        return {
            "question_id": str(row.get("question_id") or ""),
            "questions": public_question(
                row.get("questions"),
                sanitize_text=self.sanitize_text,
            ),
            "status": question_status,
            "created_at": row.get("created_at"),
        }
