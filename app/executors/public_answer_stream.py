import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass

from app.execution.api import public_answer_failure_reason
from app.kernel.memory_redaction import sanitizer_unstable_suffix_length


_RECOVERED_TEXT = "[content unavailable]"
_PUBLIC_ANSWER_COALESCE_SECONDS = 0.05
_PUBLIC_ANSWER_MAX_CODEPOINTS = 8_192


@dataclass(frozen=True, slots=True)
class PublicAnswerFinish:
    """One terminal, already-sanitized public answer projection."""

    chunks: tuple[str, ...]
    final_text: str


class PublicAnswerCoalescer:
    """Coalesce adjacent safe answer text before event identity allocation."""

    def __init__(
        self,
        emit: Callable[[str], Awaitable[bool]],
        *,
        window_seconds: float = _PUBLIC_ANSWER_COALESCE_SECONDS,
    ) -> None:
        if window_seconds < 0:
            raise ValueError("answer coalescing window must be non-negative")
        self._emit = emit
        self._window_seconds = window_seconds
        self._pending = ""
        self._source_identity: object = None
        self._has_source = False
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._error: BaseException | None = None
        self._emission_unacknowledged = False

    async def push(self, text: object, *, source_identity: object = None) -> bool:
        if not isinstance(text, str) or not text:
            return True
        async with self._lock:
            self._raise_if_failed()
            if self._closed:
                return False
            if (
                self._pending
                and self._has_source
                and source_identity != self._source_identity
                and not await self._flush_locked()
            ):
                return False
            remaining = text
            while remaining:
                if not self._pending:
                    self._source_identity = source_identity
                    self._has_source = True
                available = _PUBLIC_ANSWER_MAX_CODEPOINTS - len(self._pending)
                self._pending += remaining[:available]
                remaining = remaining[available:]
                if len(self._pending) == _PUBLIC_ANSWER_MAX_CODEPOINTS:
                    if not await self._flush_locked():
                        return False
            if self._pending and self._flush_task is None:
                self._flush_task = asyncio.create_task(self._flush_after_window())
            return True

    async def flush(self) -> bool:
        async with self._lock:
            self._raise_if_failed()
            return await self._flush_locked()

    async def close(self, *, flush: bool) -> bool:
        self._closed = True
        task = self._flush_task
        self._flush_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            self._raise_if_failed()
            if self._emission_unacknowledged:
                return False
            if flush:
                return await self._flush_locked()
            self._pending = ""
            self._has_source = False
            return True

    async def _flush_after_window(self) -> None:
        try:
            await asyncio.sleep(self._window_seconds)
            await self.flush()
        except asyncio.CancelledError as error:
            task = asyncio.current_task()
            if task is None or task.cancelling() == 0:
                self._error = error
        except Exception as error:  # noqa: BLE001 - re-raised at the owner barrier.
            self._error = error
        finally:
            if self._flush_task is asyncio.current_task():
                self._flush_task = None

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    async def _flush_locked(self) -> bool:
        if not self._pending:
            self._has_source = False
            return True
        text = self._pending
        self._pending = ""
        self._has_source = False
        try:
            accepted = await self._emit(text)
        except asyncio.CancelledError:
            self._emission_unacknowledged = True
            raise
        if not accepted:
            self._closed = True
        return accepted


