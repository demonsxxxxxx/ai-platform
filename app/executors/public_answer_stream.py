from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.execution.api import public_answer_failure_reason
from app.memory_redaction import sanitizer_unstable_suffix_length


@dataclass(frozen=True, slots=True)
class PublicAnswerFinish:
    """One terminal, already-sanitized public answer projection."""

    chunks: tuple[str, ...]
    final_text: str


class PublicAnswerStreamGate:
    """Project bounded answer text without exposing exact executor-private tokens."""

    def __init__(
        self,
        *,
        private_replacements: Mapping[str, str],
        sanitizer: Callable[[str], str],
        max_private_token_chars: int = 512,
        max_sealed_chars: int = 4_096,
    ) -> None:
        self._sanitizer = sanitizer
        self._max_private_token_chars = max_private_token_chars
        self._max_sealed_chars = max_sealed_chars
        self._replacements: dict[str, str] = {}
        self._tokens: tuple[str, ...] = ()
        self._pending = ""
        self._published_suffix = ""
        self._logical_view = ""
        self._logical_overflowed = False
        self._accepted_text = False
        self._public_answer_text = ""
        self._active_capability_invocations: set[tuple[str, str, str]] = set()
        self._capability_boundary_seen = False
        self._failed = False
        self._failure_reason: str | None = None
        self._finished = False
        if (
            not callable(sanitizer)
            or not isinstance(max_private_token_chars, int)
            or isinstance(max_private_token_chars, bool)
            or max_private_token_chars < 2
            or not isinstance(max_sealed_chars, int)
            or isinstance(max_sealed_chars, bool)
            or max_sealed_chars < 1
        ):
            self._fail("invalid_configuration")
        else:
            self._add_replacements(private_replacements)

    @property
    def failed(self) -> bool:
        """Return whether text safety can no longer be proven for this run."""

        return self._failed

    @property
    def failure_reason(self) -> str | None:
        """Return the first public-safe reason that made projection fail closed."""

        return self._failure_reason

    def final_text_exceeds_bound(self, value: object) -> bool:
        """Check the projected terminal fallback without publishing it."""

        if (
            self._failed
            or not isinstance(value, str)
            or self._accepted_text
            or self._capability_boundary_seen
        ):
            return False
        projected = self._project(value)
        exceeds = self._logical_overflowed or (
            projected is not None and len(projected) > self._max_sealed_chars
        )
        if exceeds:
            self._fail("answer_too_large")
        return exceeds

    def accept(self, text: object) -> tuple[str, ...]:
        """Accept one ordered Assistant fragment and return immediately safe chunks."""

        if self._failed or self._finished:
            return ()
        if not isinstance(text, str):
            self._fail("invalid_input")
            return ()
        if not text:
            return ()
        if self._active_capability_invocations:
            return ()
        self._accepted_text = True
        self._extend_logical_view(text)
        if self._failed:
            return ()
        raw_candidate = self._pending + text
        raw_hold = self._private_prefix_chars(raw_candidate)
        if raw_hold:
            if raw_hold > self._max_private_token_chars:
                self._fail("sanitizer_bound_exceeded")
                return ()
            stable_candidate = self._project(raw_candidate[:-raw_hold])
            if stable_candidate is None:
                return ()
            self._pending = raw_candidate[-raw_hold:]
            emitted = self._project_across_publication_boundary(stable_candidate)
            return self._emit(emitted) if emitted is not None else ()
        candidate = self._project(raw_candidate)
        if candidate is None:
            return ()
        held_chars = self._private_prefix_chars(candidate)
        if held_chars > self._max_private_token_chars:
            self._fail("private_token_prefix_overflow")
            return ()
        emitted = candidate[:-held_chars] if held_chars else candidate
        self._pending = candidate[-held_chars:] if held_chars else ""
        emitted = self._project_across_publication_boundary(emitted)
        return self._emit(emitted) if emitted is not None else ()

    def seal(
        self,
        private_replacements: Mapping[str, str] | None = None,
        *,
        capability_boundary: bool = False,
        invocation_key: tuple[str, str, str],
    ) -> None:
        """Register private identities and close disclosure for one invocation."""

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
        held_chars = self._private_replacement_prefix_chars(self._pending)
        if held_chars:
            self._pending = self._pending[:-held_chars]
            self._logical_view = self._logical_view[:-held_chars]
        self._active_capability_invocations.add(invocation_key)
        self._capability_boundary_seen = True

    def register_private_replacements(
        self,
        private_replacements: Mapping[str, str],
    ) -> None:
        """Learn executor-private tokens before later answer text can expose them."""

        if self._failed or self._finished:
            return
        previous_tokens = set(self._tokens)
        self._add_replacements(private_replacements)
        if self._failed:
            return
        added_tokens = set(self._tokens) - previous_tokens
        if any(token in self._public_answer_text for token in added_tokens):
            self._fail("private_token_already_published")
            return
        logical_view = self._project(self._logical_view)
        pending = self._project(self._pending)
        if logical_view is None or pending is None:
            return
        self._logical_view = logical_view
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
        if self._failed or release is not True:
            return self._discard()
        if self._active_capability_invocations:
            self._fail("upstream_projection_failed")
            return self._discard()

        if self._accepted_text:
            candidate = self._pending
        elif self._capability_boundary_seen:
            candidate = ""
        else:
            if not isinstance(final_text, str):
                self._fail("invalid_input")
                return self._discard()
            safe_final = self._project(final_text)
            if safe_final is None or len(safe_final) > self._max_sealed_chars:
                if safe_final is not None:
                    self._fail("answer_too_large")
                return self._discard()
            candidate = safe_final
        emitted = self._project_across_publication_boundary(candidate)
        if emitted is None:
            return self._discard()
        chunks = self._emit(emitted)
        self._pending = ""
        self._finished = True
        return PublicAnswerFinish(chunks, self._public_answer_text)

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
        if any(
            token in replacement
            for token in self._tokens
            for replacement in self._replacements.values()
        ):
            self._fail("private_replacement_invalid")

    def _extend_logical_view(self, text: str) -> None:
        if self._logical_overflowed:
            return
        candidate = self._project(self._logical_view + text)
        if candidate is None:
            return
        if len(candidate) > self._max_sealed_chars:
            self._logical_view = ""
            self._logical_overflowed = True
            self._fail("answer_too_large")
            return
        self._logical_view = candidate

    def _project(self, text: str) -> str | None:
        candidate = text
        for token in self._tokens:
            candidate = candidate.replace(token, self._replacements[token])
        try:
            sanitized = self._sanitizer(candidate)
        except Exception:  # noqa: BLE001
            self._fail("sanitizer_failed")
            return None
        if (
            not isinstance(sanitized, str)
            or (candidate and not sanitized)
            or any(token in sanitized for token in self._tokens)
        ):
            self._fail("sanitizer_rejected")
            return None
        return sanitized

    def _private_prefix_chars(self, text: str) -> int:
        sanitizer_hold = sanitizer_unstable_suffix_length(
            text,
            max_chars=self._max_private_token_chars,
            track_ambiguous_prefixes=True,
        )
        return max(self._private_replacement_prefix_chars(text), sanitizer_hold)

    def _private_replacement_prefix_chars(self, text: str) -> int:
        held = 0
        for token in self._tokens:
            limit = min(len(text), len(token) - 1)
            for size in range(limit, held, -1):
                if text.endswith(token[:size]):
                    held = size
                    break
        return held

    def _project_across_publication_boundary(self, candidate: str) -> str | None:
        consumed = 0
        replacement = "private value"
        boundary = len(self._published_suffix)
        combined = self._published_suffix + candidate
        for token in self._tokens:
            first_start = max(0, boundary - len(token) + 1)
            for start in range(first_start, boundary):
                if combined.startswith(token, start) and boundary < start + len(token):
                    crossing_chars = start + len(token) - boundary
                    if crossing_chars > consumed:
                        consumed = crossing_chars
                        replacement = self._replacements[token]
        if consumed:
            candidate = replacement + candidate[consumed:]
        projected = self._project(candidate)
        if projected is None:
            return None
        if any(token in self._published_suffix + projected for token in self._tokens):
            self._fail("private_token_boundary_conflict")
            return None
        return projected

    def _emit(self, text: str) -> tuple[str, ...]:
        if not text:
            return ()
        if len(self._public_answer_text) + len(text) > self._max_sealed_chars:
            self._fail("answer_too_large")
            return ()
        self._public_answer_text += text
        suffix_chars = self._max_private_token_chars - 1
        self._published_suffix = (self._published_suffix + text)[-suffix_chars:]
        return (text,)

    def _fail(self, reason: str) -> None:
        if self._failure_reason is None:
            self._failure_reason = (
                public_answer_failure_reason(reason) or "upstream_projection_failed"
            )
        self._failed = True
        self._pending = ""
        self._logical_view = ""

    def _discard(self) -> PublicAnswerFinish:
        self._pending = ""
        self._logical_view = ""
        self._finished = True
        return PublicAnswerFinish((), "")
