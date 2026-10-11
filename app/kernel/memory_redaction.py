from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


SECRET_KEY_PATTERN = (
    r"[A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?(?:key|header|value|token)|authorization|private[_-]?key|bearer|secret|token|credential|password)"
    r"[A-Za-z0-9_-]*"
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    rf"(?i)\b({SECRET_KEY_PATTERN})"
    r"\s*[:=]\s*(bearer\s+(?:\[redacted-secret\]|[A-Za-z0-9._~+/=-]+)|\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
QUOTED_SECRET_FIELD_PATTERN = re.compile(
    rf"(?i)([\"'])({SECRET_KEY_PATTERN})\1\s*:\s*([\"'])(.*?)\3"
)
BEARER_TOKEN_PATTERN = re.compile(
    r"(?i)\bbearer\s+(?=[A-Za-z0-9._~+/=-]{8,})(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])[A-Za-z0-9._~+/=-]+"
)
# Unconditional run finders must not retry a failing suffix at every boundary.
_SECRET_LIKE_RUN_PATTERN = re.compile(r"[\w.-]+")
_SECRET_LIKE_SEGMENT_PATTERN = re.compile(r"[^._-]+")
_SECRET_LIKE_ALNUM_PATTERN = re.compile(r"[A-Za-z0-9]+", re.IGNORECASE)
_SECRET_LIKE_MARKER_PATTERN = re.compile(
    r"secret|token|credential|password", re.IGNORECASE
)
_SECRET_LIKE_ASSIGNMENT_FOLLOWER_PATTERN = re.compile(r"[\"']?\s*[:=]")
_EMAIL_LOCAL_RUN_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+")
_EMAIL_DOMAIN_PATTERN = re.compile(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
STRICT_PROVIDER_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9_]{10,}|AKIA[0-9A-Z]{16})"
    r"(?![A-Za-z0-9_-])"
)
STRICT_JWT_LIKE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9_-]{10,}\.){2}[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
)

