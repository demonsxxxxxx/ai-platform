import importlib
import importlib.util


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


def test_real_runtime_lease_requires_canonical_governed_egress_proof():
    module = _module()
    proof = module.build_governed_egress_proof(
        provider="docker",
        runtime_subject="runtime-subject-a",
        policy_subject="policy-subject-a",
        callback_subject="callback-subject-a",
        denial_subject="denial-subject-a",
    )
    real = {
        "provider": "docker",
        "lease_payload_json": {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "governed_egress_proof": proof,
        },
    }

    assert module.is_accepted_runtime_lease(real) is True
    assert module.is_accepted_runtime_lease({**real, "provider": "fake"}) is False
    assert module.is_accepted_runtime_lease(
        {
            **real,
            "lease_payload_json": {
                "source": "sandbox_runtime",
                "evidence_class": "runtime_lease_projection",
                "labels": {module.GOVERNED_EGRESS_PROOF_LABEL: module.governed_egress_proof_label(proof)},
            },
        }
    ) is False
    assert module.is_accepted_runtime_lease(
        {
            **real,
            "lease_payload_json": {
                "source": "sdk_only_lifecycle_placeholder",
                "evidence_class": "sdk_only_lifecycle_placeholder",
            },
        }
    ) is False


def test_runtime_lease_rejects_legacy_and_forged_governed_egress_proofs():
    module = _module()
    legacy = {
        "provider": "opensandbox",
        "lease_payload_json": {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
        },
    }
    forged = {
        **legacy,
        "lease_payload_json": {
            **legacy["lease_payload_json"],
            "governed_egress_proof": {
                "schema_version": module.GOVERNED_EGRESS_PROOF_SCHEMA,
                "provider": "opensandbox",
                "default_deny_outbound": True,
                "governed_callback_exception": True,
                "policy_bound_enforcement": True,
                "runtime_subject_sha256": "a" * 64,
                "policy_subject_sha256": "b" * 64,
                "callback_subject_sha256": "c" * 64,
                "denial_subject_sha256": "d" * 64,
                "unredacted_callback_url": "http://private.test/callback",
            },
        },
    }

    assert module.is_accepted_runtime_lease(legacy) is False
    assert module.is_accepted_runtime_lease(forged) is False
