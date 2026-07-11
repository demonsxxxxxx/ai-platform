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


def test_real_runtime_lease_requires_provider_source_and_evidence_class():
    module = _module()
    real = {
        "provider": "docker",
        "lease_payload_json": {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
        },
    }

    assert module.is_accepted_runtime_lease(real) is True
    assert module.is_accepted_runtime_lease({**real, "provider": "fake"}) is False
    assert module.is_accepted_runtime_lease(
        {
            **real,
            "lease_payload_json": {
                "source": "sdk_only_lifecycle_placeholder",
                "evidence_class": "sdk_only_lifecycle_placeholder",
            },
        }
    ) is False
