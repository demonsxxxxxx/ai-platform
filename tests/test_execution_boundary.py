import importlib
import importlib.util
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

PROOF_KEY = "proof-key-for-tests-with-enough-independent-entropy-2026"


def _module():
    spec = importlib.util.find_spec("app.execution_boundary")
    assert spec is not None, "execution boundary deep module is missing"
    return importlib.import_module("app.execution_boundary")


def test_claude_single_run_requires_real_sandbox_contract():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="claude-agent-worker",
        execution_mode="",
        execution_tier="sdk_only_writing",
        mcp_requires_sandbox=False,
    )

    assert decision.requires_real_sandbox is True
    assert decision.accepted_providers == frozenset({"docker", "opensandbox"})
    assert decision.permission_policy == "sandbox_brokered"
    assert decision.evidence_source == "sandbox_runtime"
    assert decision.evidence_class == "runtime_lease_projection"
    assert decision.fail_closed is False


def test_unknown_claude_tier_fails_closed_without_local_execution():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="claude-agent-worker",
        execution_mode="",
        execution_tier="unknown_writing_tier",
        mcp_requires_sandbox=False,
    )

    assert decision.requires_real_sandbox is True
    assert decision.fail_closed is True
    assert decision.local_sdk_allowed is False


@pytest.mark.parametrize("mcp_requires_sandbox", [False, True])
def test_non_parked_multi_agent_fails_closed(mcp_requires_sandbox):
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="claude-agent-worker",
        execution_mode="multi_agent",
        execution_tier="heavy_sandbox",
        mcp_requires_sandbox=mcp_requires_sandbox,
    )

    assert decision.fail_closed is True
    assert decision.local_sdk_allowed is False


def test_non_claude_adapter_keeps_adapter_managed_execution():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="ragflow",
        execution_mode="",
        execution_tier="sdk_only_writing",
        mcp_requires_sandbox=False,
    )

    assert decision.requires_real_sandbox is False
    assert decision.permission_policy == "adapter_managed"
    assert decision.fail_closed is False


def test_mcp_requirement_forces_real_sandbox_without_synthetic_execution_tier():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="claude-agent-worker",
        execution_mode="",
        execution_tier="",
        mcp_requires_sandbox=True,
    )

    assert decision.requires_real_sandbox is True
    assert decision.permission_policy == "sandbox_brokered"
    assert decision.fail_closed is False
    assert decision.reason == "mcp_execution_requires_real_sandbox"


def test_mcp_requirement_preserves_non_claude_worker_sandbox_override():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="ragflow",
        execution_mode="",
        execution_tier="",
        mcp_requires_sandbox=True,
    )

    assert decision.requires_real_sandbox is True
    assert decision.permission_policy == "sandbox_brokered"
    assert decision.fail_closed is False
    assert decision.reason == "mcp_execution_requires_real_sandbox"


def test_invalid_mcp_requirement_fails_closed_without_local_execution():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="ragflow",
        execution_mode="",
        execution_tier="",
        mcp_requires_sandbox=None,
    )

    assert decision.requires_real_sandbox is True
    assert decision.fail_closed is True
    assert decision.local_sdk_allowed is False
    assert decision.reason == "invalid_mcp_sandbox_requirement"


