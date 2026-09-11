"""Public in-process contracts owned by the Sandbox bounded context."""

from dataclasses import asdict, dataclass
from re import fullmatch
from typing import Literal, Mapping

from app.sandbox.domain.runtime_diagnostics import (
    SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT as SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT,
)
from app.sandbox.domain.runtime_diagnostics import (
    SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES as SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
)
from app.sandbox.domain.runtime_diagnostics import (
    SDK_RUNTIME_DIAGNOSTIC_LIFECYCLE_LIMIT as SDK_RUNTIME_DIAGNOSTIC_LIFECYCLE_LIMIT,
)
from app.sandbox.domain.runtime_diagnostics import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION as SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
)
from app.sandbox.domain.runtime_diagnostics import (
    normalize_sdk_runtime_diagnostics as normalize_sdk_runtime_diagnostics,
)
from app.sandbox.domain.runtime_diagnostics import (
    runtime_diagnostic_text as runtime_diagnostic_text,
)
from app.sandbox.domain.runtime_diagnostics import (
    runtime_diagnostic_value as runtime_diagnostic_value,
)


@dataclass(frozen=True, slots=True)
class AssistantAnswerReceipt:
    """Bounded receipt for a persisted assistant delta sequence."""

    schema_version: Literal["ai-platform.assistant-answer-receipt.v1"]
    message_id: str
    delta_count: int
    text_length: int
    last_delta_event_id: str

    def __post_init__(self) -> None:
        if self.schema_version != "ai-platform.assistant-answer-receipt.v1":
            raise ValueError("assistant answer receipt schema version is invalid")
        if not isinstance(self.delta_count, int) or isinstance(self.delta_count, bool) or self.delta_count <= 0:
            raise ValueError("delta_count must be a positive integer")
        if not isinstance(self.text_length, int) or isinstance(self.text_length, bool) or self.text_length <= 0:
            raise ValueError("text_length must be a positive integer")
        for field_name in ("message_id", "last_delta_event_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
                raise ValueError(f"{field_name} contains unsupported characters")

    @classmethod
    def model_validate(cls, value: object) -> "AssistantAnswerReceipt":
        if not isinstance(value, Mapping):
            raise ValueError("assistant answer receipt fields are invalid")
        expected_fields = {
            "schema_version",
            "message_id",
            "delta_count",
            "text_length",
            "last_delta_event_id",
        }
        if set(value) != expected_fields:
            raise ValueError("extra or missing assistant answer receipt fields")
        return cls(**value)

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        del mode
        return asdict(self)

