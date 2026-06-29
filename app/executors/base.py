from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from app.control_plane_contracts import RUN_PAYLOAD_SCHEMA_VERSION
from app.skills.release_policy import validate_release_decision_lock


ADAPTER_RESULT_SCHEMA_VERSION = "ai-platform.executor-result.v1"
ExecutorEventSink = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_type: str
    label: str
    content_type: str
    storage_key: str
    size_bytes: int
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorResult:
    status: str
    adapter_version: str
    executor_type: str
    executor_version: str
    capabilities: dict[str, bool]
    result: dict[str, Any] = field(default_factory=dict)
    artifacts: list[ArtifactManifest] = field(default_factory=list)
    executor_payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ADAPTER_RESULT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != ADAPTER_RESULT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported executor result schema: {self.schema_version}")
        if self.status not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported executor status: {self.status}")
        if not self.adapter_version:
            raise ValueError("adapter_version is required")
        if not self.executor_type:
            raise ValueError("executor_type is required")
        if not self.executor_version:
            raise ValueError("executor_version is required")
        for artifact in self.artifacts:
            if not artifact.artifact_type or not artifact.storage_key:
                raise ValueError("artifact_type and storage_key are required for artifacts")


@dataclass(frozen=True)
class RunPayload:
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str
    skill_id: str
    file_ids: list[str]
    input: dict[str, Any]
    trace_id: str = ""
    skill_version: str = ""
    release_decision: dict[str, Any] = field(default_factory=dict)
    skill_manifests: list[dict[str, Any]] = field(default_factory=list)
    context_snapshot_id: str = ""
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    context_pack: dict[str, Any] = field(default_factory=dict)
    model_id: str = ""
    model_value: str = ""
    schema_version: str = RUN_PAYLOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUN_PAYLOAD_SCHEMA_VERSION:
            raise ValueError("run_payload_schema_version_invalid")
        validate_release_decision_lock(
            release_decision=self.release_decision,
            skill_version=self.skill_version,
            skill_id=self.skill_id,
            skill_manifests=self.skill_manifests,
        )


class ExecutorAdapter(Protocol):
    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        """Execute a platform run and return a normalized platform result."""
