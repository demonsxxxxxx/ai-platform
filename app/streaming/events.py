"""Public v4 protocol boundary for platform adapters."""

from __future__ import annotations

from typing import Final, TypeAlias

from app.streaming.domain import protocol_v4 as _protocol_v4

INTERNAL_STREAM_EVENT_SCHEMA_V4: Final = _protocol_v4.INTERNAL_STREAM_EVENT_SCHEMA
PUBLIC_RUN_STREAM_SCHEMA_V4: Final = _protocol_v4.PUBLIC_RUN_STREAM_SCHEMA
PUBLIC_STREAM_EVENT_TYPES_V4: Final = _protocol_v4.PUBLIC_STREAM_EVENT_TYPES
STREAM_DESIGN_ID_V4: Final = _protocol_v4.STREAM_DESIGN_ID
STREAM_PROJECTION_VERSION_V4: Final = _protocol_v4.STREAM_PROJECTION_VERSION
PublicRunStreamEventV4: TypeAlias = _protocol_v4.PublicRunStreamEventV4

PUBLIC_APPLICATION_EVENT_TYPES_V4: Final = frozenset(
    value for value in PUBLIC_STREAM_EVENT_TYPES_V4
    if not value.startswith("stream.")
)
