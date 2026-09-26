"""PostgreSQL value encoding, size errors, and scalar coercion."""

from __future__ import annotations

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.limits import PersistenceSizeLimitError
from app.platform.postgres.limits import ensure_json_size
from app.platform.postgres.limits import ensure_text_size
from datetime import datetime
from datetime import timezone
from typing import Any
import json
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _require_json_size(value: Any, *, max_bytes: int, code: str) -> None:
    try:
        ensure_json_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


def _require_text_size(value: str, *, max_bytes: int, code: str) -> None:
    try:
        ensure_text_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


def dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)
