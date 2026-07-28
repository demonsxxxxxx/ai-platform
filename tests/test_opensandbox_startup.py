import asyncio

import pytest

from app.runtime.sandbox.providers.opensandbox.startup import (
    OpenSandboxStartupEvidence,
    OpenSandboxStartupFailure,
    OpenSandboxStartupOperations,
    OpenSandboxStartupSequence,
    OpenSandboxStartupStage,
)


class FakeSdkError(RuntimeError):
    def __init__(self, *, error_code: str, request_id: str) -> None:
        super().__init__("private provider detail")
        self.error = type("FakeSdkErrorCode", (), {"code": error_code})()
        self.request_id = request_id


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage", tuple(OpenSandboxStartupStage))
async def test_startup_sequence_preserves_stage_evidence_and_cleanup_subject(failed_stage):
    sandbox = object()
    sdk_error = FakeSdkError(error_code="POOL_ACQUIRE_FAILED", request_id="request-668")

    def create():
        if failed_stage is OpenSandboxStartupStage.CREATE:
            raise sdk_error
        return sandbox

    def endpoint(_sandbox):
        if failed_stage is OpenSandboxStartupStage.ENDPOINT:
            raise sdk_error
        return "https://executor.test", {"X-Endpoint": "private"}

    def readback(_sandbox, _executor_url):
        if failed_stage is OpenSandboxStartupStage.READBACK:
            raise sdk_error
        return "sandbox-668"

    def health(_executor_url, _headers):
        if failed_stage is OpenSandboxStartupStage.HEALTH:
            raise sdk_error
        return 7

    def identity(_executor_url, _headers):
        if failed_stage is OpenSandboxStartupStage.IDENTITY:
            raise sdk_error

    startup = OpenSandboxStartupSequence(
        OpenSandboxStartupOperations(
            create=create,
            resolve_endpoint=endpoint,
            readback=readback,
            health=health,
            identity=identity,
        )
    )

    with pytest.raises(OpenSandboxStartupFailure) as exc_info:
        await startup.launch()

    failure = exc_info.value
    expected_sandbox = None if failed_stage is OpenSandboxStartupStage.CREATE else sandbox
    assert failure.stage is failed_stage
    assert failure.sandbox is expected_sandbox
    assert failure.evidence.private_payload() == {
        "provider": "opensandbox",
        "startup_stage": failed_stage.value,
        "sdk_error_code": "POOL_ACQUIRE_FAILED",
        "request_id": "request-668",
    }
    assert "private provider detail" not in str(failure)


@pytest.mark.parametrize(
    ("error_code", "request_id", "expected_code", "expected_request_id"),
    (
        ("POOL_ACQUIRE_FAILED", "request-668", "POOL_ACQUIRE_FAILED", "request-668"),
        ("unsafe code", "request-668", None, "request-668"),
        ("POOL_ACQUIRE_FAILED", "request id contains whitespace", "POOL_ACQUIRE_FAILED", None),
        ("x" * 129, "x" * 129, None, None),
    ),
)
def test_startup_evidence_allowlists_sdk_code_and_request_id(
    error_code,
    request_id,
    expected_code,
    expected_request_id,
):
    evidence = OpenSandboxStartupEvidence.from_exception(
        OpenSandboxStartupStage.CREATE,
        FakeSdkError(error_code=error_code, request_id=request_id),
    )

    assert evidence.sdk_error_code == expected_code
    assert evidence.request_id == expected_request_id


@pytest.mark.asyncio
async def test_startup_sequence_preserves_cancellation():
    def cancelled_create():
        raise asyncio.CancelledError()

    startup = OpenSandboxStartupSequence(
        OpenSandboxStartupOperations(
            create=cancelled_create,
            resolve_endpoint=lambda _sandbox: pytest.fail("endpoint must not run"),
            readback=lambda _sandbox, _executor_url: pytest.fail("readback must not run"),
            health=lambda _executor_url, _headers: pytest.fail("health must not run"),
            identity=lambda _executor_url, _headers: pytest.fail("identity must not run"),
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await startup.launch()


@pytest.mark.asyncio
async def test_startup_sequence_preserves_configured_policy_error():
    class PolicyDenied(Exception):
        pass

    policy_error = PolicyDenied("policy admission rejected")
    startup = OpenSandboxStartupSequence(
        OpenSandboxStartupOperations(
            create=lambda: object(),
            resolve_endpoint=lambda _sandbox: ("https://executor.test", {}),
            readback=lambda _sandbox, _executor_url: (_ for _ in ()).throw(policy_error),
            health=lambda _executor_url, _headers: pytest.fail("health must not run"),
            identity=lambda _executor_url, _headers: pytest.fail("identity must not run"),
        ),
        passthrough_error_types=(PolicyDenied,),
    )

    with pytest.raises(PolicyDenied) as exc_info:
        await startup.launch()

    assert exc_info.value is policy_error
