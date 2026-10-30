"""Public in-process contracts owned by the Sandbox bounded context."""

from app.sandbox.application.workspace_versions import (
    restore_workspace_skill_outputs as restore_workspace_skill_outputs,
    restore_workspace_version as restore_workspace_version,
    save_workspace_version as save_workspace_version,
    verify_workspace_version as verify_workspace_version,
)
from app.sandbox.domain.workspace_version import WorkspaceVersionError as WorkspaceVersionError

from app.sandbox.application.artifact_transfer import ArtifactStreamStager as ArtifactStreamStager

from app.sandbox.application.callback_authority import (
    ExecutorTerminalReceiptIdentity as ExecutorTerminalReceiptIdentity,
    ProviderSessionAppendIdentity as ProviderSessionAppendIdentity,
    SandboxCallbackIdentity as SandboxCallbackIdentity,
    SandboxCallbackAuthority as SandboxCallbackAuthority,
    SandboxCallbackAuthorityError as SandboxCallbackAuthorityError,
)

from app.sandbox.application.diagnostic_capture import (
    capture_sdk_runtime_diagnostics as capture_sdk_runtime_diagnostics,
)

from app.sandbox.application.dispatch_intent import (
    DispatchOutcome as DispatchOutcome,
    SandboxDispatchIdentity as SandboxDispatchIdentity,
    SandboxDispatchIntentRepository as SandboxDispatchIntentRepository,
    SandboxDispatchIntentService as SandboxDispatchIntentService,
    SandboxDispatchOutcomeUnknown as SandboxDispatchOutcomeUnknown,
)

from app.sandbox.application.reconciliation_receipt import (
    SandboxReconciliationReceiptAuthority as SandboxReconciliationReceiptAuthority,
    finalize_stopped_executor_checkpoint as finalize_stopped_executor_checkpoint,
)
from app.sandbox.application.cancel_delivery import (
    SandboxCancelDeliveryService as SandboxCancelDeliveryService,
)

from app.sandbox.application.runtime_cleanup import (
    SandboxCleanupPersistenceError as SandboxCleanupPersistenceError,
    SandboxRuntimeCleanup as SandboxRuntimeCleanup,
    SandboxRuntimeCleanupError as SandboxRuntimeCleanupError,
)
from app.sandbox.application.lease_ports import (
    SandboxExecutorCallbackLeasePort as SandboxExecutorCallbackLeasePort,
    SandboxExecutorReconciliationPort as SandboxExecutorReconciliationPort,
    SandboxRunLeaseReader as SandboxRunLeaseReader,
    SandboxRuntimeLeasePort as SandboxRuntimeLeasePort,
    SandboxLeaseTransportPort as SandboxLeaseTransportPort,
)

from dataclasses import asdict, dataclass
from re import fullmatch
from typing import Any, Literal, Mapping, Protocol


from app.sandbox.domain.executor_failure import (
    SandboxExecutorHttpError as SandboxExecutorHttpError,
    canonical_executor_reported_failure_code as canonical_executor_reported_failure_code,
    executor_reported_failure_message as executor_reported_failure_message,
    normalize_executor_reported_failure as normalize_executor_reported_failure,
)
from app.sandbox.domain.lease_errors import (
    SandboxExecutorTerminalConflictError as SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError as SandboxLeaseReleaseScopeMismatchError,
)
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
    SandboxExecutorHttpFailure as SandboxExecutorHttpFailure,
)
from app.sandbox.domain.runtime_diagnostics import (
    SandboxRuntimeFailure as SandboxRuntimeFailure,
)
from app.sandbox.domain.runtime_diagnostics import (
    exception_chain_from_error as exception_chain_from_error,
)
from app.sandbox.domain.runtime_diagnostics import (
    normalize_sdk_runtime_diagnostics as normalize_sdk_runtime_diagnostics,
)
from app.sandbox.domain.runtime_diagnostics import (
    runtime_diagnostics_rejection as runtime_diagnostics_rejection,
)
from app.sandbox.domain.runtime_diagnostics import (
    runtime_diagnostic_text as runtime_diagnostic_text,
)
from app.sandbox.domain.runtime_diagnostics import (
    runtime_diagnostic_value as runtime_diagnostic_value,
)
from app.sandbox.domain.workspace_policy import (
    WORKSPACE_RUNTIME_PRIVATE_ROOTS as WORKSPACE_RUNTIME_PRIVATE_ROOTS,
)
from app.sandbox.domain.workspace_policy import (
    PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME as PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME,
)
from app.sandbox.domain.workspace_policy import (
    workspace_collection_directory_allowed as workspace_collection_directory_allowed,
)
from app.sandbox.domain.workspace_policy import (
    workspace_collection_file_allowed as workspace_collection_file_allowed,
)
from app.sandbox.domain.workspace_policy import (
    workspace_delivery_file_allowed as workspace_delivery_file_allowed,
)
from app.sandbox.domain.workspace_policy import (
    workspace_mutation_allowed as workspace_mutation_allowed,
)
from app.sandbox.domain.workspace_policy import (
    workspace_read_allowed as workspace_read_allowed,
)
from app.sandbox.domain.workspace_delivery import (
    opensandbox_delivery_files as opensandbox_delivery_files,
)
from app.sandbox.domain.host_bind import (
    OpenSandboxHostBindError as OpenSandboxHostBindError,
    normalize_host_bind_relative_path as normalize_host_bind_relative_path,
    read_host_bind_control_file as read_host_bind_control_file,
    resolve_opensandbox_host_bind_source as resolve_opensandbox_host_bind_source,
    snapshot_host_bind_delivery_files as snapshot_host_bind_delivery_files,
)


class SandboxRuntimeDebugReader(Protocol):
    """Read bounded same-Run lease facts without handles, paths or credentials."""

    async def __call__(self, conn: Any, *, tenant_id: str, run_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AssistantAnswerReceipt:
    """Bounded receipt for a persisted assistant delta sequence."""

    schema_version: Literal[
        "ai-platform.assistant-answer-receipt.v1",
        "ai-platform.assistant-answer-receipt.v2",
    ]
    message_id: str
    delta_count: int
    text_length: int
    last_delta_event_id: str

    def __post_init__(self) -> None:
        if self.schema_version not in (
            "ai-platform.assistant-answer-receipt.v1",
            "ai-platform.assistant-answer-receipt.v2",
        ):
            raise ValueError("assistant answer receipt schema version is invalid")
        if (
            not isinstance(self.delta_count, int)
            or isinstance(self.delta_count, bool)
            or self.delta_count <= 0
        ):
            raise ValueError("delta_count must be a positive integer")
        if (
            not isinstance(self.text_length, int)
            or isinstance(self.text_length, bool)
            or self.text_length <= 0
        ):
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
