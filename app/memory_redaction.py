from __future__ import annotations

import re
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
SECRET_LIKE_TOKEN_VALUE_PATTERN = re.compile(
    r"(?i)(?<!\[)\b(?=[A-Za-z0-9._-]{8,}\b)"
    r"(?:[A-Za-z0-9]+[._-])+(?:secret|token|credential|password)(?:[._-][A-Za-z0-9]+)*\b"
    r"(?![\"']?\s*[:=])(?!\])"
    r"|(?<!\[)\b(?=[A-Za-z0-9._-]{8,}\b)"
    r"(?:secret|token|credential|password)(?:[._-][A-Za-z0-9]+)+\b"
    r"(?![\"']?\s*[:=])(?!\])"
)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
STRICT_PROVIDER_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9_]{10,}|AKIA[0-9A-Z]{16})"
    r"(?![A-Za-z0-9_-])"
)
STRICT_JWT_LIKE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9_-]{10,}\.){2}[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
)

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


def _is_safe_secret_like_value(value: str) -> bool:
    segments = [item.lower() for item in re.split(r"[._-]+", value) if item]
    sensitive_indexes = [
        index
        for index, segment in enumerate(segments)
        if segment in {"secret", "token", "credential", "password"}
    ]
    if not sensitive_indexes:
        return False
    return all(
        index + 1 < len(segments) and segments[index + 1] in SAFE_SECRET_VALUE_FOLLOWERS
        for index in sensitive_indexes
    )


def _full_secret_like_token_at(text: str, start: int) -> str:
    end = start
    while end < len(text) and (text[end].isalnum() or text[end] in "._-"):
        end += 1
    return text[start:end]


def _redact_secret_like_token_value(match: re.Match[str]) -> str:
    value = match.group(0)
    if (
        match.end() + 1 < len(match.string)
        and match.string[match.end()] in "._-"
        and match.string[match.end() + 1].isalnum()
        and _is_safe_secret_like_value(_full_secret_like_token_at(match.string, match.start()))
    ):
        return value
    if _is_safe_secret_like_value(value):
        return value
    return "[redacted-secret]"


def redact_memory_text(value: object, *, mode: str = MEMORY_REDACTION_MODE_STANDARD) -> str:
    redaction_mode = normalize_memory_redaction_mode(mode)
    text = "" if value is None else str(value)
    text = QUOTED_SECRET_FIELD_PATTERN.sub(_redact_quoted_secret_field, text)
    text = SECRET_ASSIGNMENT_PATTERN.sub(_redact_secret_assignment, text)
    text = BEARER_TOKEN_PATTERN.sub("Bearer [redacted-secret]", text)
    text = SECRET_LIKE_TOKEN_VALUE_PATTERN.sub(_redact_secret_like_token_value, text)
    text = EMAIL_PATTERN.sub("[redacted-email]", text)
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
