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
    """Return the private release-blocker subject for a missing provider identity."""

    return {
        "provider": "opensandbox",
        "cleanup_state": "provider_identity_unavailable",
        "run_id": run_id,
        "attempt_id": attempt_id,
    }


async def reconcile_authoritative_identity_unavailable_cleanup(
    provider: Any,
    *,
    request: Any,
    workspace: Any,
    settings: Any,
    metadata: dict[str, str],
    cache_key: tuple[str, str],
    required: bool,
    cleanup_subject: dict[str, str],
    status_from_info: Callable[[Any], Any],
    sealed_metadata_for_id: Callable[[str], dict[str, str]],
    cleanup_error: Callable[[str, dict[str, str]], BaseException],
) -> bool:
    """Recover one exact remote candidate through the provider's tracked stop path only."""

    try:
        manager = await provider._manager(provider._connection_config(settings))
        try:
            infos = await provider._list_all_sandbox_infos(manager, metadata)
        finally:
            await provider._close_manager(manager)
        statuses = []
        for info in infos or []:
            status = status_from_info(info)
            labels = status.detail.get("labels") if status is not None else None
            if (
                status is None
                or not status.container_id
                or not isinstance(labels, dict)
                or any(str(labels.get(key) or "") != value for key, value in metadata.items())
            ):
                raise cleanup_error("sandbox cleanup inventory is not an exact authorized match", cleanup_subject)
            statuses.append(status)
        if not statuses:
            if required:
                raise cleanup_error("sandbox cleanup candidate is unavailable", cleanup_subject)
            return False
        if len(statuses) != 1:
            raise cleanup_error("sandbox cleanup inventory is ambiguous", cleanup_subject)
        candidate = statuses[0]
        sandbox = await provider._connect(
            candidate.container_id,
            provider._connection_config(settings),
            skip_health_check=True,
        )
        if str(getattr(sandbox, "id", "") or "") != candidate.container_id:
            raise cleanup_error("sandbox cleanup candidate identity is unavailable", cleanup_subject)
        provider._track_cleanup_pending_sandbox(
            sandbox,
            request,
            workspace,
            metadata=sealed_metadata_for_id(candidate.container_id),
            executor_auth_token="",
        )
        result = await provider.stop(provider._leases[cache_key], reason="startup_identity_unavailable")
        if result.status in {"stopped", "not_found"}:
            return True
        raise cleanup_error("sandbox cleanup could not be confirmed", cleanup_subject)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        if getattr(exc, "error_code", None) == "container_cleanup_failed":
            raise
        raise cleanup_error("sandbox cleanup inventory is unavailable", cleanup_subject) from exc


async def cleanup_new_sandbox_or_reconcile(
    provider: Any,
    *,
    sandbox: Any | None,
    request: Any,
    workspace: Any,
    metadata: dict[str, str],
    executor_auth_token: str,
    original_error: BaseException | None,
    reconcile_identity: Callable[[dict[str, str]], Awaitable[bool]],
    cleanup_error: Callable[[str, dict[str, str] | None, Any | None], BaseException],
) -> None:
    """Clean a rejected sandbox, recovering a missing SDK ID only through inventory."""

    if await cleanup_started_sandbox(sandbox):
        return
    cleanup_subject = None
    if sandbox is not None:
        cleanup_subject = provider._track_cleanup_pending_sandbox(
            sandbox,
            request,
            workspace,
            metadata=metadata,
            executor_auth_token=executor_auth_token,
        )
        if cleanup_subject is not None:
            try:
                await reconcile_identity(cleanup_subject)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                error = exc
                if getattr(error, "error_code", None) != "container_cleanup_failed":
                    error = cleanup_error("sandbox cleanup inventory is unavailable", cleanup_subject, original_error)
                if hasattr(error, "readiness_evidence") and original_error is not None:
                    error.readiness_evidence = getattr(original_error, "readiness_evidence", None)
                if original_error is not None and hasattr(error, "attach_opensandbox_startup_evidence"):
                    evidence = getattr(original_error, "opensandbox_startup_evidence", None)
                    if evidence is not None:
                        error.attach_opensandbox_startup_evidence(evidence)
                raise error
            return
    message = "sandbox cleanup could not be confirmed without a provider identity" if cleanup_subject else "sandbox cleanup could not be confirmed"
    error = cleanup_error(message, cleanup_subject, original_error)
    if original_error is not None and hasattr(error, "attach_opensandbox_startup_evidence"):
        evidence = getattr(original_error, "opensandbox_startup_evidence", None)
        if evidence is not None:
            error.attach_opensandbox_startup_evidence(evidence)
    raise error


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
