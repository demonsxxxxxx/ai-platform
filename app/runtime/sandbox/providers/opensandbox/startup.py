"""Bounded OpenSandbox cold-start sequencing and safe failure evidence."""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


class OpenSandboxStartupStage(str, Enum):
    """One externally observable step in a new OpenSandbox startup."""

    CREATE = "create"
    ENDPOINT = "endpoint"
    READBACK = "readback"
    HEALTH = "health"
    IDENTITY = "identity"


_SDK_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class OpenSandboxStartupEvidence:
    """Safe provider evidence retained without a provider message or endpoint."""

    stage: OpenSandboxStartupStage
    sdk_error_code: str | None
    request_id: str | None

    @classmethod
    def from_exception(
        cls,
        stage: OpenSandboxStartupStage,
        exc: BaseException,
    ) -> "OpenSandboxStartupEvidence":
        error = getattr(exc, "error", None)
        return cls(
            stage=stage,
            sdk_error_code=_safe_sdk_error_code(getattr(error, "code", None)),
            request_id=_safe_request_id(getattr(exc, "request_id", None)),
        )

    def private_payload(self) -> dict[str, str | None]:
        """Return only the fields approved for private operational handling."""

        return {
            "provider": "opensandbox",
            "startup_stage": self.stage.value,
            "sdk_error_code": self.sdk_error_code,
            "request_id": self.request_id,
        }


