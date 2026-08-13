from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping


class SkillInvocationEvidenceBinder:
    """Fail closed while binding SDK Skill hooks to one exact run attempt."""

    def __init__(
        self,
        *,
        allowed_skill_names: Iterable[str],
        project_record: Callable[[Mapping[str, str]], dict[str, object]],
    ) -> None:
        self._allowed_skill_names = frozenset(allowed_skill_names)
        self._project_record = project_record
        self._records: list[dict[str, object]] = []
        self._invocation_states: dict[tuple[str, str], str] = {}
        self._rejected = False
        self._lock = asyncio.Lock()

    @property
    def records(self) -> list[dict[str, object]]:
        return [dict(record) for record in self._records]

    def _reject(self) -> bool:
        self._rejected = True
        self._records.clear()
        self._invocation_states.clear()
        return False

    async def bind(self, raw: dict[str, str]) -> bool:
        async with self._lock:
            if self._rejected:
                return False
            try:
                skill_name = raw.get("canonical_identity")
                tool_call_id = raw.get("tool_call_id")
                lifecycle_phase = raw.get("lifecycle_phase")
                if (
                    raw.get("capability_kind") != "skill"
                    or not isinstance(skill_name, str)
                    or skill_name not in self._allowed_skill_names
                    or not isinstance(tool_call_id, str)
                    or not isinstance(lifecycle_phase, str)
                ):
                    return self._reject()
                invocation_key = (skill_name, tool_call_id)
                current_phase = self._invocation_states.get(invocation_key)
                if lifecycle_phase == "invocation_requested":
                    if current_phase is not None:
                        return self._reject()
                elif current_phase != "invocation_requested":
                    return self._reject()
                record = self._project_record(raw)
                if not isinstance(record, dict):
                    return self._reject()
            except (AttributeError, ValueError):
                return self._reject()
            self._records.append(dict(record))
            self._invocation_states[invocation_key] = (
                "invocation_requested"
                if lifecycle_phase == "invocation_requested"
                else "terminal"
            )
            return True
