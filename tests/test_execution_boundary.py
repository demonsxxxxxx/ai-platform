import importlib
import importlib.util
from datetime import datetime, timedelta, timezone

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


def test_injected_non_harness_test_adapter_keeps_adapter_managed_execution():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="test-adapter",
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


def test_mcp_requirement_preserves_injected_test_adapter_sandbox_override():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="test-adapter",
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
        executor_type="test-adapter",
        execution_mode="",
        execution_tier="",
        mcp_requires_sandbox=None,
    )

    assert decision.requires_real_sandbox is True
    assert decision.fail_closed is True
    assert decision.local_sdk_allowed is False
    assert decision.reason == "invalid_mcp_sandbox_requirement"


@pytest.mark.parametrize("provider", ["docker", "opensandbox"])
def test_governed_egress_native_tool_scope_hashes_authorized_large_policy_and_rejects_invalid_input(
    provider,
):
    module = _module()
    from app.settings import (
        DIRECT_OPENSANDBOX_NETWORK_NAME,
        DIRECT_OPENSANDBOX_POLICY_SUBJECT,
        DIRECT_OPENSANDBOX_PROFILE_ID,
    )
    authorized_policy = [
        {
            "identity": "Skill",
            "registered": True,
            "declared": True,
            "allowed_skill_names": [f"controlled-file-skill-{index:03d}" for index in range(256)],
        }
    ]

    scope = module.governed_egress_authorized_native_tool_scope(authorized_policy)
    changed_scope = module.governed_egress_authorized_native_tool_scope(
        [
            {
                **authorized_policy[0],
                "allowed_skill_names": [*authorized_policy[0]["allowed_skill_names"], "additional-skill"],
            }
        ]
    )

    assert scope.startswith("sha256:")
    assert len(scope) < 4096
    assert changed_scope != scope
    is_opensandbox = provider == "opensandbox"
    proof = module.build_governed_egress_proof(
        signing_key=PROOF_KEY,
        provider=provider,
        runtime_subject="runsc" if is_opensandbox else "docker-internal-bridge",
        policy_subject=(
            DIRECT_OPENSANDBOX_POLICY_SUBJECT
            if is_opensandbox
            else "network-id:network-name:internal"
        ),
        callback_subject="http://api.sandbox.internal:8020",
        denial_subject="network-id:internal-default-deny",
        network_id=DIRECT_OPENSANDBOX_PROFILE_ID if is_opensandbox else "network-id",
        network_name=(
            DIRECT_OPENSANDBOX_NETWORK_NAME
            if is_opensandbox
            else "ai-platform-sandbox-egress-internal-v1"
        ),
        network_internal=not is_opensandbox,
        default_deny_outbound=not is_opensandbox,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="qat-attempt-a",
        image_subject="registry.test/executor@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope=module.governed_egress_authorized_skill_scope(
            skill_ids=["general-chat"], mcp_tool_ids=[]
        ),
        authorized_native_tool_scope=scope,
        lease_identity="docker:executor-exec-run-a:exec-run-a",
    )
    assert module.is_governed_egress_proof(
        proof,
        provider=provider,
        signing_key=PROOF_KEY,
        expected_binding={"authorized_native_tool_scope": scope},
    ) is True
    assert module.is_governed_egress_proof(
        proof,
        provider=provider,
        signing_key=PROOF_KEY,
        expected_binding={"authorized_native_tool_scope": changed_scope},
    ) is False
    with pytest.raises(ValueError, match="governed_egress_scope_invalid"):
        module.governed_egress_authorized_native_tool_scope([{"identity": object()}])
    with pytest.raises(ValueError, match="governed_egress_scope_invalid"):
        module.governed_egress_authorized_native_tool_scope(
            [{"identity": "Skill", "allowed_skill_names": ["x" * (65 * 1024)]}]
        )


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


