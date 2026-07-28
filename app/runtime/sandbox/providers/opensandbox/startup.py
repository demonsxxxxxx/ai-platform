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
_REQUEST_ID = re.compile(r"[A-Za-z0-9._~+/=-]{1,128}")


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
    ) -> None:
        self._operations = operations
        self._passthrough_error_types = passthrough_error_types

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
        except OpenSandboxStartupFailure:
            raise
        except Exception as exc:
            raise OpenSandboxStartupFailure(stage=stage, cause=exc, sandbox=sandbox) from None


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