class OpenSandboxStartupEvidenceCarrier:
    """Explicit private-evidence capability for preserving an existing typed error."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._opensandbox_startup_evidence: OpenSandboxStartupEvidence | None = None

    def attach_opensandbox_startup_evidence(self, evidence: OpenSandboxStartupEvidence) -> None:
        """Attach validated private evidence without changing a public error taxonomy."""

        if not isinstance(evidence, OpenSandboxStartupEvidence):
            raise TypeError("OpenSandbox startup evidence is invalid")
        self._opensandbox_startup_evidence = evidence

    @property
    def opensandbox_startup_evidence(self) -> OpenSandboxStartupEvidence | None:
        """Return the typed private evidence, if this error originated during startup."""

        return self._opensandbox_startup_evidence


class OpenSandboxStartupFailure(Exception):
    """Internal failure carrying the stage, safe SDK evidence, and cleanup subject."""

    def __init__(
        self,
        *,
        stage: OpenSandboxStartupStage,
        cause: BaseException,
        sandbox: Any | None,
    ) -> None:
        super().__init__("OpenSandbox startup failed")
        self.stage = stage
        self.cause = cause
        self.sandbox = sandbox
        self.evidence = OpenSandboxStartupEvidence.from_exception(stage, cause)


@dataclass(frozen=True)
class OpenSandboxStartupOperations:
    """Private callbacks that implement one provider-specific cold-start sequence."""

    create: Callable[[], Awaitable[Any] | Any]
    resolve_endpoint: Callable[[Any], Awaitable[tuple[str, dict[str, str]]] | tuple[str, dict[str, str]]]
    readback: Callable[[Any, str], Awaitable[str] | str]
    health: Callable[[str, dict[str, str]], Awaitable[int] | int]
    identity: Callable[[str, dict[str, str]], Awaitable[None] | None]


@dataclass(frozen=True)
class OpenSandboxStartupResult:
    """Sealed values produced only after every cold-start stage succeeds."""

    sandbox: Any
    sandbox_id: str
    executor_url: str
    executor_headers: dict[str, str]
    healthcheck_latency_ms: int


class OpenSandboxStartupSequence:
    """Run the complete create-to-identity sequence through one internal seam."""

    def __init__(
        self,
        operations: OpenSandboxStartupOperations,
        *,
        passthrough_error_types: tuple[type[BaseException], ...] = (),
        typed_error_types: tuple[type[BaseException], ...] = (),
        typed_error_evidence_attacher: Callable[[BaseException, OpenSandboxStartupEvidence], None] | None = None,
    ) -> None:
        self._operations = operations
        self._passthrough_error_types = passthrough_error_types
        self._typed_error_types = typed_error_types
        self._typed_error_evidence_attacher = typed_error_evidence_attacher

    async def launch(self) -> OpenSandboxStartupResult:
        sandbox = await self._at_stage(OpenSandboxStartupStage.CREATE, None, self._operations.create)
        executor_url, executor_headers = await self._at_stage(
            OpenSandboxStartupStage.ENDPOINT,
            sandbox,
            self._operations.resolve_endpoint,
            sandbox,
        )
        sandbox_id = await self._at_stage(
            OpenSandboxStartupStage.READBACK,
            sandbox,
            self._operations.readback,
            sandbox,
            executor_url,
        )
        healthcheck_latency_ms = await self._at_stage(
            OpenSandboxStartupStage.HEALTH,
            sandbox,
            self._operations.health,
            executor_url,
            executor_headers,
        )
        await self._at_stage(
            OpenSandboxStartupStage.IDENTITY,
            sandbox,
            self._operations.identity,
            executor_url,
            executor_headers,
        )
        return OpenSandboxStartupResult(
            sandbox=sandbox,
            sandbox_id=sandbox_id,
            executor_url=executor_url,
            executor_headers=executor_headers,
            healthcheck_latency_ms=healthcheck_latency_ms,
        )

    async def _at_stage(
        self,
        stage: OpenSandboxStartupStage,
        sandbox: Any | None,
        operation: Callable[..., Awaitable[Any] | Any],
        *args: Any,
    ) -> Any:
        try:
            return await _maybe_await(operation(*args))
        except asyncio.CancelledError:
            raise
        except self._passthrough_error_types:
            raise
        except self._typed_error_types as exc:
            if self._typed_error_evidence_attacher is not None:
                self._typed_error_evidence_attacher(exc, OpenSandboxStartupEvidence.from_exception(stage, exc))
            raise
        except OpenSandboxStartupFailure:
            raise
        except Exception as exc:
            raise OpenSandboxStartupFailure(stage=stage, cause=exc, sandbox=sandbox) from None


async def launch_opensandbox_startup(
    operations: OpenSandboxStartupOperations,
    *,
    passthrough_error_types: tuple[type[BaseException], ...] = (),
    typed_error_types: tuple[type[BaseException], ...] = (),
    typed_error_evidence_attacher: Callable[[BaseException, OpenSandboxStartupEvidence], None] | None = None,
) -> OpenSandboxStartupResult:
    """Launch one sequence while keeping typed provider errors intact."""

    return await OpenSandboxStartupSequence(
        operations,
        passthrough_error_types=passthrough_error_types,
        typed_error_types=typed_error_types,
        typed_error_evidence_attacher=typed_error_evidence_attacher,
    ).launch()


async def resolve_executor_endpoint(
    sandbox: Any,
    settings: Any,
    *,
    error_factory: Callable[[str], BaseException],
) -> tuple[str, dict[str, str]]:
    """Resolve the executor endpoint without exposing an SDK response object."""

    endpoint = await _maybe_await(sandbox.get_endpoint(port=18000))
    headers = _endpoint_headers(endpoint)
    url = getattr(endpoint, "endpoint", None)
    if url is None and isinstance(endpoint, dict):
        url = endpoint.get("endpoint")
    if not isinstance(url, str) or not url.strip():
        raise error_factory("OpenSandbox executor endpoint unavailable")
    return _opensandbox_executor_url(url, settings), headers


async def cleanup_started_sandbox(
    sandbox: Any | None,
    *,
    propagate_authoritative_not_found: bool = False,
) -> bool:
    """Stop then close a rejected sandbox while retaining only an explicit SDK 404."""

    if sandbox is None:
        return True
    killed = False
    not_found_error: BaseException | None = None
    try:
        kill = getattr(sandbox, "kill", None)
        if kill is None:
            raise RuntimeError("OpenSandbox sandbox stop failed")
        await _maybe_await(kill())
        killed = True
    except Exception as exc:
        if propagate_authoritative_not_found and is_authoritative_not_found_error(exc):
            not_found_error = exc
    try:
        close = getattr(sandbox, "close", None)
        if close is not None:
            await _maybe_await(close())
    except Exception:
        pass
    if not_found_error is not None:
        raise not_found_error
    return killed


def identity_unavailable_cleanup_subject(run_id: str, attempt_id: str) -> dict[str, str]:
    """Return the minimal release-blocker subject when the provider supplied no ID."""

    return {
        "provider": "opensandbox",
        "cleanup_state": "provider_identity_unavailable",
        "run_id": run_id,
        "attempt_id": attempt_id,
    }


async def retry_untracked_sandbox_cleanup(
    pending: dict[tuple[str, str], Any],
    cleanup_key: tuple[str, str],
) -> bool:
    """Retry only the retained SDK object and report whether cleanup is now confirmed."""

    sandbox = pending.get(cleanup_key)
    if sandbox is None:
        return True
    if not await cleanup_started_sandbox(sandbox):
        return False
    pending.pop(cleanup_key, None)
    return True


def unhealthy_readiness_fields(elapsed_ms: int) -> dict[str, object]:
    """Return the fixed safe fields for an unhealthy OpenSandbox executor probe."""

    return {
        "readiness_phase": "health_probe",
        "container_state": "unknown",
        "exit_code": None,
        "oom_killed": None,
        "published_port_observed": True,
        "health_outcome": "unhealthy",
        "elapsed_ms": elapsed_ms,
    }


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_sdk_error_code(value: object) -> str | None:
    if not isinstance(value, str) or _SDK_ERROR_CODE.fullmatch(value) is None:
        return None
    return value


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        return None
    return value


def _endpoint_headers(endpoint: Any) -> dict[str, str]:
    headers = getattr(endpoint, "headers", None)
    if headers is None and isinstance(endpoint, dict):
        headers = endpoint.get("headers")
    if not isinstance(headers, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if key is None or value is None:
            continue
        header_name = str(key).strip()
        header_value = str(value)
        if header_name:
            normalized[header_name] = header_value
    return normalized


def _opensandbox_executor_url(raw_url: str, settings: Any) -> str:
    url = raw_url.strip().rstrip("/")
    if not url:
        return ""
    if url.startswith("//"):
        protocol = str(getattr(settings, "opensandbox_protocol", "http") or "http").strip() or "http"
        return f"{protocol}:{url}"
    if "://" not in url:
        protocol = str(getattr(settings, "opensandbox_protocol", "http") or "http").strip() or "http"
        return f"{protocol}://{url.lstrip('/')}"
    return url


def is_authoritative_not_found_error(exc: BaseException) -> bool:
    """Recognize only the OpenSandbox SDK's explicit HTTP 404 response."""

    try:
        from opensandbox.exceptions import SandboxApiException
    except ImportError:
        return False
    return isinstance(exc, SandboxApiException) and getattr(exc, "status_code", None) == 404