class PublicAnswerStreamGate:
    """Project bounded answer text without exposing exact executor-private tokens."""

    def __init__(
        self,
        *,
        private_replacements: Mapping[str, str],
        sanitizer: Callable[[str], str],
        public_replacements: Mapping[str, str] | None = None,
        max_private_token_chars: int = 512,
    ) -> None:
        self._sanitizer = sanitizer
        self._max_private_token_chars = max_private_token_chars
        self._replacements: dict[str, str] = {}
        self._public_replacements: dict[str, str] = {}
        self._public_values: set[str] = set()
        self._replacement_pattern: re.Pattern[str] | None = None
        self._tokens: tuple[str, ...] = ()
        self._private_tokens: tuple[str, ...] = ()
        self._pending = ""
        self._pending_source_spans: list[tuple[object, int]] = []
        self._published_suffix = ""
        self._public_answer_chunks: list[str] = []
        self._accepted_text = False
        self._active_capability_invocations: set[tuple[str, str, str]] = set()
        self._failed = False
        self._failure_reason: str | None = None
        self._private_token_exposed = False
        self._projection_omissions = 0
        self._finished = False
        if (
            not callable(sanitizer)
            or not isinstance(max_private_token_chars, int)
            or isinstance(max_private_token_chars, bool)
            or max_private_token_chars < 2
        ):
            self._fail("invalid_configuration")
        else:
            try:
                self._public_replacements = dict(public_replacements or {})
            except (TypeError, ValueError):
                self._fail("invalid_configuration")
                return
            self._add_replacements(private_replacements)

    @property
    def failed(self) -> bool:
        """Return whether text safety can no longer be proven for this run."""

        return self._failed

    @property
    def failure_reason(self) -> str | None:
        """Return the first public-safe projection fault reason."""

        return self._failure_reason

    @property
    def private_token_exposed(self) -> bool:
        """Retain actual disclosure independently of the first projection fault."""

        return self._private_token_exposed

    @property
    def projection_omissions(self) -> int:
        """Return the number of answer fragments omitted after local projection faults."""

        return self._projection_omissions

    def accept(self, text: object) -> tuple[str, ...]:
        """Accept one ordered Assistant fragment and return immediately safe chunks."""

        if self._failed or self._finished:
            return ()
        if not isinstance(text, str):
            self._fail("invalid_input")
            return ()
        if not text:
            return ()
        self._accepted_text = True
        raw_candidate = self._pending + text
        projected_candidate = self._project(raw_candidate, recoverable=True)
        if projected_candidate is None:
            return self._emit(_RECOVERED_TEXT)
        raw_hold = max(
            (
                0
                if any(token in raw_candidate for token in self._tokens)
                else self._private_prefix_chars(raw_candidate)
            ),
            self._replacement_prefix_chars(raw_candidate, self._public_values),
        )
        if raw_hold:
            if raw_hold > self._max_private_token_chars:
                self._omit("sanitizer_bound_exceeded")
                return self._emit(_RECOVERED_TEXT)
            stable_candidate = self._project(
                raw_candidate[:-raw_hold], recoverable=True
            )
            if stable_candidate is None:
                return self._emit(_RECOVERED_TEXT)
            self._pending = raw_candidate[-raw_hold:]
            emitted = self._project_across_publication_boundary(
                stable_candidate, recoverable=True
            )
            if emitted is None:
                self._pending = ""
                emitted = _RECOVERED_TEXT
            return self._emit(emitted)
        candidate = projected_candidate
        held_chars = self._private_prefix_chars(candidate)
        if held_chars > self._max_private_token_chars:
            self._omit("private_token_prefix_overflow")
            return self._emit(_RECOVERED_TEXT)
        emitted = candidate[:-held_chars] if held_chars else candidate
        self._pending = candidate[-held_chars:] if held_chars else ""
        emitted = self._project_across_publication_boundary(
            emitted, recoverable=True
        )
        if emitted is None:
            self._pending = ""
            emitted = _RECOVERED_TEXT
        return self._emit(emitted)

    def accept_routed(
        self,
        text: object,
        *,
        source_identity: object,
    ) -> tuple[tuple[object, str], ...]:
        """Accept gated text while retaining source spans for the bounded suffix."""

        if not isinstance(text, str):
            chunks = self.accept(text)
            self._pending_source_spans.clear()
            return tuple((source_identity, chunk) for chunk in chunks if chunk)
        if not text:
            return ()

        if self._pending and not self._pending_source_spans:
            # Preserve the historical mixed accept/accept_routed behavior by
            # assigning untagged retained text to the current source.
            self._pending_source_spans = [(source_identity, len(self._pending))]
        elif sum(length for _owner, length in self._pending_source_spans) != len(
            self._pending
        ):
            self._fail("upstream_projection_failed")
            return ()

        if not self._pending or all(
            owner == source_identity
            for owner, _length in self._pending_source_spans
        ):
            chunks = self.accept(text)
            self._pending_source_spans = (
                [(source_identity, len(self._pending))] if self._pending else []
            )
            return tuple((source_identity, chunk) for chunk in chunks if chunk)

        prior_pending = self._pending
        combined_spans = list(self._pending_source_spans)
        self._append_span(combined_spans, source_identity, len(text))
        expected_segments, matched_private_token = self._known_token_segments(
            prior_pending + text,
            combined_spans,
        )
        published_count = len(self._public_answer_chunks)
        published_suffix = self._published_suffix
        chunks = self.accept(text)
        emitted = "".join(chunks)
        pending = self._pending

        if self._failed:
            self._pending_source_spans = []
            return ()

        if matched_private_token:
            projected_candidate = "".join(value for _owner, value in expected_segments)
            emitted_count = len(projected_candidate) - len(pending)
            if (
                emitted_count >= 0
                and emitted == projected_candidate[:emitted_count]
                and pending == projected_candidate[emitted_count:]
            ):
                emitted_segments, pending_segments = self._split_source_text(
                    expected_segments,
                    emitted_count,
                )
                self._pending_source_spans = self._span_lengths(pending_segments)
                return tuple(self._route_segments(emitted, emitted_segments))

        consumed = len(prior_pending) + len(text) - len(pending)
        if (
            not matched_private_token
            and consumed >= 0
            and len(emitted) == consumed
            and emitted + pending == prior_pending + text
        ):
            emitted_spans, pending_spans = self._split_spans(
                combined_spans,
                consumed,
            )
            self._pending_source_spans = pending_spans
            return tuple(self._route_spans(emitted, emitted_spans))

        owners = {owner for owner, _length in combined_spans}
        if len(owners) == 1:
            owner = next(iter(owners))
            self._pending_source_spans = [(owner, len(pending))] if pending else []
            return ((owner, emitted),) if emitted else ()

        # The sanitizer changed a projection spanning several sources, so its
        # output cannot be assigned to a provider part with known ownership.
        del self._public_answer_chunks[published_count:]
        self._published_suffix = published_suffix
        self._fail("upstream_projection_failed")
        return ()

    def finish_routed(
        self,
        *,
        final_text: object,
        release: bool,
        fallback_source_identity: object = None,
    ) -> tuple[PublicAnswerFinish, tuple[tuple[object, str], ...]]:
        """Finish the gate and bind any withheld suffix to its original source."""

        spans = list(self._pending_source_spans)
        pending = self._pending
        effective_final_text = final_text
        published_count = len(self._public_answer_chunks)
        published_suffix = self._published_suffix
        published_text = self._published_text()
        if release is True and final_text == "" and self._accepted_text:
            effective_final_text = self._published_text() + pending

        finished = self.finish(final_text=effective_final_text, release=release)
        if effective_final_text != final_text:
            routed = self._route_finish_chunks(
                finished.chunks,
                spans,
                fallback_source_identity,
                expected_text=pending,
            )
            if routed is None:
                del self._public_answer_chunks[published_count:]
                self._published_suffix = published_suffix
                self._fail("upstream_projection_failed")
                finished = PublicAnswerFinish((), published_text)
                routed = ()
        else:
            routed = tuple(
                (fallback_source_identity, chunk)
                for chunk in finished.chunks
                if chunk
            )
        self._pending_source_spans = []
        return finished, routed

    @staticmethod
    def _append_span(
        spans: list[tuple[object, int]],
        owner: object,
        length: int,
    ) -> list[tuple[object, int]]:
        if length <= 0:
            return spans
        if spans and spans[-1][0] == owner:
            spans[-1] = (owner, spans[-1][1] + length)
        else:
            spans.append((owner, length))
        return spans

    def _known_token_segments(
        self,
        text: str,
        spans: list[tuple[object, int]],
    ) -> tuple[list[tuple[object, str]], bool]:
        """Replace exact known tokens while carrying each token's start owner."""

        if not self._tokens or sum(length for _owner, length in spans) != len(text):
            return [], False

        def owner_at(position: int) -> object:
            remaining = position
            for owner, length in spans:
                if remaining < length:
                    return owner
                remaining -= length
            return self._first_owner(spans, None)

        result: list[tuple[object, str]] = []
        matched = False
        index = 0
        while index < len(text):
            match = (
                self._replacement_pattern.match(text, index)
                if self._replacement_pattern is not None
                else None
            )
            token = match.group() if match is not None else None
            if token in self._public_values:
                for offset, character in enumerate(token):
                    self._append_text_segment(result, owner_at(index + offset), character)
                index += len(token)
            elif token is not None:
                matched = True
                self._append_text_segment(
                    result,
                    owner_at(index),
                    self._replacements[token],
                )
                index += len(token)
            else:
                self._append_text_segment(result, owner_at(index), text[index])
                index += 1
        return result, matched

    @staticmethod
    def _append_text_segment(
        segments: list[tuple[object, str]],
        owner: object,
        text: str,
    ) -> None:
        if not text:
            return
        if segments and segments[-1][0] == owner:
            segments[-1] = (owner, segments[-1][1] + text)
        else:
            segments.append((owner, text))

    @staticmethod
    def _split_spans(
        spans: list[tuple[object, int]],
        prefix_length: int,
    ) -> tuple[list[tuple[object, int]], list[tuple[object, int]]]:
        prefix: list[tuple[object, int]] = []
        tail: list[tuple[object, int]] = []
        remaining = max(0, prefix_length)
        for owner, length in spans:
            taken = min(length, remaining)
            PublicAnswerStreamGate._append_span(prefix, owner, taken)
            PublicAnswerStreamGate._append_span(tail, owner, length - taken)
            remaining -= taken
        return prefix, tail

    @staticmethod
    def _prefix_spans(
        spans: list[tuple[object, int]],
        prefix_length: int,
    ) -> list[tuple[object, int]]:
        prefix, _tail = PublicAnswerStreamGate._split_spans(spans, prefix_length)
        return prefix

    @staticmethod
    def _span_lengths(segments: list[tuple[object, str]]) -> list[tuple[object, int]]:
        spans: list[tuple[object, int]] = []
        for owner, value in segments:
            PublicAnswerStreamGate._append_span(spans, owner, len(value))
        return spans

    @staticmethod
    def _split_source_text(
        segments: list[tuple[object, str]],
        prefix_length: int,
    ) -> tuple[list[tuple[object, str]], list[tuple[object, str]]]:
        prefix: list[tuple[object, str]] = []
        tail: list[tuple[object, str]] = []
        remaining = max(0, prefix_length)
        for owner, value in segments:
            taken = min(len(value), remaining)
            PublicAnswerStreamGate._append_text_segment(prefix, owner, value[:taken])
            PublicAnswerStreamGate._append_text_segment(tail, owner, value[taken:])
            remaining -= taken
        return prefix, tail

    @staticmethod
    def _route_spans(
        text: str,
        spans: list[tuple[object, int]],
    ) -> list[tuple[object, str]]:
        result: list[tuple[object, str]] = []
        offset = 0
        for owner, length in spans:
            PublicAnswerStreamGate._append_text_segment(
                result,
                owner,
                text[offset : offset + length],
            )
            offset += length
        return result

    @staticmethod
    def _route_segments(
        text: str,
        segments: list[tuple[object, str]],
    ) -> list[tuple[object, str]]:
        result: list[tuple[object, str]] = []
        offset = 0
        for owner, value in segments:
            piece = text[offset : offset + len(value)]
            PublicAnswerStreamGate._append_text_segment(result, owner, piece)
            offset += len(value)
        return result

    @staticmethod
    def _first_owner(
        spans: list[tuple[object, int]],
        fallback: object,
    ) -> object:
        return spans[0][0] if spans else fallback

    @classmethod
    def _route_finish_chunks(
        cls,
        chunks: tuple[str, ...],
        spans: list[tuple[object, int]],
        fallback_owner: object,
        *,
        expected_text: str,
    ) -> tuple[tuple[object, str], ...] | None:
        text = "".join(chunks)
        if not text:
            return ()
        owners = {owner for owner, _length in spans}
        if text != expected_text:
            if len(owners) == 1:
                return ((next(iter(owners)), text),)
            if not owners:
                return ((fallback_owner, text),)
            return None
        if sum(length for _owner, length in spans) == len(text):
            return tuple(cls._route_spans(text, spans))
        if len(owners) == 1:
            return ((next(iter(owners)), text),)
        if not owners:
            return ((fallback_owner, text),)
        return None

    def seal(
        self,
        private_replacements: Mapping[str, str] | None = None,
        *,
        capability_boundary: bool = False,
        invocation_key: tuple[str, str, str],
    ) -> None:
        """Register invocation identities without suppressing Assistant text."""

        del capability_boundary
        if self._finished:
            return
        if private_replacements is not None:
            self.register_private_replacements(private_replacements)
        if (
            not isinstance(invocation_key, tuple)
            or len(invocation_key) != 3
            or any(not isinstance(value, str) or not value for value in invocation_key)
            or invocation_key in self._active_capability_invocations
        ):
            self._fail("invalid_input")
            return
        self._active_capability_invocations.add(invocation_key)

    def register_private_replacements(
        self,
        private_replacements: Mapping[str, str],
    ) -> None:
        """Learn executor-private tokens before later answer text can expose them."""

        if self._finished:
            return
        previous_tokens = set(self._tokens)
        reclassified_public_tokens = {
            token
            for token, replacement in self._public_replacements.items()
            if isinstance(private_replacements, Mapping)
            and token in private_replacements
            and private_replacements[token] != replacement
        }
        self._add_replacements(private_replacements)
        added_tokens = (set(self._tokens) - previous_tokens) | reclassified_public_tokens
        if added_tokens:
            published_text = self._published_text()
            if any(token in published_text for token in added_tokens):
                self._fail("private_token_already_published")
                return
        if self._failed:
            return
        prior_pending = self._pending
        prior_spans = list(self._pending_source_spans)
        if prior_spans and sum(length for _owner, length in prior_spans) != len(
            prior_pending
        ):
            self._fail("upstream_projection_failed")
            return
        pending = self._project(self._pending, recoverable=True)
        if pending is None:
            self._pending = ""
            self._pending_source_spans = []
            return
        if prior_spans and all(
            owner == prior_spans[0][0] for owner, _ in prior_spans
        ):
            self._pending_source_spans = [(prior_spans[0][0], len(pending))]
        elif len(pending) == len(prior_pending) and pending == prior_pending:
            self._pending_source_spans = prior_spans
        else:
            routed_pending, matched = self._known_token_segments(
                prior_pending,
                prior_spans,
            )
            routed_text = "".join(value for _owner, value in routed_pending)
            if matched and routed_text == pending:
                self._pending_source_spans = self._span_lengths(routed_pending)
            elif prior_spans:
                self._fail("upstream_projection_failed")
                return
            else:
                self._pending_source_spans = []
        self._pending = pending

    def release_after_verified_capability(
        self,
        invocation_key: tuple[str, str, str],
    ) -> bool:
        """Release exact receipt ownership without reopening a failed projection."""

        if invocation_key not in self._active_capability_invocations:
            return False
        self._active_capability_invocations.remove(invocation_key)
        return True

    def fail_closed(self) -> None:
        """Irreversibly discard retained text when an upstream projection is unsafe."""

        self._fail("upstream_projection_failed")

    def finish(self, *, final_text: object, release: bool) -> PublicAnswerFinish:
        """Finish the public body without replaying a second terminal authority."""

        if self._finished:
            return PublicAnswerFinish((), "")
        if release is not True:
            return self._discard()
        if self._private_token_exposed:
            return self._discard()
        published_text = self._published_text()
        if self._failed:
            self._pending_source_spans = []
            self._finished = True
            return PublicAnswerFinish((), published_text)
        if not isinstance(final_text, str):
            self._fail("invalid_input")
            return self._discard()
        pending = self._pending
        safe_final = self._project(final_text, recoverable=True)
        if safe_final is None:
            self._pending = ""
            self._pending_source_spans = []
            emitted = (
                self._project_across_publication_boundary(
                    pending, recoverable=True
                )
                if pending
                else None
            )
            if emitted is None and not published_text:
                emitted = _RECOVERED_TEXT
            chunks = self._emit(emitted or "")
            self._finished = True
            return PublicAnswerFinish(chunks, self._published_text())
        if self._accepted_text and safe_final.startswith(published_text):
            candidate = safe_final[len(published_text) :]
        elif self._accepted_text:
            candidate = self._pending
        else:
            candidate = safe_final
        emitted = self._project_across_publication_boundary(
            candidate, recoverable=True
        )
        if emitted is None:
            emitted = _RECOVERED_TEXT
        chunks = self._emit(emitted)
        self._pending = ""
        self._pending_source_spans = []
        self._finished = True
        return PublicAnswerFinish(chunks, self._published_text())

    def _add_replacements(self, replacements: Mapping[str, str]) -> None:
        try:
            items = tuple(replacements.items())
        except (AttributeError, TypeError):
            self._fail("private_replacement_invalid")
            return
        for token, replacement in items:
            if (
                not isinstance(token, str)
                or not token
                or len(token) > self._max_private_token_chars
                or not isinstance(replacement, str)
                or not replacement
                or token == replacement
                or (
                    token in self._replacements
                    and self._replacements[token] != replacement
                )
            ):
                self._fail("private_replacement_invalid")
                return
            self._replacements[token] = replacement
        self._tokens = tuple(
            sorted(self._replacements, key=lambda value: (-len(value), value))
        )
        self._private_tokens = tuple(
            token for token in self._tokens if token not in self._public_replacements
        )
        if any(
            self._replacements.get(token) != replacement
            for token, replacement in self._public_replacements.items()
        ) or any(
            token in replacement
            for token in self._private_tokens
            for replacement in self._replacements.values()
        ):
            self._fail("private_replacement_invalid")
            return
        self._public_values = set(self._public_replacements.values())
        match_values = sorted(
            set(self._tokens) | self._public_values,
            key=lambda value: (-len(value), value),
        )
        self._replacement_pattern = (
            re.compile("|".join(re.escape(value) for value in match_values))
            if match_values
            else None
        )

    def _project(self, text: str, *, recoverable: bool = False) -> str | None:
        candidate = (
            self._replacement_pattern.sub(
                lambda match: (
                    match.group()
                    if match.group() in self._public_values
                    else self._replacements[match.group()]
                ),
                text,
            )
            if self._replacement_pattern is not None
            else text
        )
        try:
            sanitized = self._sanitizer(candidate)
        except Exception:  # noqa: BLE001
            if recoverable:
                self._omit("sanitizer_failed")
            else:
                self._fail("sanitizer_failed")
            return None
        if (
            not isinstance(sanitized, str)
            or (candidate and not sanitized)
            or any(token in sanitized for token in self._private_tokens)
        ):
            if recoverable:
                self._omit("sanitizer_rejected")
            else:
                self._fail("sanitizer_rejected")
            return None
        return sanitized

    def _private_prefix_chars(self, text: str) -> int:
        sanitizer_hold = sanitizer_unstable_suffix_length(
            text,
            max_chars=self._max_private_token_chars,
            track_ambiguous_prefixes=True,
        )
        return max(
            self._replacement_prefix_chars(text, (*self._tokens, *self._public_values)),
            sanitizer_hold,
        )

    def _replacement_prefix_chars(self, text: str, tokens: Iterable[str]) -> int:
        held = 0
        for token in tokens:
            limit = min(len(text), len(token) - 1)
            for size in range(limit, held, -1):
                if text.endswith(token[:size]):
                    held = size
                    break
        return held

    def _project_across_publication_boundary(
        self,
        candidate: str,
        *,
        recoverable: bool = False,
    ) -> str | None:
        consumed = 0
        replacement = "private value"
        boundary = len(self._published_suffix)
        combined = self._published_suffix + candidate
        for token in self._private_tokens:
            first_start = max(0, boundary - len(token) + 1)
            for start in range(first_start, boundary):
                if combined.startswith(token, start) and boundary < start + len(token):
                    crossing_chars = start + len(token) - boundary
                    if crossing_chars > consumed:
                        consumed = crossing_chars
                        replacement = self._replacements[token]
        if consumed:
            candidate = replacement + candidate[consumed:]
        projected = self._project(candidate, recoverable=recoverable)
        if projected is None:
            return None
        if any(
            token in self._published_suffix + projected for token in self._private_tokens
        ):
            if recoverable:
                self._omit("private_token_boundary_conflict")
            else:
                self._fail("private_token_boundary_conflict")
            return None
        return projected

    def _published_text(self) -> str:
        return "".join(self._public_answer_chunks)

    def _emit(self, text: str) -> tuple[str, ...]:
        if not text:
            return ()
        self._public_answer_chunks.append(text)
        suffix_chars = self._max_private_token_chars - 1
        self._published_suffix = (self._published_suffix + text)[-suffix_chars:]
        return (text,)

    def _omit(self, reason: str) -> None:
        if self._failure_reason is None:
            self._failure_reason = (
                public_answer_failure_reason(reason) or "upstream_projection_failed"
            )
        self._projection_omissions += 1
        self._pending = ""
        self._pending_source_spans = []

    def _fail(self, reason: str) -> None:
        if reason == "private_token_already_published":
            self._private_token_exposed = True
        if self._failure_reason is None:
            self._failure_reason = (
                public_answer_failure_reason(reason) or "upstream_projection_failed"
            )
        self._failed = True
        self._pending = ""
        self._pending_source_spans = []

    def _discard(self) -> PublicAnswerFinish:
        self._pending = ""
        self._pending_source_spans = []
        self._finished = True
        return PublicAnswerFinish((), "")
