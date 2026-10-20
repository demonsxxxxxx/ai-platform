"""Pure policy for one bounded follow-up-question completion."""

from __future__ import annotations

import json
import re
from typing import Sequence

FOLLOW_UP_SUGGESTION_LIMIT = 3
FOLLOW_UP_QUESTION_MAX_CHARS = 60
FOLLOW_UP_TRANSCRIPT_MAX_CHARS = 4000
FOLLOW_UP_TURN_MAX_CHARS = 1200
FOLLOW_UP_MAX_OUTPUT_TOKENS = 256
FOLLOW_UP_PROMPT_CACHE_KEY = "ai-platform.chat.follow-up-suggestions.v1"

_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*$")
_NUMBERING = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)、])\s*")
_ASSISTANT_ROLE = "assistant"
_USER_ROLE = "user"

_FOLLOW_UP_INSTRUCTION = (
    "你是对话助手。下面给出用户与助手最近的公开对话。"
    f"请只输出一个 JSON 字符串数组，包含至多 {FOLLOW_UP_SUGGESTION_LIMIT} 条用户接下来可能想直接发送的问题，"
    f"每条都是简体中文、不超过 {FOLLOW_UP_QUESTION_MAX_CHARS} 个字、可直接作为用户消息发送。"
    "不要输出解释、编号、Markdown 或代码块之外的内容；无法给出时输出 []。"
)


class FollowUpSuggestionError(ValueError):
    """Stable failure for an unusable follow-up completion request or answer."""


def bounded_turn_text(text: str) -> str:
    """Trim one public turn without cutting a UTF-8 character in half."""

    collapsed = " ".join(text.split())
    return collapsed[:FOLLOW_UP_TURN_MAX_CHARS]


def build_follow_up_messages(turns: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    """Build one bounded, tool-free completion input from public turns only."""

    lines: list[str] = []
    used = 0
    for role, text in reversed(list(turns)):
        if role not in {_USER_ROLE, _ASSISTANT_ROLE}:
            raise FollowUpSuggestionError("follow_up_turn_role_invalid")
        normalized = bounded_turn_text(text)
        if not normalized:
            continue
        line = f"{'用户' if role == _USER_ROLE else '助手'}：{normalized}"
        if used + len(line) > FOLLOW_UP_TRANSCRIPT_MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        raise FollowUpSuggestionError("follow_up_transcript_empty")
    lines.reverse()
    return [
        {"role": "system", "content": _FOLLOW_UP_INSTRUCTION},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _candidate_strings(raw: str) -> list[object]:
    stripped = "\n".join(line for line in raw.splitlines() if not _FENCE.match(line)).strip()
    if not stripped:
        raise FollowUpSuggestionError("follow_up_answer_empty")
    try:
        payload = json.loads(stripped)
    except (TypeError, ValueError):
        return stripped.splitlines()
    if not isinstance(payload, list):
        raise FollowUpSuggestionError("follow_up_answer_invalid")
    return payload


def parse_follow_up_suggestions(raw: str) -> list[str]:
    """Accept only short, distinct, line-safe questions from the model answer."""

    suggestions: list[str] = []
    seen: set[str] = set()
    for candidate in _candidate_strings(raw):
        if not isinstance(candidate, str):
            raise FollowUpSuggestionError("follow_up_answer_invalid")
        text = _NUMBERING.sub("", candidate).strip()
        if not text:
            continue
        if len(text) > FOLLOW_UP_QUESTION_MAX_CHARS or any(
            ord(character) < 32 or ord(character) == 127 for character in text
        ):
            raise FollowUpSuggestionError("follow_up_question_invalid")
        marker = text.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        suggestions.append(text)
        if len(suggestions) == FOLLOW_UP_SUGGESTION_LIMIT:
            break
    return suggestions
