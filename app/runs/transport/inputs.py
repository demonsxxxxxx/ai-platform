"""HTTP envelopes for Run-owned execution inputs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class RunInputSubmissionRequest(BaseModel):
    """HTTP boundary for one idempotent Run input submission."""

    model_config = ConfigDict(extra="forbid")

    input_id: UUID
    text: str | None = Field(default=None, max_length=16_000)
    question_id: str | None = Field(default=None, min_length=1, max_length=128)
    answers: dict[str, str | list[str]] | None = None

    @model_validator(mode="after")
    def exactly_one_input_kind(self) -> "RunInputSubmissionRequest":
        is_text = self.text is not None and self.question_id is None and self.answers is None
        is_answer = self.text is None and self.question_id is not None and self.answers is not None
        if not (is_text or is_answer):
            raise ValueError("run_input_shape_invalid")
        return self


class RunInputQuestionOptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2_000)


class RunInputQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=16_000)
    header: str = Field(min_length=1, max_length=128)
    options: list[RunInputQuestionOptionRequest] = Field(max_length=8)
    multiSelect: StrictBool


class RunInputsCallbackRequest(BaseModel):
    """Private, bounded HTTP envelope for one existing fenced callback."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["open", "question", "poll", "ack", "settle"]
    run_id: str = Field(min_length=1, max_length=256)
    attempt_id: str = Field(min_length=1, max_length=256)
    callback_token_id: str = Field(min_length=1, max_length=512)
    question_id: str | None = Field(default=None, min_length=1, max_length=128)
    questions: list[RunInputQuestionRequest] | None = Field(default=None, max_length=4)
    input_ids: list[UUID] | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def operation_shape(self) -> "RunInputsCallbackRequest":
        optional_fields = (self.question_id, self.questions, self.input_ids)
        if self.operation in {"open", "settle"} and any(
            value is not None for value in optional_fields
        ):
            raise ValueError("run_input_callback_shape_invalid")
        if self.operation == "question" and (
            self.question_id is None or not self.questions or self.input_ids is not None
        ):
            raise ValueError("run_input_callback_shape_invalid")
        if self.operation == "poll" and (
            self.questions is not None or self.input_ids is not None
        ):
            raise ValueError("run_input_callback_shape_invalid")
        if self.operation == "ack" and (
            not self.input_ids
            or self.question_id is not None
            or self.questions is not None
            or len(set(self.input_ids)) != len(self.input_ids)
        ):
            raise ValueError("run_input_callback_shape_invalid")
        return self
