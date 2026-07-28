import asyncio
import importlib

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
        ("POOL_ACQUIRE_FAILED", "123e4567-e89b-12d3-a456-426614174000", "POOL_ACQUIRE_FAILED", "123e4567-e89b-12d3-a456-426614174000"),
        ("POOL_ACQUIRE_FAILED", "request_668-A", "POOL_ACQUIRE_FAILED", "request_668-A"),
        ("unsafe code", "request-668", None, "request-668"),
        ("POOL_ACQUIRE_FAILED", "request id contains whitespace", "POOL_ACQUIRE_FAILED", None),
        ("POOL_ACQUIRE_FAILED", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature", "POOL_ACQUIRE_FAILED", None),
        ("POOL_ACQUIRE_FAILED", "request/path", "POOL_ACQUIRE_FAILED", None),
        ("POOL_ACQUIRE_FAILED", "request+token", "POOL_ACQUIRE_FAILED", None),
        ("POOL_ACQUIRE_FAILED", "request=token", "POOL_ACQUIRE_FAILED", None),
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


@pytest.mark.asyncio
async def test_identityless_cleanup_recovers_one_exact_remote_candidate(monkeypatch):
    from test_sandbox_container_provider import (
        FakeOpenSandbox,
        FakeOpenSandboxManager,
        OpenSandboxSettings,
        opensandbox_provider,
        request,
        workspace,
    )

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    original_create = FakeOpenSandbox.create
    recovered: list[object] = []

    def create_without_id(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.id = ""
        sandbox.kill_error = RuntimeError("private stop detail")
        sandbox.close_error = RuntimeError("private close detail")
        remote = FakeOpenSandbox(sandbox_id="osb-recovered", metadata=kwargs["metadata"])
        FakeOpenSandbox.instances[remote.id] = remote
        FakeOpenSandboxManager.sandboxes = [remote]
        recovered.append(remote)
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_without_id)
    with pytest.raises(container_provider.ContainerStartFailedError, match="sandbox start failed"):
        await opensandbox_provider().create_or_reuse(request(), workspace())

    assert recovered[0].killed is True
    assert recovered[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("inventory", ["zero", "ambiguous", "cross_scope"])
async def test_identityless_cleanup_fails_closed_for_nonunique_or_mismatched_inventory(monkeypatch, inventory):
    from test_sandbox_container_provider import (
        FakeOpenSandbox,
        FakeOpenSandboxManager,
        OpenSandboxSettings,
        opensandbox_provider,
        request,
        workspace,
    )

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    original_create = FakeOpenSandbox.create
    remote_candidates: list[object] = []

    def create_without_id(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.id = ""
        sandbox.kill_error = RuntimeError("private stop detail")
        sandbox.close_error = RuntimeError("private close detail")
        metadata = dict(kwargs["metadata"])
        if inventory == "cross_scope":
            metadata["ai-platform.tenant_id"] = "tenant-other"
        count = 2 if inventory == "ambiguous" else 1
        remote_candidates[:] = [
            FakeOpenSandbox(sandbox_id=f"osb-candidate-{index}", metadata=metadata)
            for index in range(count)
        ]
        for candidate in remote_candidates:
            FakeOpenSandbox.instances[candidate.id] = candidate
        FakeOpenSandboxManager.sandboxes = [] if inventory == "zero" else list(remote_candidates)
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_without_id)
    with pytest.raises(container_provider.ContainerCleanupFailedError) as exc_info:
        await opensandbox_provider().create_or_reuse(request(), workspace())

    assert exc_info.value.cleanup_subject["cleanup_state"] == "provider_identity_unavailable"
    assert all(candidate.killed is False for candidate in remote_candidates)


@pytest.mark.asyncio
async def test_concurrent_same_scope_inventory_is_never_kept_in_provider_memory(monkeypatch):
    from test_sandbox_container_provider import (
        FakeOpenSandbox,
        FakeOpenSandboxManager,
        OpenSandboxSettings,
        opensandbox_provider,
        request,
        workspace,
    )

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    seed = await opensandbox_provider().create_or_reuse(request(), workspace())
    remote = FakeOpenSandbox.instances[seed.container_id]
    duplicate = FakeOpenSandbox(sandbox_id="osb-duplicate", metadata=remote.metadata)
    FakeOpenSandbox.instances[duplicate.id] = duplicate
    FakeOpenSandboxManager.sandboxes = [remote, duplicate]
    first, second = opensandbox_provider(), opensandbox_provider()

    results = await asyncio.gather(
        first.create_or_reuse(request(), workspace()),
        second.create_or_reuse(request(), workspace()),
        return_exceptions=True,
    )

    assert all(isinstance(result, container_provider.ContainerCleanupFailedError) for result in results)
    assert remote.killed is False and duplicate.killed is False
    assert not hasattr(first, "_untracked_cleanup_pending")
    assert not hasattr(second, "_untracked_cleanup_pending")
