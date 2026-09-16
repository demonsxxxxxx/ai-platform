"""Bounded, complete-turn source chunks for conversation checkpoints."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.context.domain.conversation_authority import canonical_message


class CompleteSourceTurnStream:
    def __init__(self) -> None:
        self._current: list[dict[str, Any]] = []

    def add_page(self, rows: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
        completed: list[list[dict[str, Any]]] = []
        for row in rows:
            canonical_message(row)
            if row["role"] == "user":
                if self._current:
                    completed.append(self._current)
                self._current = [dict(row)]
            elif not self._current:
                raise ValueError("conversation_source_turn_invalid")
            else:
                self._current.append(dict(row))
        return completed

    def finish(self) -> list[dict[str, Any]] | None:
        current, self._current = self._current, []
        return current or None


def complete_source_turns(rows: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    stream = CompleteSourceTurnStream()
    turns = stream.add_page(rows)
    final = stream.finish()
    if final:
        turns.append(final)
    return turns


def checkpoint_source_text(summary: str | None, turns: Sequence[Sequence[Mapping[str, Any]]]) -> str:
    if not turns or any(not turn or turn[0]["role"] != "user" for turn in turns):
        raise ValueError("conversation_checkpoint_turn_invalid")
    source = {"previous_checkpoint_summary": summary,
              "conversation_turns": [[{"role": row["role"], "content": row["content"]}
                                      for row in turn] for turn in turns]}
    return json.dumps(source, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
