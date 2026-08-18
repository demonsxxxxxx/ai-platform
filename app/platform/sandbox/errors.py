"""Stable sandbox provider error contracts."""

from __future__ import annotations

from app.runtime.sandbox import readiness_evidence as readiness_contracts
from app.runtime.sandbox.providers.opensandbox.startup import (
    OpenSandboxStartupEvidence,
    OpenSandboxStartupEvidenceCarrier,
)


class SandboxRuntimeError(OpenSandboxStartupEvidenceCarrier, RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class DockerUnavailableError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker SDK is unavailable") -> None:
        super().__init__("docker_unavailable", message)


class OpenSandboxUnavailableError(SandboxRuntimeError):
    """Raised when the optional OpenSandbox SDK cannot be imported or used."""

    def __init__(self, message: str = "OpenSandbox SDK is unavailable") -> None:
        super().__init__("opensandbox_unavailable", message)


class OpenSandboxCapabilityAdmissionError(SandboxRuntimeError):
    """Raised before OpenSandbox dispatch when external egress is unproven."""

    def __init__(
        self,
        message: str = "OpenSandbox external-egress capability admission failed",
    ) -> None:
        super().__init__("opensandbox_capability_admission_failed", message)


class GovernedEgressAdmissionError(SandboxRuntimeError):
    """Raised before sandbox side effects when default-deny egress is unproven."""

    def __init__(self) -> None:
        super().__init__(
            "sandbox_egress_unavailable",
            "Governed sandbox egress is unavailable; contact an operator.",
        )


class DockerPermissionDeniedError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker permission denied") -> None:
        super().__init__("docker_permission_denied", message)


class ContainerStartFailedError(SandboxRuntimeError):
    def __init__(self, message: str = "Container start failed") -> None:
        super().__init__("container_start_failed", message)


class OpenSandboxStartupFailedError(ContainerStartFailedError):
    """Generic public startup failure with safe private OpenSandbox evidence."""

    def __init__(
        self,
        evidence: OpenSandboxStartupEvidence,
        message: str = "OpenSandbox sandbox start failed",
    ) -> None:
        super().__init__(message)
        self.private_evidence = evidence.private_payload()


class NativeToolAdmissionError(SandboxRuntimeError):
    """Raised when the isolated native-command sidecar cannot become ready."""

    def __init__(self, message: str = "Native tool sandbox admission failed") -> None:
        super().__init__("native_tool_admission_failed", message)


class ContainerCleanupFailedError(SandboxRuntimeError):
    """Raised when a rejected executor cannot be confirmed stopped and removed."""

    def __init__(
        self,
        message: str = "Container cleanup failed",
        *,
        readiness_evidence: readiness_contracts.ExecutorReadinessEvidence | None = None,
        cleanup_subject: dict[str, str] | None = None,
    ) -> None:
        super().__init__("container_cleanup_failed", message)
        self.readiness_evidence: readiness_contracts.ExecutorReadinessEvidence | None = (
            readiness_evidence
        )
        self.cleanup_subject = cleanup_subject


class ExecutorHealthTimeoutError(SandboxRuntimeError):
    def __init__(
        self,
        message: str = "Executor health timeout",
        *,
        readiness_evidence: readiness_contracts.ExecutorReadinessEvidence | None = None,
    ) -> None:
        super().__init__("executor_health_timeout", message)
        self.readiness_evidence: readiness_contracts.ExecutorReadinessEvidence | None = (
            readiness_evidence
        )
