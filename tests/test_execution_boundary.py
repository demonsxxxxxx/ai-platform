import importlib
import importlib.util
from datetime import datetime, timedelta, timezone


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
    )

    assert decision.requires_real_sandbox is True
    assert decision.fail_closed is True
    assert decision.local_sdk_allowed is False


def test_non_parked_multi_agent_fails_closed():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="claude-agent-worker",
        execution_mode="multi_agent",
        execution_tier="heavy_sandbox",
    )

    assert decision.fail_closed is True
    assert decision.local_sdk_allowed is False


def test_non_claude_adapter_keeps_adapter_managed_execution():
    module = _module()

    decision = module.decide_execution_boundary(
        executor_type="ragflow",
        execution_mode="",
        execution_tier="sdk_only_writing",
    )

    assert decision.requires_real_sandbox is False
    assert decision.permission_policy == "adapter_managed"
    assert decision.fail_closed is False


def _real_runtime_lease(module, **overrides):
    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "image_subject": "registry.test/executor@sha256:" + "a" * 64,
        "image_digest": "sha256:" + "a" * 64,
        "authorized_skill_scope": module.governed_egress_authorized_skill_scope(
            skill_ids=["general-chat"], mcp_tool_ids=["knowledge.search"]
        ),
        "authorized_native_tool_scope": module.governed_egress_authorized_native_tool_scope([]),
        "lease_identity": "docker:executor-exec-run-a",
    }
    proof = module.build_governed_egress_proof(
        signing_key=PROOF_KEY,
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
            "labels": {},
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
        image_subject="registry.test/executor@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope=module.governed_egress_authorized_skill_scope(
            skill_ids=["general-chat"], mcp_tool_ids=["knowledge.search"]
        ),
        authorized_native_tool_scope=module.governed_egress_authorized_native_tool_scope([]),
        lease_identity="docker:executor-exec-run-a",
        issued_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert module.is_accepted_runtime_lease(legacy, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(tampered, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(replayed, signing_key=PROOF_KEY) is False
    assert module.is_accepted_runtime_lease(expired, signing_key=PROOF_KEY) is False
    assert module.has_governed_egress_signing_key("") is False
    assert module.has_governed_egress_signing_key("too-short") is False