def _opensandbox_runtime_lease(
    module,
    *,
    legacy_internal=False,
    network_name=None,
):
    from app.settings import (
        DIRECT_OPENSANDBOX_NETWORK_NAME,
        DIRECT_OPENSANDBOX_POLICY_SUBJECT,
        DIRECT_OPENSANDBOX_PROFILE_ID,
        LEGACY_DIRECT_OPENSANDBOX_NETWORK_NAME,
        LEGACY_DIRECT_OPENSANDBOX_POLICY_SUBJECT,
    )

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
        "lease_identity": "opensandbox:opensandbox-run-a:osb-run-a",
    }
    network_name = network_name or (
        LEGACY_DIRECT_OPENSANDBOX_NETWORK_NAME
        if legacy_internal
        else DIRECT_OPENSANDBOX_NETWORK_NAME
    )
    proof = module.build_governed_egress_proof(
        signing_key=PROOF_KEY,
        provider="opensandbox",
        runtime_subject="runsc",
        policy_subject=(
            LEGACY_DIRECT_OPENSANDBOX_POLICY_SUBJECT
            if legacy_internal
            else DIRECT_OPENSANDBOX_POLICY_SUBJECT
        ),
        callback_subject="callback-boundary-a",
        denial_subject="deny-a",
        network_id=DIRECT_OPENSANDBOX_PROFILE_ID,
        network_name=network_name,
        network_internal=legacy_internal,
        default_deny_outbound=legacy_internal,
        **scope,
    )
    row = {
        "provider": "opensandbox",
        **{key: scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id")},
        "status": "active",
        "lease_payload_json": {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "container_id": "osb-run-a",
            "container_name": "opensandbox-run-a",
            "labels": {"ai-platform.attempt_id": scope["attempt_id"]},
            "governed_egress_network_name": network_name,
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
    return row


def test_opensandbox_public_proof_is_exact_and_legacy_is_history_only():
    module = _module()
    current = _opensandbox_runtime_lease(module)
    legacy = _opensandbox_runtime_lease(module, legacy_internal=True)

    assert module.is_accepted_runtime_lease(current, signing_key=PROOF_KEY) is True
    assert module.is_accepted_runtime_lease(legacy, signing_key=PROOF_KEY) is False
    assert module.is_governed_egress_proof(
        legacy["lease_payload_json"]["governed_egress_proof"],
        provider="opensandbox", signing_key=PROOF_KEY,
        allow_legacy_opensandbox=True,
    ) is False
    legacy["status"] = "released"
    assert module.is_accepted_runtime_lease(
        legacy,
        signing_key=PROOF_KEY,
        verification_mode="historical",
    ) is True

    public_proof = current["lease_payload_json"]["governed_egress_proof"]
    assert public_proof["network_internal"] is False
    assert public_proof["default_deny_outbound"] is False
    assert public_proof["policy_bound_enforcement"] is True
    assert public_proof["governed_callback_exception"] is True


@pytest.mark.parametrize(
    ("network_name", "policy_subject", "network_internal", "default_deny_outbound"),
    [
        ("ai-platform-opensandbox-egress-v2", "host-public-egress-v1", False, True),
        ("ai-platform-opensandbox-egress-v2", "host-public-egress-v1", True, False),
        ("unknown-network", "host-public-egress-v1", False, False),
        ("ai-platform-opensandbox-egress-v2", "unknown-policy", False, False),
    ],
)
def test_opensandbox_proof_rejects_posture_drift_and_unknown_networks(
    network_name,
    policy_subject,
    network_internal,
    default_deny_outbound,
):
    module = _module()
    proof = module.build_governed_egress_proof(
        signing_key=PROOF_KEY,
        provider="opensandbox",
        runtime_subject="runsc",
        policy_subject=policy_subject,
        callback_subject="callback-boundary-a",
        denial_subject="deny-a",
        network_id="direct-opensandbox",
        network_name=network_name,
        network_internal=network_internal,
        default_deny_outbound=default_deny_outbound,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        image_subject="registry.test/executor@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope=module.governed_egress_authorized_skill_scope(
            skill_ids=[], mcp_tool_ids=[]
        ),
        authorized_native_tool_scope=module.governed_egress_authorized_native_tool_scope([]),
        lease_identity="opensandbox:opensandbox-run-a:osb-run-a",
    )

    assert module.is_governed_egress_proof(
        proof,
        provider="opensandbox",
        signing_key=PROOF_KEY,
    ) is False


def test_opensandbox_public_proof_signature_tampering_is_rejected():
    module = _module()
    proof = _opensandbox_runtime_lease(module)["lease_payload_json"]["governed_egress_proof"]
    proof["signature"] = "0" * 64

    assert module.is_governed_egress_proof(
        proof,
        provider="opensandbox",
        signing_key=PROOF_KEY,
    ) is False


def test_opensandbox_named_network_proof_requires_the_expected_network(monkeypatch):
    from types import SimpleNamespace

    module = _module()
    network_name = "customer-egress-2026"
    current = _opensandbox_runtime_lease(module, network_name=network_name)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            opensandbox_expected_network_mode=network_name,
            sandbox_egress_proof_key_id="current",
            sandbox_egress_proof_previous_keys_json="",
        ),
    )

    assert module.is_accepted_runtime_lease(current, signing_key=PROOF_KEY) is True
    current["status"] = "released"
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            opensandbox_expected_network_mode="another-egress-network",
            sandbox_egress_proof_key_id="current",
            sandbox_egress_proof_previous_keys_json="",
        ),
    )
    assert module.is_accepted_runtime_lease(current, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(
        current,
        signing_key=PROOF_KEY,
        verification_mode="historical",
    ) is True


@pytest.mark.parametrize("mode", ["active", "historical"])
@pytest.mark.parametrize("missing", ["container_id", "container_name", "labels"])
@pytest.mark.parametrize("run_id", ["run-a", "run-b"])
def test_opensandbox_network_binding_cannot_repair_an_incomplete_scope(mode, missing, run_id):
    module = _module()
    row = _opensandbox_runtime_lease(module)
    row["run_id"] = run_id
    if mode == "historical":
        row["status"] = "released"
    row["lease_payload_json"].pop(missing)

    assert module.is_accepted_runtime_lease(
        row, signing_key=PROOF_KEY, verification_mode=mode,
    ) is False


def test_real_runtime_lease_requires_canonical_signed_governed_egress_proof():
    module = _module()
    real = _real_runtime_lease(module)
    proof = real["lease_payload_json"]["governed_egress_proof"]

    assert proof["network_internal"] is True
    assert proof["default_deny_outbound"] is True
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