def _real_runtime_lease(module, *, signing_key=PROOF_KEY, key_id="current", **overrides):
    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "qat-attempt-a",
        "image_subject": "registry.test/executor@sha256:" + "a" * 64,
        "image_digest": "sha256:" + "a" * 64,
        "authorized_skill_scope": module.governed_egress_authorized_skill_scope(
            skill_ids=["general-chat"], mcp_tool_ids=["knowledge.search"]
        ),
        "authorized_native_tool_scope": module.governed_egress_authorized_native_tool_scope([]),
        "lease_identity": "docker:executor-exec-run-a:exec-run-a",
    }
    proof = module.build_governed_egress_proof(
        signing_key=signing_key,
        key_id=key_id,
        provider="docker",
        runtime_subject="docker-internal-bridge",
        policy_subject="network-id:network-name:internal",
        callback_subject="http://api.sandbox.internal:8020",
        denial_subject="network-id:internal-default-deny",
        network_id="network-id",
        network_name="ai-platform-sandbox-egress-internal-v1",
        network_internal=True,
        **scope,
    )
    row = {
        "provider": "docker",
        **{key: scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id")},
        "lease_payload_json": {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "container_id": "exec-run-a",
            "container_name": "executor-exec-run-a",
            "labels": {"ai-platform.attempt_id": scope["attempt_id"]},
            **{
                f"governed_egress_{field}": proof[field]
                for field in (
                    "image_subject_sha256",
                    "image_digest_sha256",
                    "authorized_skill_scope_sha256",
                    "authorized_native_tool_scope_sha256",
                )
            },
            "governed_egress_proof": proof,
        },
    }
    row.update(overrides)
    return row


def _trusted_internal_runtime_lease(module):
    labels = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": "tenant-a",
        "ai-platform.workspace_id": "workspace-a",
        "ai-platform.user_id": "user-a",
        "ai-platform.session_id": "session-a",
        "ai-platform.run_id": "run-a",
        "ai-platform.attempt_id": "qat-attempt-a",
        "ai-platform.sandbox_mode": "ephemeral",
        "ai-platform.browser_enabled": "false",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.security_profile": "trusted_internal",
    }
    return {
        "provider": "opensandbox",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
        "runtime_container_id": "osb-run-a",
        "runtime_container_name": "opensandbox-run-a-qat-attempt-a",
        "runtime_executor_url": "http://osb-run-a.opensandbox.test:18000",
        "runtime_workspace_container_path": "/workspace",
        "lease_payload_json": {
            "source": module.REAL_SANDBOX_EVIDENCE_SOURCE,
            "evidence_class": module.REAL_SANDBOX_EVIDENCE_CLASS,
            "security_profile": "trusted_internal",
            "attempt_id": "qat-attempt-a",
            "container_id": "osb-run-a",
            "container_name": "opensandbox-run-a-qat-attempt-a",
            "executor_url": "http://osb-run-a.opensandbox.test:18000",
            "workspace_host_path": "/runtime/workspace",
            "workspace_container_path": "/workspace",
            "labels": labels,
        },
    }


def test_trusted_internal_runtime_lease_requires_current_profile_and_exact_bindings(monkeypatch):
    module = _module()
    current = SimpleNamespace(sandbox_security_profile="trusted_internal")
    monkeypatch.setattr(module, "get_settings", lambda: current)
    real = _trusted_internal_runtime_lease(module)

    assert module.is_accepted_runtime_lease(real) is True

    mutations = []
    for path, value in (
        (("run_id",), "run-b"),
        (("sandbox_mode",), "persistent"),
        (("runtime_container_id",), "osb-other"),
        (("lease_payload_json", "attempt_id"), "qat-attempt-b"),
        (("lease_payload_json", "labels", "ai-platform.workspace_id"), "workspace-b"),
        (("lease_payload_json", "labels", "ai-platform.security_profile"), "governed"),
    ):
        candidate = deepcopy(real)
        target = candidate
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        mutations.append(candidate)
    for candidate in mutations:
        assert module.is_accepted_runtime_lease(candidate) is False

    with_governed_proof = deepcopy(real)
    with_governed_proof["lease_payload_json"]["governed_egress_proof"] = {}
    assert module.is_accepted_runtime_lease(with_governed_proof) is False
    with_extra_label = deepcopy(real)
    with_extra_label["lease_payload_json"]["labels"]["ai-platform.unreviewed"] = "unexpected"
    assert module.is_accepted_runtime_lease(with_extra_label) is False
    current.sandbox_security_profile = "governed"
    assert module.is_accepted_runtime_lease(real) is False


