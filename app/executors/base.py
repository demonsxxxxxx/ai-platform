import asyncio
from dataclasses import dataclass, field
import inspect
from typing import Any, Awaitable, Callable, Coroutine, Protocol, TypeVar

from app.control_plane_contracts import RUN_PAYLOAD_SCHEMA_VERSION
from app.skills.release_policy import validate_release_decision_lock


ADAPTER_RESULT_SCHEMA_VERSION = "ai-platform.executor-result.v1"
ExecutorEventSink = Callable[..., Awaitable[None]]
RunStopCallback = Callable[[str], Awaitable[bool | None] | bool | None]
_ExecutionResult = TypeVar("_ExecutionResult")


@dataclass(frozen=True)
class RunStopResult:
    """Observable result of one bounded execution-owner stop attempt."""

    status: str
    quiescent: bool
    detail: str = ""


class RunExecutionOwner:
    """Own one run task and its adapter-registered external stop operation."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._task: asyncio.Task[Any] | None = None
        self._stop_callbacks: list[RunStopCallback] = []
        self._stop_lock = asyncio.Lock()
        self._stop_attempt: asyncio.Task[RunStopResult] | None = None

    @property
    def done(self) -> bool:
        return self._task is not None and self._task.done()

    def start(self, execution: Coroutine[Any, Any, _ExecutionResult]) -> asyncio.Task[_ExecutionResult]:
        """Start and own exactly one execution task."""

        if self._task is not None:
            raise RuntimeError("run_execution_already_started")
        task = asyncio.create_task(execution, name=f"run-executor-{self.run_id}")
        self._task = task
        return task

    def start_adapter(
        self,
        adapter: Any,
        payload: "RunPayload",
        *,
        event_sink: ExecutorEventSink | None,
    ) -> asyncio.Task["ExecutorResult"]:
        """Start an adapter, exposing this owner only when its seam accepts it."""

        submit_run = adapter.submit_run
        try:
            parameters = inspect.signature(submit_run).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        accepts_owner = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or parameter.name == "execution_owner"
            for parameter in parameters
        )
        kwargs: dict[str, Any] = {"event_sink": event_sink}
        if accepts_owner:
            kwargs["execution_owner"] = self
        return self.start(submit_run(payload, **kwargs))

    def register_stop(self, callback: RunStopCallback) -> None:
        """Register the adapter's authoritative external-writer stop operation."""

        if callback not in self._stop_callbacks:
            self._stop_callbacks.append(callback)

    async def wait(self) -> _ExecutionResult:
        """Wait for the owned execution result without changing ownership."""

        if self._task is None:
            raise RuntimeError("run_execution_not_started")
        return await self._task

    async def _call_stop_callbacks(self, reason: str) -> RunStopResult | None:
        for callback in tuple(self._stop_callbacks):
            try:
                result = callback(reason)
                if inspect.isawaitable(result):
                    result = await result
            except asyncio.CancelledError:
                raise
            except Exception:
                return RunStopResult(status="failed", quiescent=False, detail="external_stop_failed")
            if result is False:
                return RunStopResult(status="failed", quiescent=False, detail="external_stop_failed")
        return None

    async def _stop_once(self, reason: str) -> RunStopResult:
        if self._task is None:
            return RunStopResult(status="quiescent", quiescent=True)
        callback_failure = await self._call_stop_callbacks(reason)
        if callback_failure is not None:
            return callback_failure
        if not self._task.done() and self._task.cancelling() == 0:
            self._task.cancel()
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if not self._task.done():
                raise
        except Exception:
            pass
        return RunStopResult(status="quiescent", quiescent=True)

    async def stop(self, *, reason: str, timeout_seconds: float) -> RunStopResult:
        """Request stop and confirm quiescence within one finite attempt."""

        async with self._stop_lock:
            if self._stop_attempt is None or self._stop_attempt.done():
                self._stop_attempt = asyncio.create_task(
                    self._stop_once(reason),
                    name=f"run-stop-{self.run_id}",
                )
            stop_task = self._stop_attempt
            done, _ = await asyncio.wait(
                {stop_task},
                timeout=max(float(timeout_seconds), 0.0),
            )
            if stop_task in done:
                return stop_task.result()
            return RunStopResult(status="timed_out", quiescent=False, detail="stop_timeout")


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
    async def submit_run(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> ExecutorResult:
        """Execute a platform run and return a normalized platform result."""
