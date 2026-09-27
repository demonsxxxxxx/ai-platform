"""Validate Claude SDK text framing before the separate public-answer gate."""

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES


_KNOWN_STOP_REASONS = frozenset(
    {
        "end_turn",
        "max_tokens",
        "stop_sequence",
        "tool_use",
        "pause_turn",
        "refusal",
        "model_context_window_exceeded",
    }
)


def is_known_stop_reason(value: object) -> bool:
    return value is None or (
        isinstance(value, str) and value in _KNOWN_STOP_REASONS
    )


def provider_message_identity(message_id: object = None) -> str | None:
    if not isinstance(message_id, str) or not message_id:
        return None
    return message_id


def _is_hashable(value: object) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


_KNOWN_BLOCK_TYPES = frozenset(
    {
        "text",
        "thinking",
        "redacted_thinking",
        "tool_use",
        "server_tool_use",
        "mcp_tool_use",
        "tool_result",
        "server_tool_result",
        "advisor_tool_result",
        "mcp_tool_result",
        "tool_search_tool_result",
        "web_search_tool_result",
        "web_fetch_tool_result",
        "code_execution_tool_result",
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
    }
)
_NON_TEXT_DELTA_TYPES = {
    "thinking": frozenset({"thinking_delta", "signature_delta"}),
    "redacted_thinking": frozenset(),
    "tool_use": frozenset({"input_json_delta"}),
    "server_tool_use": frozenset({"input_json_delta"}),
    "mcp_tool_use": frozenset({"input_json_delta"}),
    "tool_result": frozenset(),
    "server_tool_result": frozenset(),
    "advisor_tool_result": frozenset(),
    "mcp_tool_result": frozenset(),
    "tool_search_tool_result": frozenset(),
    "web_search_tool_result": frozenset(),
    "web_fetch_tool_result": frozenset(),
    "code_execution_tool_result": frozenset(),
    "bash_code_execution_tool_result": frozenset(),
    "text_editor_code_execution_tool_result": frozenset(),
}


@dataclass
class _AnswerSource:
    key: object
    message_key: object
    parent_tool_use_id: str | None = None
    coverage: str = ""
    coverage_known: bool = False
    coverage_length: int = 0
    coverage_digest: str = ""
    coverage_truncated: bool = False
    coverage_hasher: Any = field(default=None, repr=False)
    raw_coverage: str = ""
    raw_coverage_known: bool = False
    raw_coverage_length: int = 0
    raw_coverage_digest: str = ""
    raw_coverage_truncated: bool = False
    raw_coverage_hasher: Any = field(default=None, repr=False)
    raw_open: bool = False
    raw_closed: bool = False
    published_chars: int = 0
    sequence: int = 0
    raw_delta_count: int = 0
    typed_body_replay_pending: bool = False
    typed_body_replay_offset: int = 0
    typed_body_replay_length: int = 0
    typed_body_replay_expected_length: int = 0
    typed_body_replay_expected_digest: str = ""
    typed_body_replay_hasher: Any = field(default=None, repr=False)


@dataclass
class _RawBlockSource:
    identity: tuple[object, ...]
    content_type: str


# These bounds cap replay/reconciliation evidence, not the cumulative public text.
_MAX_RECONCILIATION_BINDINGS = 128
_MAX_RECONCILIATION_COVERAGE_BYTES = RUN_RESULT_MAX_BYTES
_MAX_RETAINED_SOURCES = _MAX_RECONCILIATION_BINDINGS


