from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.memory_redaction import (
    sanitizer_unstable_assignment_suffix_length,
    sanitizer_unstable_suffix_length,
)


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
        self._published_text = False
        self._public_answer_text = ""
        self._sealed = False
        self._deferred_until_finish = False
        self._capability_boundary_seen = False
        self._released_after_verified_capability = False
        self._failed = (
            not callable(sanitizer)
            or not isinstance(max_private_token_chars, int)
            or isinstance(max_private_token_chars, bool)
            or max_private_token_chars < 2
            or not isinstance(max_sealed_chars, int)
            or isinstance(max_sealed_chars, bool)
            or max_sealed_chars < 1
        )
        self._finished = False
        if not self._failed:
            self._add_replacements(private_replacements)

    @property
    def failed(self) -> bool:
        """Return whether text safety can no longer be proven for this run."""

        return self._failed

    def final_text_exceeds_bound(self, value: object) -> bool:
        """Check the projected terminal answer without publishing it."""

        if self._failed or not isinstance(value, str):
            return False
        projected = self._project(value)
        return self._logical_overflowed or (
            projected is not None and len(projected) > self._max_sealed_chars
        )

    def accept(self, text: object) -> tuple[str, ...]:
        """Accept one ordered answer fragment and return immediately safe chunks."""

        if self._failed or self._finished:
            return ()
        if not isinstance(text, str):
            self._fail()
            return ()
        if not text:
            return ()
        if self._deferred_until_finish and not self._released_after_verified_capability:
            return ()
        self._accepted_text = True
        self._extend_logical_view(text)
        raw_candidate = self._pending + text
        raw_hold = sanitizer_unstable_assignment_suffix_length(
            raw_candidate,
            max_chars=self._max_private_token_chars,
        )
        if raw_hold:
            if raw_hold > self._max_private_token_chars:
                self._fail()
                return ()
            stable_candidate = self._project(raw_candidate[:-raw_hold])
            if stable_candidate is None:
                return ()
            self._pending = raw_candidate[-raw_hold:]
            return () if self._deferred_until_finish else self._emit(stable_candidate)
        candidate = self._project(raw_candidate)
        if candidate is None:
            return ()
        if self._sealed:
            if len(candidate) > self._max_sealed_chars:
                self._fail()
                return ()
            self._pending = candidate
            return ()
        held_chars = self._private_prefix_chars(candidate)
        if held_chars > self._max_private_token_chars:
            self._fail()
            return ()
        emitted = candidate[:-held_chars] if held_chars else candidate
        self._pending = candidate[-held_chars:] if held_chars else ""
        return () if self._deferred_until_finish else self._emit(emitted)

    def seal(self, private_replacements: Mapping[str, str] | None = None) -> None:
        """Seal later text and add private tokens learned from an actual invocation."""

        if self._failed or self._finished:
            return
        if private_replacements is not None:
            self._add_replacements(private_replacements)
        if self._deferred_until_finish:
            self._capability_boundary_seen = True
            self._sealed = True
            return
        self._sealed = True
        self._released_after_verified_capability = False
        if self._failed or self._logical_overflowed:
            self._fail()
            return
        logical_view = self._project(self._logical_view)
        pending = self._project(self._pending)
        if logical_view is None or pending is None:
            return
        self._logical_view = logical_view
        self._pending = pending
        if (
            len(self._logical_view) > self._max_sealed_chars
            or len(self._pending) > self._max_sealed_chars
        ):
            self._fail()

    def defer_until_finish(self) -> None:
        """Discard interim text and project only the authoritative terminal answer."""

        if self._failed or self._finished:
            return
        self._pending = ""
        self._logical_view = ""
        self._deferred_until_finish = True

    def release_after_verified_capability(self) -> None:
        """Allow new text only after the caller has verified capability completion.

        Text retained before the verification point is deliberately discarded: raw
        assistant text before a capability completes is not a server-authoritative
        final-answer projection and must not be replayed as one.
        """

        if self._failed or self._finished or not self._sealed:
            return
        self._pending = ""
        self._logical_view = ""
        self._public_answer_text = ""
        self._sealed = False
        self._released_after_verified_capability = True

    def fail_closed(self) -> None:
        """Irreversibly discard retained text when an upstream projection is unsafe."""

        self._fail()

    def finish(self, *, final_text: object, release: bool) -> PublicAnswerFinish:
        """Release retained text once, or discard it without a public terminal answer."""

        if self._finished:
            return PublicAnswerFinish((), "")
        if self._failed or release is not True or not isinstance(final_text, str):
            if not isinstance(final_text, str):
                self._fail()
            return self._discard()

        safe_final = self._project(final_text)
        if safe_final is None:
            return self._discard()
        if self._logical_overflowed or (
            not self._capability_boundary_seen and len(safe_final) > self._max_sealed_chars
        ):
            self._fail()
            return self._discard()

        if self._sealed and self._accepted_text:
            logical_answer = _reconcile_answer_views(self._logical_view, safe_final)
            if logical_answer != safe_final:
                self._fail()
                return self._discard()

        if self._released_after_verified_capability:
            candidate = (
                self._logical_view
                if self._deferred_until_finish
                else self._pending
            )
        elif self._capability_boundary_seen:
            candidate = ""
        elif not self._accepted_text:
            candidate = safe_final
        elif self._sealed and not self._published_text:
            candidate = safe_final
        else:
            candidate = self._pending
        emitted = self._project_across_publication_boundary(candidate)
        if emitted is None:
            return self._discard()
        chunks = self._emit(emitted)
        self._pending = ""
        self._finished = True
        public_final_text = (
            self._logical_view
            if self._released_after_verified_capability
            else ""
            if self._capability_boundary_seen
            else safe_final
        )
        return PublicAnswerFinish(chunks, public_final_text)

    def _add_replacements(self, replacements: Mapping[str, str]) -> None:
        try:
            items = tuple(replacements.items())
        except (AttributeError, TypeError):
            self._fail()
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
                self._fail()
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
            self._fail()

    def _extend_logical_view(self, text: str) -> None:
        if self._logical_overflowed:
            if self._sealed:
                self._fail()
            return
        candidate = self._project(self._logical_view + text)
        if candidate is None:
            return
        if len(candidate) > self._max_sealed_chars:
            self._logical_view = ""
            self._logical_overflowed = True
            if self._sealed:
                self._fail()
            return
        self._logical_view = candidate

    def _project(self, text: str) -> str | None:
        candidate = text
        for token in self._tokens:
            candidate = candidate.replace(token, self._replacements[token])
        try:
            sanitized = self._sanitizer(candidate)
        except Exception:  # noqa: BLE001
            self._fail()
            return None
        if (
            not isinstance(sanitized, str)
            or (candidate and not sanitized)
            or any(token in sanitized for token in self._tokens)
        ):
            self._fail()
            return None
        return sanitized

    def _private_prefix_chars(self, text: str) -> int:
        held = 0
        for token in self._tokens:
            limit = min(len(text), len(token) - 1)
            for size in range(limit, held, -1):
                if text.endswith(token[:size]):
                    held = size
                    break
        sanitizer_hold = sanitizer_unstable_suffix_length(
            text,
            max_chars=self._max_private_token_chars,
            track_ambiguous_prefixes=True,
        )
        return max(held, sanitizer_hold)

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
            self._fail()
            return None
        return projected

    def _emit(self, text: str) -> tuple[str, ...]:
        if not text:
            return ()
        self._published_text = True
        self._public_answer_text += text
        suffix_chars = self._max_private_token_chars - 1
        self._published_suffix = (self._published_suffix + text)[-suffix_chars:]
        return (text,)

    def _fail(self) -> None:
        self._failed = True
        self._pending = ""
        self._logical_view = ""

    def _discard(self) -> PublicAnswerFinish:
        self._pending = ""
        self._logical_view = ""
        self._finished = True
        return PublicAnswerFinish((), "")


def _reconcile_answer_views(*views: str) -> str | None:
    merged = ""
    for view in filter(None, views):
        if not merged or view.startswith(merged) or view.endswith(merged):
            merged = view
            continue
        if merged.startswith(view) or merged.endswith(view):
            continue
        limit = min(len(merged), len(view))
        forward = next(
            (size for size in range(limit, 0, -1) if merged.endswith(view[:size])), 0
        )
        reverse = next(
            (size for size in range(limit, 0, -1) if view.endswith(merged[:size])), 0
        )
        if not forward and not reverse:
            return None
        merged = (
            merged + view[forward:] if forward >= reverse else view + merged[reverse:]
        )
    return merged