def test_real_runtime_lease_requires_canonical_signed_governed_egress_proof():
    module = _module()
    real = _real_runtime_lease(module)

    assert module.is_accepted_runtime_lease(real, signing_key=PROOF_KEY) is True
    assert module.is_accepted_runtime_lease({**real, "provider": "fake"}, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(
        {
            **real,
            "lease_payload_json": {
                "source": "sandbox_runtime",
                "evidence_class": "runtime_lease_projection",
                "labels": {},
            },
        },
        signing_key=PROOF_KEY,
    ) is False
    assert module.is_accepted_runtime_lease(
        {
            **real,
            "lease_payload_json": {
                "source": "sdk_only_lifecycle_placeholder",
                "evidence_class": "sdk_only_lifecycle_placeholder",
            },
        },
        signing_key=PROOF_KEY,
    ) is False


def test_runtime_lease_rejects_legacy_shape_tamper_replay_and_expiry():
    module = _module()
    real = _real_runtime_lease(module)
    legacy = {**real, "lease_payload_json": {"source": "sandbox_runtime", "evidence_class": "runtime_lease_projection"}}
    tampered = _real_runtime_lease(module)
    tampered["lease_payload_json"]["governed_egress_proof"]["run_id_sha256"] = "b" * 64
    replayed = _real_runtime_lease(module, run_id="run-b")
    expired = _real_runtime_lease(module)
    expired["status"] = "released"
    expired["lease_payload_json"]["governed_egress_proof"] = module.build_governed_egress_proof(
        signing_key=PROOF_KEY,
        provider="docker",
        runtime_subject="docker-internal-bridge",
        policy_subject="network-id:network-name:internal",
        callback_subject="http://api.sandbox.internal:8020",
        denial_subject="network-id:internal-default-deny",
        network_id="network-id",
        network_name="ai-platform-sandbox-egress-internal-v1",
        network_internal=True,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="qat-attempt-a",
        image_subject="registry.test/executor@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope=module.governed_egress_authorized_skill_scope(
            skill_ids=["general-chat"], mcp_tool_ids=["knowledge.search"]
        ),
        authorized_native_tool_scope=module.governed_egress_authorized_native_tool_scope([]),
        lease_identity="docker:executor-exec-run-a:exec-run-a",
        issued_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert module.is_accepted_runtime_lease(legacy, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(tampered, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(replayed, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(expired, signing_key=PROOF_KEY) is False
    expired["status"] = "active"
    assert module.is_accepted_runtime_lease(
        expired,
        signing_key=PROOF_KEY,
        verification_mode="historical",
    ) is False
    expired["status"] = "released"
    assert module.is_accepted_runtime_lease(
        expired,
        signing_key=PROOF_KEY,
        verification_mode="historical",
    ) is True
    assert module.has_governed_egress_signing_key("") is False
    assert module.has_governed_egress_signing_key("too-short") is False


def test_governed_egress_attempt_is_required_signed_and_exactly_bound():
    module = _module()
    real = _real_runtime_lease(module)
    proof = real["lease_payload_json"]["governed_egress_proof"]
    legacy = dict(proof)
    legacy.pop("attempt_id_sha256")

    assert module.is_governed_egress_proof(
        proof,
        provider="docker",
        signing_key=PROOF_KEY,
        expected_binding={"attempt_id": "qat-attempt-a"},
    ) is True
    assert module.is_governed_egress_proof(
        proof,
        provider="docker",
        signing_key=PROOF_KEY,
        expected_binding={"attempt_id": "qat-attempt-b"},
    ) is False
    assert module.is_governed_egress_proof(
        legacy,
        provider="docker",
        signing_key=PROOF_KEY,
    ) is False
    with pytest.raises(ValueError, match="governed_egress_subject_invalid"):
        module.build_governed_egress_proof(
            signing_key=PROOF_KEY,
            provider="docker",
            runtime_subject="docker-internal-bridge",
            policy_subject="network-id:network-name:internal",
            callback_subject="http://api.sandbox.internal:8020",
            denial_subject="network-id:internal-default-deny",
            network_id="network-id",
            network_name="ai-platform-sandbox-egress-internal-v1",
            network_internal=True,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            attempt_id="",
            image_subject="registry.test/executor@sha256:" + "a" * 64,
            image_digest="sha256:" + "a" * 64,
            authorized_skill_scope="[]",
            authorized_native_tool_scope="[]",
            lease_identity="docker:executor-exec-run-a:exec-run-a",
        )


def test_runtime_lease_key_rotation_allows_only_bounded_previous_terminal_history():
    module = _module()
    previous_key = "previous-proof-key-for-tests-with-enough-entropy-2026"
    current_key = "current-proof-key-for-tests-with-enough-entropy-2026"
    row = _real_runtime_lease(module, signing_key=previous_key, key_id="previous-2026")
    row["status"] = "released"

    assert module.is_accepted_runtime_lease(
        row,
        signing_key=current_key,
        signing_key_id="current-2026",
        previous_signing_keys={"previous-2026": previous_key},
        verification_mode="active",
    ) is False
    assert module.is_accepted_runtime_lease(
        row,
        signing_key=current_key,
        signing_key_id="current-2026",
        previous_signing_keys={"previous-2026": previous_key},
        verification_mode="historical",
    ) is True
    assert module.is_accepted_runtime_lease(
        row,
        signing_key=current_key,
        signing_key_id="current-2026",
        previous_signing_keys={"unknown-key": previous_key},
        verification_mode="historical",
    ) is False