class AssistantAnswerTimeline:
    """Reconcile text observations only inside one SDK source identity.

    Raw deltas are published immediately.  Typed Assistant observations and the
    terminal Result may extend the same source, but they cannot replace an
    acknowledged prefix or make an unrelated source look like a suffix.  The
    caller passes private SDK text through the public answer gate after this
    class returns it.
    """

    def __init__(self) -> None:
        self._sources: list[_AnswerSource] = []
        self._sources_by_key: dict[object, _AnswerSource] = {}
        self._next_source_sequence = 0
        self._last_published_message_key: object = None
        self._last_published_sequence = -1
        self._current: _AnswerSource | None = None
        self._result_seen = False
        self._result_identity: object = None
        self._result_length: int | None = None
        self._result_digest: str | None = None
        self._result_suffix = ""
        self._rendered_text = ""
        self._rendered_length = 0
        self._rendered_hasher = hashlib.sha256()
        self._terminal_identity: object = None
        # SDK delivery is sequential; exact replay suppression is limited to
        # this recent window. An evicted identity is treated as a new event.
        self._recent_raw_observations: OrderedDict[str, tuple[object, ...]] = (
            OrderedDict()
        )
        self._recent_assistant_observations: OrderedDict[tuple[object, object], tuple[object, ...]] = OrderedDict()
        self._answer_binding_retired = False
        self._disabled = False

    @property
    def disabled(self) -> bool:
        """Whether an identity or lifecycle conflict disabled new output."""

        return self._disabled

    @property
    def text(self) -> str:
        return self._rendered_text + self._result_suffix

    def fail_closed(self) -> None:
        """Preserve already observed text and reject every later observation."""

        self._disabled = True

    @property
    def latest_binding(self) -> tuple[object, object, str | None] | None:
        if self._answer_binding_retired:
            return None
        source = self._current
        if source is None:
            return None
        return source.key, source.message_key, source.parent_tool_use_id

    def retire_answer_binding(self) -> None:
        """Retire the current answer source at a tool-only turn boundary."""

        had_answer_source = self._current is not None or bool(self._sources)
        source = self._current
        if source is not None and source.raw_open:
            source.raw_open = False
            source.raw_closed = True
            self._seal_coverage(source)
            self._seal_raw_coverage(source)
        self._current = None
        self._answer_binding_retired = had_answer_source

    @property
    def has_answer_source(self) -> bool:
        return self._next_source_sequence > 0

    @staticmethod
    def _text_size(text: str) -> int:
        return len(text.encode("utf-8"))

    @staticmethod
    def _bounded_prefix(text: str, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        return encoded[:max_bytes].decode("utf-8", "ignore")

    @staticmethod
    def _coverage_matches(source: _AnswerSource, text: str) -> bool:
        return (
            source.coverage_known
            and len(text) == source.coverage_length
            and hashlib.sha256(text.encode("utf-8")).hexdigest()
            == source.coverage_digest
        )

    def _publication_prefix(
        self,
        source: _AnswerSource | None,
        message_identity: object,
        suffix: str,
    ) -> str:
        if not suffix or (source is not None and source.published_chars):
            return ""
        if (
            self._last_published_sequence >= 0
            and self._last_published_message_key != message_identity
        ):
            return "\n\n"
        return ""

    def _can_create_source(
        self,
        message_identity: object,
    ) -> bool:
        self._prune_closed_sources()
        return sum(
            item.message_key == message_identity for item in self._sources
        ) < _MAX_RECONCILIATION_BINDINGS

    def _prune_closed_sources(self) -> None:
        while len(self._sources) >= _MAX_RETAINED_SOURCES:
            candidate = next(
                (
                    item
                    for item in self._sources
                    if item is not self._current
                    and not item.raw_open
                    and (item.raw_closed or item.coverage_known)
                ),
                None,
            )
            if candidate is None:
                return
            self._sources.remove(candidate)
            self._sources_by_key.pop(candidate.key, None)

    def _raw_observation_replay_status(
        self,
        observed_identity: object,
        *,
        source_identity: object,
        message_identity: object,
        parent_tool_use_id: str | None,
        body: str,
    ) -> bool | None:
        if not isinstance(observed_identity, str) or not observed_identity:
            return None
        previous = self._recent_raw_observations.get(observed_identity)
        if previous is None:
            return None
        self._recent_raw_observations.move_to_end(observed_identity)
        return previous == (
            source_identity,
            message_identity,
            parent_tool_use_id,
            len(body),
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )

    def _remember_raw_observation(
        self,
        observed_identity: object,
        *,
        source_identity: object,
        message_identity: object,
        parent_tool_use_id: str | None,
        body: str,
    ) -> None:
        if not isinstance(observed_identity, str) or not observed_identity:
            return
        self._recent_raw_observations[observed_identity] = (
            source_identity,
            message_identity,
            parent_tool_use_id,
            len(body),
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )
        self._recent_raw_observations.move_to_end(observed_identity)
        while len(self._recent_raw_observations) > _MAX_RECONCILIATION_BINDINGS:
            self._recent_raw_observations.popitem(last=False)

    def _clear_typed_body_replay(self, source: _AnswerSource) -> None:
        source.typed_body_replay_pending = False
        source.typed_body_replay_offset = 0
        source.typed_body_replay_length = 0
        source.typed_body_replay_expected_length = 0
        source.typed_body_replay_expected_digest = ""
        source.typed_body_replay_hasher = None

    def _record_typed_body_replay_target(
        self,
        source: _AnswerSource,
        candidate: str,
    ) -> None:
        if not self._coverage_matches(source, candidate):
            return
        if not source.raw_open:
            self._clear_typed_body_replay(source)
            return
        raw_length = (
            source.raw_coverage_length if source.raw_coverage_known else 0
        )
        if len(candidate) <= raw_length:
            self._clear_typed_body_replay(source)
            return
        replay_suffix = candidate[raw_length:]
        source.typed_body_replay_pending = True
        source.typed_body_replay_offset = raw_length
        source.typed_body_replay_length = raw_length
        source.typed_body_replay_expected_length = len(replay_suffix)
        source.typed_body_replay_expected_digest = hashlib.sha256(
            replay_suffix.encode("utf-8")
        ).hexdigest()
        source.typed_body_replay_hasher = hashlib.sha256()

    def _reconcile_typed_body_replay(
        self,
        source: _AnswerSource,
        text: str,
    ) -> tuple[bool, str | None]:
        if not source.typed_body_replay_pending:
            return False, None
        target_length = source.coverage_length
        remaining_length = target_length - source.typed_body_replay_length
        if remaining_length < 0:
            return True, None
        if source.coverage_truncated:
            replay_prefix = text[:remaining_length]
            replay_hasher = source.typed_body_replay_hasher
            if replay_hasher is None:
                replay_hasher = hashlib.sha256()
                source.typed_body_replay_hasher = replay_hasher
            replay_hasher.update(replay_prefix.encode("utf-8"))
            source.typed_body_replay_length += len(replay_prefix)
            if source.typed_body_replay_length < target_length:
                return True, ""
            if (
                replay_hasher.hexdigest()
                != source.typed_body_replay_expected_digest
            ):
                return True, None
            suffix = text[remaining_length:]
            self._clear_typed_body_replay(source)
            return True, suffix
        remaining = source.coverage[source.typed_body_replay_offset :]
        if remaining.startswith(text):
            source.typed_body_replay_offset += len(text)
            source.typed_body_replay_length = source.typed_body_replay_offset
            if source.typed_body_replay_offset == target_length:
                self._clear_typed_body_replay(source)
            return True, ""
        if text.startswith(remaining):
            suffix = text[len(remaining) :]
            self._clear_typed_body_replay(source)
            return True, suffix
        return True, None

    def establish_raw_source(
        self,
        source_identity: object,
        *,
        message_identity: object,
        parent_tool_use_id: str | None = None,
    ) -> bool:
        """Bind an explicit text block before its first raw delta arrives."""

        if self._disabled:
            return False
        source_was_known = (
            _is_hashable(source_identity)
            and source_identity in self._sources_by_key
        )
        source = self._bind_source(
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            allow_create=True,
        )
        if source is None or source.raw_closed:
            self._fail()
            return False
        source.raw_open = True
        if not source_was_known:
            self._answer_binding_retired = False
        self._current = source
        return True

    def accept_delta(
        self,
        text: str,
        *,
        source_identity: object = None,
        message_identity: object = None,
        parent_tool_use_id: str | None = None,
        observed_identity: object = None,
    ) -> str:
        if self._disabled:
            return ""
        if (
            not isinstance(text, str)
            or not text
            or source_identity is None
            or message_identity is None
            or not _is_hashable(source_identity)
            or not _is_hashable(message_identity)
        ):
            self._fail()
            return ""
        if observed_identity is not None and (
            not isinstance(observed_identity, str) or not observed_identity
        ):
            self._fail()
            return ""
        replay_status = self._raw_observation_replay_status(
            observed_identity,
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            body=text,
        )
        if replay_status is True:
            self._current = self._sources_by_key.get(source_identity)
            return ""
        if replay_status is False:
            self._fail()
            return ""
        try:
            existing_source = self._sources_by_key.get(source_identity)
        except TypeError:
            existing_source = None
        if existing_source is None and not self._can_create_source(message_identity):
            self._fail()
            return ""
        source = self._bind_source(
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            allow_create=True,
        )
        if source is None or source.raw_closed:
            self._fail()
            return ""
        source.raw_open = True
        if existing_source is None:
            self._answer_binding_retired = False
        source.raw_delta_count += 1
        replay_handled, replay_suffix = self._reconcile_typed_body_replay(
            source, text
        )
        if replay_handled:
            if replay_suffix is None:
                self._fail()
                return ""
            self._append_raw_coverage(source, text)
            self._remember_raw_observation(
                observed_identity,
                source_identity=source_identity,
                message_identity=message_identity,
                parent_tool_use_id=parent_tool_use_id,
                body=text,
            )
            if not replay_suffix:
                self._current = source
                return ""
            self._append_coverage(source, replay_suffix)
            self._current = source
            return self._publish(source, replay_suffix)
        suffix = self._reconcile(source, text)
        if suffix is None:
            self._fail()
            return ""
        self._append_raw_coverage(source, text)
        self._remember_raw_observation(
            observed_identity,
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            body=text,
        )
        self._current = source
        return self._publish(source, suffix)

    def close_raw_source(
        self,
        source_identity: object,
    ) -> None:
        if self._disabled:
            return
        try:
            source = self._sources_by_key.get(source_identity)
        except TypeError:
            source = None
        if source is None:
            self._fail()
            return
        if source.raw_closed:
            return
        if source.typed_body_replay_pending and source.raw_delta_count:
            self._fail()
            return
        source.raw_open = False
        source.raw_closed = True
        self._clear_typed_body_replay(source)
        self._seal_coverage(source)
        self._seal_raw_coverage(source)
        self._current = source

    def accept_assistant(
        self,
        text: str | None,
        *,
        source_identity: object = None,
        message_identity: object = None,
        parent_tool_use_id: str | None = None,
        observed_identity: object = None,
        observation_scope: object = None,
    ) -> str:
        if self._disabled:
            return ""
        if text is None:
            return ""
        if (
            not isinstance(text, str)
            or source_identity is None
            or message_identity is None
            or not _is_hashable(source_identity)
            or not _is_hashable(message_identity)
        ):
            self._fail()
            return ""
        observation_key = (observed_identity, source_identity)
        observation = (
            observation_scope, message_identity, parent_tool_use_id,
            len(text), hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        if isinstance(observed_identity, str) and observed_identity:
            previous = self._recent_assistant_observations.get(observation_key)
            if previous is not None:
                if previous[1:] != observation[1:]:
                    self._fail()
                return ""
            if any(
                key[0] == observed_identity
                and (observation_scope is None or value[0] != observation_scope)
                for key, value in self._recent_assistant_observations.items()
            ):
                self._fail()
                return ""
        try:
            existing_source = self._sources_by_key.get(source_identity)
        except TypeError:
            existing_source = None
        if existing_source is None and not self._can_create_source(message_identity):
            self._fail()
            return ""
        source = self._bind_source(
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            allow_create=True,
        )
        if source is None:
            self._fail()
            return ""
        if existing_source is None:
            self._answer_binding_retired = False
        suffix = self._reconcile_assistant(source, text)
        if suffix is None or (suffix and self._has_later_published(source)):
            self._fail()
            return ""
        if self._current is None or source.sequence >= self._current.sequence:
            self._current = source
        published = self._publish(source, suffix)
        if isinstance(observed_identity, str) and observed_identity:
            self._recent_assistant_observations[observation_key] = observation
            while len(self._recent_assistant_observations) > _MAX_RECONCILIATION_BINDINGS:
                self._recent_assistant_observations.popitem(last=False)
        self._record_typed_body_replay_target(source, text)
        if not source.raw_open:
            self._seal_coverage(source)
        return published

    def validate_assistant_observations(
        self,
        observations: list[tuple[str, object, object, str | None]],
    ) -> bool:
        """Validate typed source coverage before any suffix is published."""

        if self._disabled:
            return False
        projected: dict[int, str] = {}
        previous_source_sequence = -1
        for text, source_identity, message_identity, parent_tool_use_id in observations:
            if (
                not isinstance(text, str)
                or source_identity is None
                or message_identity is None
                or not _is_hashable(source_identity)
                or not _is_hashable(message_identity)
            ):
                self._fail()
                return False
            try:
                source = self._sources_by_key.get(source_identity)
            except TypeError:
                source = None
            if (
                source is None
                or source.message_key != message_identity
                or source.parent_tool_use_id != parent_tool_use_id
            ):
                self._fail()
                return False
            source_sequence = source.sequence
            if source_sequence < previous_source_sequence:
                self._fail()
                return False
            previous_source_sequence = source_sequence
            if source.coverage_truncated:
                if self._coverage_matches(source, text):
                    projected[source.sequence] = text
                    continue
                if (
                    not source.raw_coverage_known
                    or len(text) < source.coverage_length
                    or hashlib.sha256(
                        text[: source.coverage_length].encode("utf-8")
                    ).hexdigest()
                    != source.coverage_digest
                    or self._has_later_published(source)
                ):
                    self._fail()
                    return False
                projected[source.sequence] = text
                continue
            if source.sequence in projected:
                coverage = projected[source.sequence]
            elif source.coverage_known:
                if source.coverage:
                    coverage = source.coverage
                    if not text.startswith(coverage):
                        self._fail()
                        return False
                else:
                    if (
                        len(text) < source.coverage_length
                        or hashlib.sha256(
                            text[: source.coverage_length].encode("utf-8")
                        ).hexdigest()
                        != source.coverage_digest
                    ):
                        self._fail()
                        return False
                    coverage = text[: source.coverage_length]
                projected[source.sequence] = text
            else:
                if source.raw_open:
                    coverage = text
                    projected[source.sequence] = text
                elif text:
                    self._fail()
                    return False
                else:
                    coverage = ""
                    projected[source.sequence] = text
            suffix = text[len(coverage) :]
            if suffix and self._has_later_published(source):
                self._fail()
                return False
            projected[source.sequence] = text
        return True

    def validate_result_identity(
        self,
        result_identity: object,
        terminal_reason: object,
    ) -> bool:
        if self._disabled:
            return False
        if (
            not isinstance(result_identity, str)
            or not result_identity
            or (
                terminal_reason is not None
                and (
                    not isinstance(terminal_reason, str)
                    or not is_known_stop_reason(terminal_reason)
                )
            )
        ):
            self._fail()
            return False
        if (
            self._terminal_identity is not None
            and result_identity != self._terminal_identity
        ):
            self._fail()
            return False
        self._terminal_identity = result_identity
        return True

    def accept_result_only(
        self,
        text: str,
        *,
        result_identity: object,
        terminal_reason: str | None,
    ) -> str:
        if self.has_answer_source or self._current is not None:
            self._fail()
            return ""
        if not isinstance(text, str):
            self._fail()
            return ""
        if not self.validate_result_identity(result_identity, terminal_reason):
            return ""
        source_identity = ("result", result_identity)
        message_identity = ("result", result_identity)
        source = self._bind_source(
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=None,
            allow_create=True,
        )
        if source is None:
            self._fail()
            return ""
        return self.accept_result(
            text,
            source_identity=source_identity,
            message_identity=message_identity,
            result_identity=result_identity,
            terminal_reason=terminal_reason,
        )

    def accept_result(
        self,
        text: str,
        *,
        source_identity: object = None,
        message_identity: object = None,
        parent_tool_use_id: str | None = None,
        result_identity: object = None,
        terminal_reason: str | None = None,
    ) -> str:
        if self._disabled:
            return ""
        if self._answer_binding_retired:
            self._fail()
            return ""
        if not isinstance(text, str):
            self._fail()
            return ""
        if not self.validate_result_identity(result_identity, terminal_reason):
            return ""
        if not text:
            return ""
        if (
            source_identity is None
            or message_identity is None
            or result_identity is None
            or not _is_hashable(source_identity)
            or not _is_hashable(message_identity)
            or not _is_hashable(result_identity)
        ):
            self._fail()
            return ""
        source = self._bind_source(
            source_identity=source_identity,
            message_identity=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            allow_create=False,
        )
        if source is None:
            self._fail()
            return ""
        result_length = len(text)
        result_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if self._result_seen:
            if (
                result_identity != self._result_identity
                or result_length != self._result_length
                or result_digest != self._result_digest
            ):
                self._fail()
            return ""
        self._result_seen = True
        self._result_identity = result_identity
        self._result_length = result_length
        self._result_digest = result_digest
        rendered = self.text
        if source.coverage_truncated:
            if (
                not source.coverage_known
                or len(text) < source.coverage_length
                or hashlib.sha256(
                    text[: source.coverage_length].encode("utf-8")
                ).hexdigest()
                != source.coverage_digest
            ):
                self._fail()
                return ""
            suffix = text[source.coverage_length :]
            if suffix:
                self._append_coverage(source, suffix)
        elif text.startswith(rendered):
            suffix = text[len(rendered) :]
        elif source.coverage_known and not self._has_later_published(source):
            if source.coverage:
                if not text.startswith(source.coverage):
                    self._fail()
                    return ""
                suffix = text[len(source.coverage) :]
            elif (
                len(text) < source.coverage_length
                or hashlib.sha256(
                    text[: source.coverage_length].encode("utf-8")
                ).hexdigest()
                != source.coverage_digest
            ):
                self._fail()
                return ""
            else:
                suffix = text[source.coverage_length :]
            if (
                not source.coverage
                and source.published_chars == 0
                and self._last_published_sequence >= 0
                and self._last_published_message_key != source.message_key
            ):
                suffix = "\n\n" + suffix
        else:
            self._fail()
            return ""
        self._result_suffix = suffix
        self._current = source
        return suffix

    def _bind_source(
        self,
        *,
        source_identity: object,
        message_identity: object,
        parent_tool_use_id: str | None,
        allow_create: bool,
    ) -> _AnswerSource | None:
        if (
            source_identity is None
            or message_identity is None
            or not _is_hashable(source_identity)
            or not _is_hashable(message_identity)
        ):
            return None
        try:
            source = self._sources_by_key.get(source_identity)
        except TypeError:
            return None
        if source is not None:
            if (
                source.message_key != message_identity
                or source.parent_tool_use_id != parent_tool_use_id
            ):
                return None
            if source.raw_open and source is not self._current:
                return None
            return source
        if not allow_create or (
            self._current is not None and self._current.raw_open
        ):
            return None
        self._prune_closed_sources()
        if len(self._sources) >= _MAX_RETAINED_SOURCES:
            return None
        if not self._can_create_source(message_identity):
            return None
        source = _AnswerSource(
            key=source_identity,
            message_key=message_identity,
            parent_tool_use_id=parent_tool_use_id,
            sequence=self._next_source_sequence,
        )
        self._next_source_sequence += 1

        self._sources.append(source)
        self._sources_by_key[source_identity] = source
        self._current = source
        return source

    def _has_later_published(self, source: _AnswerSource) -> bool:
        return self._last_published_sequence > source.sequence


    def _reconcile_assistant(
        self,
        source: _AnswerSource,
        candidate: str,
    ) -> str | None:
        if source.coverage_truncated:
            if self._coverage_matches(source, candidate):
                return ""
            if (
                not source.raw_coverage_known
                or len(candidate) < source.coverage_length
                or hashlib.sha256(
                    candidate[: source.coverage_length].encode("utf-8")
                ).hexdigest()
                != source.coverage_digest
            ):
                self._fail()
                return None
            suffix = candidate[source.coverage_length :]
            self._remember_text_coverage(source, candidate)
            return suffix
        if not source.coverage_known:
            self._remember_text_coverage(source, candidate)
            return candidate
        if source.coverage:
            if candidate.startswith(source.coverage):
                suffix = candidate[len(source.coverage) :]
                self._remember_text_coverage(source, candidate)
                if source.coverage_truncated and suffix:
                    if not source.raw_coverage_known:
                        self._fail()
                        return None
                return suffix
            if source.coverage.startswith(candidate):
                return ""
            return None
        if (
            len(candidate) < source.coverage_length
            or hashlib.sha256(
                candidate[: source.coverage_length].encode("utf-8")
            ).hexdigest()
            != source.coverage_digest
        ):
            return None
        suffix = candidate[source.coverage_length :]
        self._remember_text_coverage(source, candidate)
        if source.coverage_truncated and suffix and not source.raw_coverage_known:
            self._fail()
            return None
        return suffix

    def _reconcile(self, source: _AnswerSource, text: str) -> str | None:
        if source.coverage_truncated:
            self._append_coverage(source, text)
            return text
        candidate = source.coverage + text
        if candidate.startswith(source.coverage):
            suffix = candidate[len(source.coverage) :]
            self._append_coverage(source, text)
            return suffix
        if source.coverage.startswith(candidate):
            return ""
        return None

    def _remember_text_coverage(self, source: _AnswerSource, text: str) -> None:
        encoded = text.encode("utf-8")
        hasher = hashlib.sha256()
        hasher.update(encoded)
        source.coverage_hasher = hasher
        source.coverage_known = True
        source.coverage_length = len(text)
        source.coverage_digest = hasher.hexdigest()
        source.coverage_truncated = len(encoded) > _MAX_RECONCILIATION_COVERAGE_BYTES
        source.coverage = self._bounded_prefix(
            text, _MAX_RECONCILIATION_COVERAGE_BYTES
        )
        if source.coverage_truncated:
            source.coverage = ""

    def _append_coverage(self, source: _AnswerSource, text: str) -> None:
        if not text:
            return
        hasher = source.coverage_hasher
        if hasher is None:
            hasher = hashlib.sha256()
            source.coverage_hasher = hasher
        hasher.update(text.encode("utf-8"))
        source.coverage_known = True
        source.coverage_length += len(text)
        source.coverage_digest = hasher.hexdigest()
        if source.coverage_truncated:
            return
        remaining = _MAX_RECONCILIATION_COVERAGE_BYTES - self._text_size(
            source.coverage
        )
        if remaining <= 0:
            source.coverage = ""
            source.coverage_truncated = True
            return
        retained = self._bounded_prefix(text, remaining)
        if len(retained) != len(text):
            source.coverage = ""
            source.coverage_truncated = True
            return
        source.coverage += retained

    def _append_raw_coverage(self, source: _AnswerSource, text: str) -> None:
        if not text:
            return
        hasher = source.raw_coverage_hasher
        if hasher is None:
            hasher = hashlib.sha256()
            source.raw_coverage_hasher = hasher
        hasher.update(text.encode("utf-8"))
        source.raw_coverage_known = True
        source.raw_coverage_length += len(text)
        source.raw_coverage_digest = hasher.hexdigest()
        if source.raw_coverage_truncated:
            return
        remaining = _MAX_RECONCILIATION_COVERAGE_BYTES - self._text_size(
            source.raw_coverage
        )
        if remaining <= 0:
            source.raw_coverage = ""
            source.raw_coverage_truncated = True
            return
        retained = self._bounded_prefix(text, remaining)
        if len(retained) != len(text):
            source.raw_coverage = ""
            source.raw_coverage_truncated = True
            return
        source.raw_coverage += retained

    def _seal_coverage(self, source: _AnswerSource) -> None:
        if not source.coverage_known:
            self._remember_text_coverage(source, "")
        elif source.coverage_hasher is not None:
            source.coverage_digest = source.coverage_hasher.hexdigest()
        source.coverage = ""
        self._clear_typed_body_replay(source)

    def _seal_raw_coverage(self, source: _AnswerSource) -> None:
        if source.raw_coverage_hasher is not None:
            source.raw_coverage_digest = source.raw_coverage_hasher.hexdigest()
        source.raw_coverage = ""
        source.raw_coverage_hasher = None


    def _publish(self, source: _AnswerSource, suffix: str) -> str:
        if not suffix:
            return ""
        prefix = self._publication_prefix(source, source.message_key, suffix)
        published = prefix + suffix
        source.published_chars += len(suffix)
        self._rendered_text += published
        self._rendered_length += len(published)
        self._rendered_hasher.update(published.encode("utf-8"))
        self._last_published_message_key = source.message_key
        self._last_published_sequence = source.sequence
        return published

    def _fail(self) -> None:
        self._disabled = True



class ClaudeStreamProjector:
    """Return text deltas only while the SDK is inside an exact text block.

    The projector is deliberately a framing validator, not a turn classifier.
    Safe text can therefore continue to the public-answer gate immediately;
    thinking, tool input, and other non-text blocks are observed only so their
    deltas cannot be mistaken for Assistant prose.

    A typed ``AssistantMessage`` is not a framing boundary.  The SDK can emit
    that object before the matching raw ``content_block_stop`` event, so only
    raw stream events open and close raw stream state here.
    """

    def __init__(self) -> None:
        self._active_text_index: int | None = None
        self._ignored_block_index: int | None = None
        self._ignored_block_type: str | None = None
        self._completed_block_indexes: set[int] = set()
        self._explicit_message_open = False
        self._saw_explicit_message = False
        self._message_id: str | None = None
        self._parent_tool_use_id: str | None = None
        self._message_stop_reason: str | None = None
        self._typed_lifecycle_observed = False
        self._message_delta_seen = False
        self._message_generation = 0
        self._block_generation = 0
        self._raw_sources: list[_RawBlockSource] = []
        self._raw_text_sources: list[_RawBlockSource] = []
        self._raw_sources_by_index: dict[int, _RawBlockSource] = {}
        self._last_text_source_identity: tuple[object, ...] | None = None
        self._completed_text_source_identity: tuple[object, ...] | None = None
        self._typed_text_source_cursor = 0
        self._typed_text_source_window_count: int | None = None
        self._typed_text_source_window_sources: tuple[_RawBlockSource, ...] = ()
        self._disabled = False
        self._partial_emitted = False

    @property
    def disabled(self) -> bool:
        """Whether an unsafe or conflicting event permanently disabled output."""

        return self._disabled

    @property
    def partial_emitted(self) -> bool:
        """Whether this projector has emitted any text in its lifetime."""

        return self._partial_emitted

    @property
    def typed_lifecycle_observed(self) -> bool:
        """Whether a typed Assistant observation has been accepted."""

        return self._typed_lifecycle_observed

    @property
    def raw_lifecycle_observed(self) -> bool:
        """Whether a raw message lifecycle has been accepted."""

        return self._saw_explicit_message

    @property
    def message_id(self) -> str | None:
        return self._message_id

    @property
    def parent_tool_use_id(self) -> str | None:
        return self._parent_tool_use_id

    @property
    def text_source_identity(self) -> tuple[object, ...] | None:
        return self._last_text_source_identity

    @property
    def last_stop_reason(self) -> str | None:
        return self._message_stop_reason

    def take_completed_text_source_identity(self) -> tuple[object, ...] | None:
        identity = self._completed_text_source_identity
        self._completed_text_source_identity = None
        return identity

    def retire_text_source(self) -> None:
        """Forget a stale text source at a tool-only typed turn boundary."""

        self._last_text_source_identity = None
        self._completed_text_source_identity = None
        self._typed_text_source_window_count = None
        self._typed_text_source_window_sources = ()

    def observe_typed(
        self,
        *,
        message_id: object = None,
        uuid: object = None,
        parent_tool_use_id: object = None,
        stop_reason: object = None,
    ) -> bool:
        """Validate typed message identity without changing raw framing."""

        if self._disabled:
            return False
        typed_id = provider_message_identity(message_id)
        if (
            typed_id is None
            or not isinstance(uuid, str)
            or not uuid
        ):
            self._disable()
            return False
        if parent_tool_use_id is not None and (
            not isinstance(parent_tool_use_id, str) or not parent_tool_use_id
        ):
            self._disable()
            return False
        if stop_reason is not None and not is_known_stop_reason(stop_reason):
            self._disable()
            return False
        if stop_reason is not None:
            if self._message_stop_reason not in (None, stop_reason):
                self._disable()
                return False
            self._message_stop_reason = stop_reason
        if self._saw_explicit_message:
            if (
                not self._explicit_message_open
                or typed_id != self._message_id
                or parent_tool_use_id != self._parent_tool_use_id
            ):
                self._disable()
                return False
        self._typed_lifecycle_observed = True
        return True

    def _typed_text_source_window(
        self,
        text_source_count: object,
    ) -> tuple[_RawBlockSource, ...] | None:
        if (
            self._disabled
            or not self._is_exact_index(text_source_count)
            or text_source_count <= 0
            or not self._raw_text_sources
        ):
            self._disable()
            return None
        if (
            self._typed_text_source_window_count is not None
            and self._typed_text_source_window_count != text_source_count
        ):
            self._disable()
            return None
        if self._typed_text_source_window_count is None:
            available_sources = tuple(
                self._raw_text_sources[self._typed_text_source_cursor :]
            )
            if text_source_count > len(available_sources):
                self._disable()
                return None
            self._typed_text_source_window_sources = available_sources[:text_source_count]
            self._typed_text_source_window_count = text_source_count
        return self._typed_text_source_window_sources

    def typed_text_source_identity(
        self,
        *,
        text_source_ordinal: object,
        text_source_count: object = None,
    ) -> tuple[object, ...] | None:
        if text_source_count is None:
            text_source_count = (
                text_source_ordinal + 1
                if self._is_exact_index(text_source_ordinal)
                else None
            )
        sources = self._typed_text_source_window(text_source_count)
        if (
            sources is None
            or not self._is_exact_index(text_source_ordinal)
            or text_source_ordinal < 0
            or text_source_ordinal >= len(sources)
        ):
            self._disable()
            return None
        source_identity = sources[text_source_ordinal].identity
        if text_source_ordinal == len(sources) - 1:
            self._typed_text_source_cursor += len(sources)
            self._typed_text_source_window_count = None
            self._typed_text_source_window_sources = ()
        return source_identity

    def validate_typed_text_source_count(self, text_source_count: object) -> bool:
        """Validate a typed message's local text-source window."""

        return self._typed_text_source_window(text_source_count) is not None

    def accept(
        self,
        event: object,
        *,
        parent_tool_use_id: object = None,
    ) -> tuple[str, ...]:
        """Consume one raw event and return newly framed text immediately.

        Returned fragments remain executor-private until the caller passes
        them through the public-answer sanitization gate.
        """

        if self._disabled:
            return ()
        if parent_tool_use_id is not None and (
            not isinstance(parent_tool_use_id, str) or not parent_tool_use_id
        ):
            self._disable()
            return ()
        if self._explicit_message_open and parent_tool_use_id != self._parent_tool_use_id:
            self._disable()
            return ()
        if not isinstance(event, dict):
            self._disable()
            return ()
        event_type = event.get("type")
        if event_type == "ping":
            return ()
        if event_type == "message_start":
            self._parent_tool_use_id = parent_tool_use_id
            return self._accept_message_start(event)
        if event_type == "message_delta":
            return self._accept_message_delta(event)
        if event_type == "message_stop":
            return self._accept_message_stop(event)
        if event_type == "content_block_start":
            return self._accept_start(event)
        if event_type == "content_block_stop":
            return self._accept_stop(event)
        if event_type == "content_block_delta":
            return self._accept_delta(event)
        self._disable()
        return ()

    def close_unfinished(self) -> None:
        """Disable raw framing that never received its exact stop event."""

        if (
            self._active_text_index is not None
            or self._ignored_block_index is not None
            or self._explicit_message_open
        ):
            self._disable()

    def _accept_message_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        message = event.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        stop_reason = message.get("stop_reason") if isinstance(message, dict) else None
        if (
            not isinstance(message_id, str)
            or not message_id
            or role != "assistant"
            or stop_reason is not None
            or self._explicit_message_open
            or self._active_text_index is not None
            or self._ignored_block_index is not None
            or self._completed_block_indexes
        ):
            self._disable()
            return ()
        self._raw_sources.clear()
        self._raw_text_sources.clear()
        self._raw_sources_by_index.clear()
        self._last_text_source_identity = None
        self._completed_text_source_identity = None
        self._typed_text_source_cursor = 0
        self._typed_text_source_window_count = None
        self._typed_text_source_window_sources = ()
        self._explicit_message_open = True
        self._saw_explicit_message = True
        self._message_id = message_id
        self._message_stop_reason = None
        self._message_delta_seen = False
        self._message_generation += 1
        self._block_generation = 0
        return ()

    def _accept_message_delta(self, event: dict[str, Any]) -> tuple[str, ...]:
        if not self._explicit_message_open or self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()
            return ()
        delta = event.get("delta")
        if not isinstance(delta, dict):
            self._disable()
            return ()
        stop_reason = delta.get("stop_reason")
        if stop_reason is not None and not is_known_stop_reason(stop_reason):
            self._disable()
            return ()
        if "stop_sequence" in delta and delta["stop_sequence"] is not None and not isinstance(delta["stop_sequence"], str):
            self._disable()
            return ()
        if stop_reason is not None:
            if self._message_stop_reason not in (None, stop_reason):
                self._disable()
                return ()
            self._message_stop_reason = stop_reason
        self._message_delta_seen = True
        return ()

    def _accept_message_stop(self, event: dict[str, Any]) -> tuple[str, ...]:
        del event
        if (
            not self._explicit_message_open
            or self._active_text_index is not None
            or self._ignored_block_index is not None
            or not self._message_delta_seen
            or self._message_stop_reason is None
        ):
            self._disable()
            return ()
        self._explicit_message_open = False
        self._completed_block_indexes.clear()
        return ()

    def _accept_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        if (
            not self._explicit_message_open
            or self._active_text_index is not None
            or self._ignored_block_index is not None
        ):
            self._disable()
            return ()
        index = event.get("index")
        content_block = event.get("content_block")
        if (
            not self._is_exact_index(index)
            or not isinstance(content_block, dict)
            or (self._explicit_message_open and index in self._completed_block_indexes)
        ):
            self._disable()
            return ()
        content_type = content_block.get("type")
        if content_type not in _KNOWN_BLOCK_TYPES:
            self._disable()
            return ()
        if len(self._raw_sources) >= _MAX_RECONCILIATION_BINDINGS:
            self._disable()
            return ()
        self._block_generation += 1
        source_identity = (
            self._message_id,
            index,
            self._parent_tool_use_id,
            self._message_generation,
            self._block_generation,
        )
        source = _RawBlockSource(identity=source_identity, content_type=content_type)
        self._raw_sources.append(source)
        self._raw_sources_by_index[index] = source
        if content_type != "text":
            self._ignored_block_index = index
            self._ignored_block_type = content_type
            return ()
        self._raw_text_sources.append(source)
        self._active_text_index = index
        self._last_text_source_identity = source_identity
        return ()

    def _accept_stop(self, event: dict[str, Any]) -> tuple[str, ...]:
        index = event.get("index")
        if self._ignored_block_index is not None:
            if self._ignored_block_type is None or not self._is_ignored_index(index):
                self._disable()
                return ()
            self._ignored_block_index = None
            self._ignored_block_type = None
            if self._explicit_message_open:
                self._completed_block_indexes.add(index)
            return ()
        if not self._is_active_index(index):
            self._disable()
            return ()
        self._active_text_index = None
        self._completed_text_source_identity = self._last_text_source_identity
        if self._explicit_message_open:
            self._completed_block_indexes.add(index)
        return ()

    def _accept_delta(self, event: dict[str, Any]) -> tuple[str, ...]:
        index = event.get("index")
        delta = event.get("delta")
        if self._ignored_block_index is not None:
            if (
                self._ignored_block_type is None
                or not self._is_ignored_index(index)
                or not isinstance(delta, dict)
                or not isinstance(delta.get("type"), str)
                or delta.get("type") not in _NON_TEXT_DELTA_TYPES.get(self._ignored_block_type, ())
            ):
                self._disable()
            return ()
        if self._active_text_index is None:
            self._disable()
            return ()
        if not self._is_active_index(index) or not isinstance(delta, dict):
            self._disable()
            return ()
        if delta.get("type") != "text_delta":
            self._disable()
            return ()
        text = delta.get("text")
        if not isinstance(text, str) or not text:
            self._disable()
            return ()
        self._partial_emitted = True
        return (text,)

    def _disable(self) -> None:
        self._disabled = True
        self._active_text_index = None
        self._ignored_block_index = None
        self._ignored_block_type = None
        self._completed_block_indexes.clear()
        self._explicit_message_open = False
        self._message_delta_seen = False

    def _is_active_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._active_text_index

    def _is_ignored_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._ignored_block_index

    @staticmethod
    def _is_exact_index(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)
