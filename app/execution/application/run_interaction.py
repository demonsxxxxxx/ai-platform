"""Execution port for additional Run inputs during one attempt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


RunInputState = Literal["open", "sealed", "inactive"]
RunInputKind = Literal["text", "answer"]


@dataclass(frozen=True)
class RunInputCommand:
    input_id: str
    kind: RunInputKind
    text: str | None = None
    question_id: str | None = None
    answers: Mapping[str, str | list[str]] | None = None


@dataclass(frozen=True)
class RunInputSnapshot:
    state: RunInputState
    inputs: tuple[RunInputCommand, ...] = ()
    questions: tuple[Mapping[str, object], ...] | None = None


class RunInteractionProtocol(Protocol):
    """Run-scoped callbacks used by an execution adapter."""

    async def open(self) -> RunInputSnapshot: ...

    async def publish_question(
        self,
        *,
        question_id: str,
        questions: Sequence[Mapping[str, object]],
    ) -> RunInputSnapshot: ...

    async def poll(self, *, question_id: str | None = None) -> RunInputSnapshot: ...

    async def acknowledge(self, *, input_ids: Sequence[str]) -> RunInputSnapshot: ...

    async def settle(self) -> RunInputSnapshot: ...