_SANITIZER_TOKEN_SUFFIX = re.compile(
    r"(?<![A-Za-z0-9._-])[A-Za-z0-9._-]+$"
)
_SANITIZER_PROVIDER_PREFIX = re.compile(
    r"(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{0,10}|gh[pousr]_[A-Za-z0-9_]{0,10}|AKIA[0-9A-Z]{0,15})$"
)
_SANITIZER_JWT_TWO_SEGMENT_PREFIX = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*$"
)
_SANITIZER_JWT_THREE_SEGMENT_PREFIX = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9_-]{10,}\.){2}[A-Za-z0-9_-]*$"
)
_SANITIZER_BEARER_PREFIX = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])bearer\s+[A-Za-z0-9._~+/=-]*$"
)
_SANITIZER_ASSIGNMENT_PREFIX = re.compile(
    rf"(?i)(?:{SECRET_KEY_PATTERN})\s*[:=]\s*[\"']?[A-Za-z0-9._-]{{0,15}}$"
)
_SANITIZER_ASSIGNMENT_SEPARATOR_PREFIX = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_-])
    (?:
        (?P<arrow_key_quote>["'])
        (?P<arrow_quoted_key>{SECRET_KEY_PATTERN})
        (?P=arrow_key_quote)
        |
        (?P<arrow_plain_key>{SECRET_KEY_PATTERN})
    )
    \s*-$
    """
)
_SANITIZER_QUOTED_KEY_PREFIX = re.compile(
    r"(?ix)(?<![A-Za-z0-9_-])(?P<quote>[\"'])(?:[A-Za-z0-9_-]*)$"
)
_SANITIZER_ASSIGNMENT_KEY_SUFFIX = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_-])
    (?:
        (?P<quoted_key_quote>["'])
        (?P<quoted_key_suffix>{SECRET_KEY_PATTERN})
        (?P=quoted_key_quote)
        |
        (?P<plain_key_suffix>{SECRET_KEY_PATTERN})
    )
    \s*$
    """
)
_SANITIZER_ASSIGNMENT_START = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_-])
    (?:
        (?P<key_quote>[\"'])
        (?P<quoted_key>{SECRET_KEY_PATTERN})
        (?P=key_quote)
        |
        (?P<plain_key>{SECRET_KEY_PATTERN})
    )
    \s*(?:=>|->|[:=])\s*
    (?P<value_quote>[\"'])?
    """
)
_SECRET_ASSIGNMENT_STREAM_PATTERN = re.compile(
    rf"""(?isx)
    (?<![A-Za-z0-9_-])
    (?P<key>
        (?P<key_quote>[\"'])
        (?P<quoted_key>{SECRET_KEY_PATTERN})
        (?P=key_quote)
        |
        (?P<plain_key>{SECRET_KEY_PATTERN})
    )
    \s*(?P<separator>=>|->|[:=])\s*
    (?P<value>
        "(?:\\.|[^"\\])*(?:"|\\?\Z)
        | '(?:\\.|[^'\\])*(?:'|\\?\Z)
        | bearer\s+[A-Za-z0-9._~+/=-]+
        | [^\s,;}}]+
    )
    """
)


def _redact_stream_secret_assignment(match: re.Match[str]) -> str:
    key = match.group("quoted_key") or match.group("plain_key")
    if not is_sensitive_redaction_key(key):
        return match.group(0)
    value = match.group("value")
    quote = value[:1] if value[:1] in {"'", '"'} else ""
    redacted_value = f"{quote}[redacted-secret]{quote}" if quote else "[redacted-secret]"
    return f"{match.group('key')}{match.group('separator')}{redacted_value}"


def redact_secret_assignments(value: str) -> str:
    """Redact raw assignment spans before display replacements can alter quotes."""

    return _SECRET_ASSIGNMENT_STREAM_PATTERN.sub(_redact_stream_secret_assignment, value)


@dataclass(frozen=True, slots=True)
class SecretQuoteContinuation:
    """Constant-size syntax state after discarding an oversized quoted value."""

    quote: str
    escaped: bool = False

    def consume(
        self, value: str, *, start: int = 0
    ) -> tuple[int, SecretQuoteContinuation | None]:
        """Return characters consumed from start without copying the suffix."""

        escaped = self.escaped
        for index in range(start, len(value)):
            character = value[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == self.quote:
                return index - start + 1, None
        return len(value) - start, SecretQuoteContinuation(self.quote, escaped)


def _unfinished_secret_assignment(
    value: str,
) -> tuple[int, SecretQuoteContinuation | None] | None:
    completed_quote_end = 0
    for match in _SANITIZER_ASSIGNMENT_START.finditer(value):
        if match.start() < completed_quote_end:
            continue
        key = match.group("quoted_key") or match.group("plain_key")
        if not is_sensitive_redaction_key(key):
            continue
        value_start = match.end()
        quote = match.group("value_quote")
        if quote:
            consumed, continuation = SecretQuoteContinuation(quote).consume(
                value, start=value_start
            )
            if continuation is not None:
                return match.start(), continuation
            completed_quote_end = value_start + consumed
            continue
        if not re.search(r"[\s,;}]", value[value_start:]):
            return match.start(), None
    separator_prefix = _SANITIZER_ASSIGNMENT_SEPARATOR_PREFIX.search(value)
    if separator_prefix and separator_prefix.start() >= completed_quote_end:
        key = separator_prefix.group("arrow_quoted_key") or separator_prefix.group(
            "arrow_plain_key"
        )
        if is_sensitive_redaction_key(key):
            return separator_prefix.start(), None
    quoted_key_prefix = _SANITIZER_QUOTED_KEY_PREFIX.search(value)
    if quoted_key_prefix and quoted_key_prefix.start() >= completed_quote_end:
        return quoted_key_prefix.start(), None
    key_suffix = _SANITIZER_ASSIGNMENT_KEY_SUFFIX.search(value)
    if key_suffix and key_suffix.start() >= completed_quote_end:
        key = key_suffix.group("quoted_key_suffix") or key_suffix.group(
            "plain_key_suffix"
        )
        if "__" not in key and is_sensitive_redaction_key(key):
            return key_suffix.start(), None
    return None


def sanitizer_unfinished_quote(
    value: str,
) -> tuple[int, SecretQuoteContinuation] | None:
    """Locate an open secret quote and its byte-free continuation grammar."""

    assignment = _unfinished_secret_assignment(value)
    if assignment is None or assignment[1] is None:
        return None
    return assignment[0], assignment[1]


def sanitizer_unstable_assignment_suffix_length(
    value: str, *, max_chars: int = 64
) -> int:
    """Return a bounded suffix held while a secret assignment remains incomplete."""

    assignment = _unfinished_secret_assignment(value)
    if assignment is None:
        return 0
    candidate_chars = len(value) - assignment[0]
    return candidate_chars if candidate_chars <= max_chars else max_chars + 1


def sanitizer_unstable_suffix_length(
    value: str, *, max_chars: int = 64, track_ambiguous_prefixes: bool = False
) -> int:
    """Return a bounded suffix that could become a secret on the next chunk."""

    assignment_hold = sanitizer_unstable_assignment_suffix_length(
        value, max_chars=max_chars
    )
    if assignment_hold:
        return assignment_hold

    for pattern in (
        _SANITIZER_JWT_THREE_SEGMENT_PREFIX,
        _SANITIZER_JWT_TWO_SEGMENT_PREFIX,
        _SANITIZER_BEARER_PREFIX,
    ):
        match = pattern.search(value)
        if match:
            candidate_chars = len(value) - match.start()
            return candidate_chars if candidate_chars <= max_chars else max_chars + 1

    limit = min(len(value), max_chars)
    for size in range(limit, 0, -1):
        suffix = value[-size:]
        if (
            _SANITIZER_PROVIDER_PREFIX.fullmatch(suffix)
            or _SANITIZER_ASSIGNMENT_PREFIX.fullmatch(suffix)
        ):
            return size
    if not track_ambiguous_prefixes:
        return 0
    candidate_match = _SANITIZER_TOKEN_SUFFIX.search(value)
    if candidate_match:
        candidate_chars = len(value) - candidate_match.start()
        return candidate_chars if candidate_chars <= max_chars else max_chars + 1
    return 0


MEMORY_REDACTION_MODE_STANDARD = "standard"
MEMORY_REDACTION_MODE_STRICT = "strict"
MEMORY_REDACTION_MODES = {MEMORY_REDACTION_MODE_STANDARD, MEMORY_REDACTION_MODE_STRICT}

SENSITIVE_METADATA_KEY_ALIASES = {
    "apikey",
    "accesskey",
    "accesstoken",
    "authorization",
    "bearertoken",
    "credential",
    "credentials",
    "password",
    "refreshtoken",
    "secret",
    "token",
}
COMPACT_SENSITIVE_KEY_FRAGMENTS = {
    "apikey",
    "accesskey",
    "accesstoken",
    "authorizationheader",
    "authorizationtoken",
    "authorizationvalue",
    "authheader",
    "authkey",
    "authtoken",
    "authvalue",
    "bearer",
    "bearertoken",
    "clientsecret",
    "idtoken",
    "password",
    "privatekey",
    "refreshtoken",
    "tokenkey",
    "tokenvalue",
    "xapikey",
}
SAFE_COMPACT_PUBLIC_KEYS = {
    "authorizationstatus",
    "authstatus",
    "clientsecretary",
    "clientsecretaryname",
    "oauthauthorizationstatus",
    "secretary",
    "secretaryname",
    "tokenbudget",
    "tokenbudgets",
    "tokencount",
    "tokencounts",
    "tokenstatus",
    "tokentotal",
    "tokenusage",
    "tokenizer",
}
SAFE_TOKEN_FOLLOWERS = {"budget", "budgets", "count", "counts", "status", "total", "usage"}
SAFE_SECRET_VALUE_FOLLOWERS = {
    "budget",
    "budgets",
    "count",
    "counts",
    "flow",
    "flows",
    "helper",
    "helpers",
    "reset",
    "resets",
    "status",
    "total",
    "usage",
}
SENSITIVE_AUTH_TOKENS = {"bearer", "header", "key", "token", "value"}


def normalize_memory_redaction_mode(value: object) -> str:
    """Return a supported memory redaction mode or raise on unsafe input."""
    mode = str(value).strip().lower() if value is not None else ""
    if mode not in MEMORY_REDACTION_MODES:
        raise ValueError("memory_redaction_mode_invalid")
    return mode


def _normalized_key(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum()).lower()


def _key_tokens(value: object) -> list[str]:
    separated = CAMEL_BOUNDARY_PATTERN.sub("_", str(value))
    return [item.lower() for item in re.split(r"[^A-Za-z0-9]+", separated) if item]


def _has_adjacent_tokens(tokens: list[str], left: str, right: str) -> bool:
    return any(tokens[index] == left and tokens[index + 1] == right for index in range(len(tokens) - 1))


def _is_sensitive_tokenized_key(tokens: list[str]) -> bool | None:
    if not tokens:
        return False
    if len(tokens) == 1:
        return None
    if "password" in tokens or "credential" in tokens or "credentials" in tokens or "secret" in tokens:
        return True
    if (
        _has_adjacent_tokens(tokens, "api", "key")
        or _has_adjacent_tokens(tokens, "access", "key")
        or _has_adjacent_tokens(tokens, "access", "token")
        or _has_adjacent_tokens(tokens, "refresh", "token")
        or _has_adjacent_tokens(tokens, "id", "token")
        or _has_adjacent_tokens(tokens, "bearer", "token")
    ):
        return True
    sensitive_token_indexes = [
        index
        for index, token in enumerate(tokens)
        if token in {"token", "secret", "credential", "password"}
    ]
    if sensitive_token_indexes and all(
        index + 1 < len(tokens) and tokens[index + 1] in SAFE_SECRET_VALUE_FOLLOWERS
        for index in sensitive_token_indexes
    ):
        return False
    if "authorization" in tokens:
        return tokens == ["authorization"] or bool(SENSITIVE_AUTH_TOKENS.intersection(tokens))
    if "auth" in tokens:
        return bool(SENSITIVE_AUTH_TOKENS.intersection(tokens))
    if "bearer" in tokens:
        return True
    if "private" in tokens and "key" in tokens:
        return True
    if "token" in tokens:
        for index, token in enumerate(tokens):
            if token != "token":
                continue
            next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
            if next_token not in SAFE_TOKEN_FOLLOWERS:
                return True
        return False
    return False


def _has_sensitive_compact_secret(normalized_key: str) -> bool:
    return "secret" in normalized_key.replace("secretary", "")


def _has_sensitive_compact_token(normalized_key: str) -> bool:
    return normalized_key.endswith("token") or any(
        fragment in normalized_key
        for fragment in {
            "accesstoken",
            "authtoken",
            "bearertoken",
            "idtoken",
            "refreshtoken",
            "tokenkey",
            "tokenvalue",
        }
    )


def is_sensitive_redaction_key(value: object) -> bool:
    normalized_key = _normalized_key(value)
    if normalized_key in SENSITIVE_METADATA_KEY_ALIASES:
        return True
    tokens = _key_tokens(value)
    tokenized_result = _is_sensitive_tokenized_key(tokens)
    if tokenized_result is not None:
        return tokenized_result
    if normalized_key in SAFE_COMPACT_PUBLIC_KEYS:
        return False
    if (
        any(fragment in normalized_key for fragment in COMPACT_SENSITIVE_KEY_FRAGMENTS)
        or "credential" in normalized_key
        or _has_sensitive_compact_secret(normalized_key)
        or _has_sensitive_compact_token(normalized_key)
    ):
        return True
    return False


def _redact_secret_assignment(match: re.Match[str]) -> str:
    if not is_sensitive_redaction_key(match.group(1)):
        return match.group(0)
    return f"{match.group(1)}=[redacted-secret]"


def _redact_quoted_secret_field(match: re.Match[str]) -> str:
    if not is_sensitive_redaction_key(match.group(2)):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}{match.group(1)}:{match.group(3)}[redacted-secret]{match.group(3)}"


def _redaction_word_boundary(text: str, index: int) -> bool:
    left = index > 0 and (text[index - 1].isalnum() or text[index - 1] == "_")
    right = index < len(text) and (text[index].isalnum() or text[index] == "_")
    return left != right


def _secret_like_token_matches(text: str) -> Iterator[tuple[int, int, bool]]:
    """Yield the existing lexical match spans and their redaction decisions.

    A grammar chain has alphanumeric segments joined by single separators.
    Both former regex alternatives select the earliest legal start and the
    farthest legal end containing at least two segments and a secret marker.
    Unicode token runs can contain many separate chains; classify each run's
    safe followers once so partial matches never rescan the remaining suffix.
    """
    # Every lexical match requires one of these fixed literals. A single
    # C-level search avoids allocating a segment list for every prose word;
    # marker-present input still uses the bounded run/chain scanner below.
    if not _SECRET_LIKE_MARKER_PATTERN.search(text):
        return
    for run in _SECRET_LIKE_RUN_PATTERN.finditer(text):
        segments = list(_SECRET_LIKE_SEGMENT_PATTERN.finditer(text, run.start(), run.end()))
        if len(segments) < 2:
            continue
        values = [text[segment.start():segment.end()] for segment in segments]
        markers = [bool(_SECRET_LIKE_MARKER_PATTERN.fullmatch(value)) for value in values]
        if not any(markers):
            continue
        grammar = [bool(_SECRET_LIKE_ALNUM_PATTERN.fullmatch(value)) for value in values]
        # IGNORECASE includes İ, ı, ſ and K. Safety deliberately uses lower(),
        # not casefold() or the regex's equivalence classes.
        normalized = [value.lower() for value in values]
        sensitive = [value in {"secret", "token", "credential", "password"} for value in normalized]
        sensitive_counts = [0]
        unsafe_counts = [0]
        for index, is_sensitive in enumerate(sensitive):
            unsafe = is_sensitive and (
                index + 1 == len(segments)
                or normalized[index + 1] not in SAFE_SECRET_VALUE_FOLLOWERS
            )
            sensitive_counts.append(sensitive_counts[-1] + is_sensitive)
            unsafe_counts.append(unsafe_counts[-1] + unsafe)

        # The old eight-character lookahead can reach beyond a grammar chain,
        # and its last boundary may follow punctuation before a Unicode word.
        length_reach = [-1] * len(segments)
        segment_index = len(segments) - 1
        boundary = -1
        for position in range(run.end() - 1, run.start() - 1, -1):
            character = text[position]
            if character not in "._-" and not _SECRET_LIKE_ALNUM_PATTERN.fullmatch(character):
                boundary = -1
            elif boundary < 0 and _redaction_word_boundary(text, position + 1):
                boundary = position + 1
            if segment_index >= 0 and position == segments[segment_index].start():
                length_reach[segment_index] = boundary
                segment_index -= 1

        chain_start = 0
        while chain_start < len(segments):
            if not grammar[chain_start]:
                chain_start += 1
                continue
            chain_end = chain_start
            while (
                chain_end + 1 < len(segments)
                and grammar[chain_end + 1]
                and segments[chain_end + 1].start() == segments[chain_end].end() + 1
            ):
                chain_end += 1
            # Endpoint checks are constant except for trailing whitespace at
            # the end of this disjoint outer run, which is scanned only once.
            last = chain_end
            while last > chain_start:
                end = segments[last].end()
                if (
                    _redaction_word_boundary(text, end)
                    and (end == len(text) or text[end] != "]")
                    and not _SECRET_LIKE_ASSIGNMENT_FOLLOWER_PATTERN.match(text, end)
                ):
                    break
                last -= 1
            last_marker = last
            while last_marker >= chain_start and not markers[last_marker]:
                last_marker -= 1
            for first in range(chain_start, min(last, last_marker + 1)):
                start = segments[first].start()
                if (
                    (start > 0 and text[start - 1] == "[")
                    or not _redaction_word_boundary(text, start)
                    or length_reach[first] < start + 8
                ):
                    continue
                end = segments[last].end()
                # A range's final sensitive segment has no follower, even if
                # the full token run has a safe follower beyond this match.
                safe_match = (
                    sensitive_counts[last + 1] > sensitive_counts[first]
                    and unsafe_counts[last] == unsafe_counts[first]
                    and not sensitive[last]
                )
                safe_suffix = (
                    end + 1 < len(text)
                    and text[end] in "._-"
                    and text[end + 1].isalnum()
                    and sensitive_counts[-1] > sensitive_counts[first]
                    and unsafe_counts[-1] == unsafe_counts[first]
                )
                yield start, end, not (safe_match or safe_suffix)
                break
            chain_start = chain_end + 1


def _redact_secret_like_values(text: str) -> str:
    pieces = []
    previous = 0
    for start, end, redact in _secret_like_token_matches(text):
        pieces.append(text[previous:start])
        pieces.append("[redacted-secret]" if redact else text[start:end])
        previous = end
    pieces.append(text[previous:])
    return "".join(pieces)


def _email_matches(text: str) -> Iterator[tuple[int, int]]:
    """Scan each local run and anchor the unchanged domain grammar once."""
    if "@" not in text:
        return
    previous = 0
    for local in _EMAIL_LOCAL_RUN_PATTERN.finditer(text):
        end = local.end()
        if end <= previous or end == len(text) or text[end] != "@":
            continue
        # A preceding email may end inside this run (a@b.com.x-y@z.com).
        start = max(local.start(), previous)
        while start < end and not _redaction_word_boundary(text, start):
            start += 1
        if start == end:
            continue
        domain = _EMAIL_DOMAIN_PATTERN.match(text, end + 1)
        if domain:
            previous = domain.end()
            yield start, previous


def _redact_emails(text: str) -> str:
    pieces = []
    previous = 0
    for start, end in _email_matches(text):
        pieces.extend((text[previous:start], "[redacted-email]"))
        previous = end
    pieces.append(text[previous:])
    return "".join(pieces)


def redact_memory_text(value: object, *, mode: str = MEMORY_REDACTION_MODE_STANDARD) -> str:
    redaction_mode = normalize_memory_redaction_mode(mode)
    text = "" if value is None else str(value)
    text = redact_secret_assignments(text)
    text = QUOTED_SECRET_FIELD_PATTERN.sub(_redact_quoted_secret_field, text)
    text = SECRET_ASSIGNMENT_PATTERN.sub(_redact_secret_assignment, text)
    text = BEARER_TOKEN_PATTERN.sub("Bearer [redacted-secret]", text)
    text = _redact_secret_like_values(text)
    text = _redact_emails(text)
    if redaction_mode == MEMORY_REDACTION_MODE_STRICT:
        text = STRICT_PROVIDER_SECRET_PATTERN.sub("[redacted-secret]", text)
        text = STRICT_JWT_LIKE_PATTERN.sub("[redacted-secret]", text)
    return text


def _redact_metadata_value(value: Any, *, mode: str) -> Any:
    if isinstance(value, dict):
        return {key: redact_memory_metadata_value(key, item, mode=mode) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_metadata_value(item, mode=mode) for item in value]
    if isinstance(value, str):
        return redact_memory_text(value, mode=mode)
    return value


def redact_memory_metadata_value(key: object, value: Any, *, mode: str = MEMORY_REDACTION_MODE_STANDARD) -> Any:
    redaction_mode = normalize_memory_redaction_mode(mode)
    if is_sensitive_redaction_key(key):
        return "[redacted-secret]"
    return _redact_metadata_value(value, mode=redaction_mode)


def redact_memory_metadata(value: Any, *, mode: str = MEMORY_REDACTION_MODE_STANDARD) -> dict[str, Any]:
    redaction_mode = normalize_memory_redaction_mode(mode)
    if not isinstance(value, dict):
        return {}
    return {str(key): redact_memory_metadata_value(key, item, mode=redaction_mode) for key, item in value.items()}
