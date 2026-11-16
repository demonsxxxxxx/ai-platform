import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.control_plane_contracts import (
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    RUN_EXECUTION_KIND_SKILL,
    RUN_PAYLOAD_SCHEMA_VERSION,
    RUN_PAYLOAD_SCHEMA_VERSION_V2,
    SUPPORTED_RUN_PAYLOAD_SCHEMA_VERSIONS,
)
from app.executors.base import project_execution_spec_to_run_payload
from app.runs.domain import execution_spec as execution_spec_domain
from app.runs.application.execution_spec import model_auto_compact_window
from app.runs.api import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    EXECUTION_SPEC_SCHEMA_VERSION_V2,
    EXECUTION_SPEC_SCHEMA_VERSION_V3,
    ExecutionSpec,
    ExecutionSpecError,
    compile_execution_spec_for_dispatch,
    worker_dispatch_fence,
    compile_execution_spec,
)
from app.runs.infrastructure.postgres import load_worker_dispatch_run_facts


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_worker_dispatch_fence_relocks_exact_queued_run_and_fails_closed():
    identity = {"tenant_id": "tenant-a", "workspace_id": "workspace-a",
                "user_id": "alice@example.test", "session_id": "session-a",
                "run_id": "run-a", "agent_id": "agent-a",
                "execution_kind": "skill", "skill_id": "skill-a"}
    frozen = {"model_id": "model-a", "model_value": "model-a",
              "model_gateway_revision": 3, "auto_compact_window": 200_000,
              "max_output_tokens": 2048}
    row = {**identity, **frozen, "id": "run-a", "status": "queued",
           "cancel_requested_at": None, "context_snapshot_id": "ctx-a"}

    class Cursor:
        def __init__(self, value):
            self.value = value

        async def fetchone(self):
            return self.value

    class Connection:
        def __init__(self):
            self.value = row
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            return Cursor(self.value)

    conn = Connection()
    kwargs = {"run_identity": identity, "locked_run": frozen,
              "context_snapshot_id": "ctx-a", "reconciliation": False,
              "run_facts_loader": load_worker_dispatch_run_facts}
    assert await worker_dispatch_fence(conn, **kwargs) == "ready"
    assert "for update" in conn.calls[0][0] and conn.calls[0][1] == ("tenant-a", "run-a")
    conn.value = {**row, "model_gateway_revision": 4}
    assert await worker_dispatch_fence(conn, **kwargs) == "invalid"
    conn.value = {**row, "status": "succeeded"}
    assert await worker_dispatch_fence(conn, **kwargs) == "stale"
    conn.value = {**row, "cancel_requested_at": "requested"}
    assert await worker_dispatch_fence(conn, **kwargs) == "stale"
    conn.value = {**row, "status": "running"}
    assert await worker_dispatch_fence(conn, **{**kwargs, "reconciliation": True}) == "ready"
    conn.value = {**row, "status": "running", "cancel_requested_at": "requested"}
    assert await worker_dispatch_fence(conn, **{**kwargs, "reconciliation": True}) == "ready"
    conn.value["context_snapshot_id"] = "foreign"
    assert await worker_dispatch_fence(conn, **{**kwargs, "reconciliation": True}) == "invalid"


EXECUTION_SPEC_ARCHITECTURE = (
    ROOT / "docs/architecture/execution-spec-and-attempt-lifecycle.md"
)


def _spec_payload(**overrides):
    payload = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "run_payload_schema_version": RUN_PAYLOAD_SCHEMA_VERSION,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "alice@example.test",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
        "execution_kind": "skill",
        "skill_id": "skill-a",
        "file_ids": ["file-a"],
        "input": {"message": "hello", "options": {"b": 2, "a": 1}},
        "executor_type": "claude-agent-worker",
        "trace_id": "trace-a",
        "skill_version": "sha-a",
        "release_decision": {
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": False,
            "selected_version": "sha-a",
            "selected_track": "manifest_pin",
        },
        "skill_manifests": [{"skill_id": "skill-a", "content_hash": "sha-a"}],
        "context_snapshot_id": "context-a",
        "context_snapshot": {"context_snapshot_id": "context-a"},
        "context_pack": {"system": "safe"},
        "model_id": "model-a",
        "model_value": "model-a",
        "agent_profile": {},
    }
    payload.update(overrides)
    return payload


def test_execution_spec_is_deterministic_and_does_not_retain_caller_mutability():
    payload = _spec_payload()
    reordered = dict(reversed(list(payload.items())))

    first = compile_execution_spec(payload)
    second = compile_execution_spec(reordered)

    assert first == second
    assert first.canonical_json == second.canonical_json
    assert len(first.spec_sha256) == 64

    payload["file_ids"].append("file-b")
    payload["input"]["options"]["a"] = 99
    assert first.to_mapping()["file_ids"] == ["file-a"]
    assert first.to_mapping()["input"]["options"] == {"a": 1, "b": 2}

    projected = first.to_mapping()
    projected["file_ids"].append("file-c")
    assert first.to_mapping()["file_ids"] == ["file-a"]


def test_compiler_validates_once_but_canonical_restore_validates_again(monkeypatch):
    normalize = execution_spec_domain._normalize_execution_spec
    calls = 0

    def counted(payload):
        nonlocal calls
        calls += 1
        return normalize(payload)

    monkeypatch.setattr(execution_spec_domain, "_normalize_execution_spec", counted)
    spec = compile_execution_spec(_spec_payload())
    assert calls == 1
    assert ExecutionSpec.from_canonical_json(
        spec.canonical_json, expected_sha256=spec.spec_sha256
    ) == spec
    assert calls == 2


def test_execution_spec_wire_literals_match_the_legacy_projection_boundary():
    assert RUN_EXECUTION_KIND_SKILL == "skill"
    assert RUN_EXECUTION_KIND_HARNESS_CHAT == "harness_chat"
    assert SUPPORTED_RUN_PAYLOAD_SCHEMA_VERSIONS == frozenset(
        {RUN_PAYLOAD_SCHEMA_VERSION, RUN_PAYLOAD_SCHEMA_VERSION_V2}
    )
    for schema_version in SUPPORTED_RUN_PAYLOAD_SCHEMA_VERSIONS:
        spec = compile_execution_spec(
            _spec_payload(run_payload_schema_version=schema_version)
        )
        assert spec.to_mapping()["run_payload_schema_version"] == schema_version


def test_execution_spec_rejects_unknown_attempt_or_credential_fields():
    for forbidden in ("attempt_id", "owner_token", "provider_credentials"):
        with pytest.raises(ExecutionSpecError, match="execution_spec_fields_invalid"):
            compile_execution_spec({**_spec_payload(), forbidden: "secret"})


@pytest.mark.parametrize(
    "override",
    [
        {"input": {"authorization": "Bearer " + "a" * 32}},
        {"context_pack": {"nested": {"openai_api_key": "sk-" + "a" * 32}}},
        {"agent_profile": {"provider_credentials": {"token": "a" * 32}}},
        {"input": {"message": "ANTHROPIC_AUTH_TOKEN=" + "a" * 32}},
        {"input": {"aws_secret_access_key": "a" * 40}},
        {"context_pack": {"nested": {"aws_session_token": "a" * 40}}},
        {"input": {"message": "AWS_SECRET_ACCESS_KEY=" + "a" * 40}},
    ],
)
def test_execution_spec_rejects_nested_credential_material(override):
    with pytest.raises(
        ExecutionSpecError,
        match="execution_spec_credential_material_forbidden",
    ) as exc_info:
        compile_execution_spec(_spec_payload(**override))

    assert exc_info.value.code == "execution_spec_credential_material_forbidden"


def test_execution_spec_allows_safe_token_accounting_fields():
    spec = compile_execution_spec(
        _spec_payload(input={"token_budget": 1000, "token_counts": {"input": 12}})
    )

    assert spec.to_mapping()["input"]["token_budget"] == 1000


def test_execution_spec_preserves_raw_upstream_model_identity():
    spec = compile_execution_spec(_spec_payload(model_value="openai/gpt-5"))

    assert spec.to_mapping()["model_id"] == "model-a"
    assert spec.to_mapping()["model_value"] == "openai/gpt-5"


def test_execution_spec_preserves_legacy_empty_model_identity():
    spec = compile_execution_spec(_spec_payload(model_id="", model_value=""))

    assert spec.to_mapping()["model_id"] == ""
    assert spec.to_mapping()["model_value"] == ""


@pytest.mark.parametrize("model_value", [" openai/gpt-5", "openai/gpt-5\n", "x" * 513])
def test_execution_spec_rejects_unsafe_upstream_model_identity(model_value):
    with pytest.raises(
        ExecutionSpecError,
        match="execution_spec_model_value_invalid",
    ):
        compile_execution_spec(_spec_payload(model_value=model_value))


def test_execution_spec_accepts_skillless_harness_without_skill_authority():
    spec = compile_execution_spec(
        _spec_payload(
            run_payload_schema_version="ai-platform.run-payload.v2",
            execution_kind="harness_chat",
            skill_id=None,
            skill_version="",
            release_decision={},
            skill_manifests=[],
        )
    )

    assert spec.to_mapping()["execution_kind"] == "harness_chat"
    assert spec.to_mapping()["skill_id"] is None


def test_execution_spec_architecture_keeps_attempt_and_credentials_out_of_spec():
    decision = EXECUTION_SPEC_ARCHITECTURE.read_text(encoding="utf-8")

    assert "The completed migration gives every dispatch or redispatch" in decision
    assert "attempt_id" not in _spec_payload()
    assert "provider API keys" in decision
    assert "Queue wire shape remains compatible" in decision


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"schema_version": "future"}, "execution_spec_schema_version_invalid"),
        ({"execution_kind": "other"}, "execution_spec_execution_kind_invalid"),
        ({"skill_id": None}, "execution_spec_skill_identity_invalid"),
        ({"input": {"ratio": float("nan")}}, "execution_spec_json_value_invalid"),
    ],
)
def test_execution_spec_fails_closed_for_invalid_contract(override, code):
    with pytest.raises(ExecutionSpecError, match=code) as exc_info:
        compile_execution_spec(_spec_payload(**override))

    assert exc_info.value.code == code


@pytest.mark.parametrize(
    "override",
    [
        {"skill_version": ""},
        {"release_decision": {}},
        {
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "sha-other",
                "selected_track": "manifest_pin",
            }
        },
        {"skill_manifests": []},
        {"skill_manifests": [{"skill_id": "skill-a", "content_hash": "sha-other"}]},
    ],
)
def test_execution_spec_rejects_unlocked_skill_authority(override):
    with pytest.raises(
        ExecutionSpecError,
        match="execution_spec_skill_authority_invalid",
    ) as exc_info:
        compile_execution_spec(_spec_payload(**override))

    assert exc_info.value.code == "execution_spec_skill_authority_invalid"


@pytest.mark.parametrize(
    "override",
    [
        {"context_snapshot_id": ""},
        {"context_snapshot": {}},
        {"context_snapshot": {"context_snapshot_id": "context-other"}},
    ],
)
def test_execution_spec_rejects_missing_or_mismatched_context_identity(override):
    with pytest.raises(ExecutionSpecError, match="execution_spec_context_snapshot"):
        compile_execution_spec(_spec_payload(**override))


def test_execution_spec_canonical_reader_rejects_reencoding_and_digest_drift():
    spec = compile_execution_spec(_spec_payload())

    assert (
        ExecutionSpec.from_canonical_json(
            spec.canonical_json,
            expected_sha256=spec.spec_sha256,
        )
        == spec
    )
    noncanonical = json.dumps(spec.to_mapping(), ensure_ascii=False).encode("utf-8")
    with pytest.raises(ExecutionSpecError, match="execution_spec_json_not_canonical"):
        ExecutionSpec.from_canonical_json(noncanonical)
    with pytest.raises(ExecutionSpecError, match="execution_spec_digest_mismatch"):
        ExecutionSpec.from_canonical_json(spec.canonical_json, expected_sha256="0" * 64)

    with pytest.raises(ExecutionSpecError, match="execution_spec_digest_mismatch"):
        ExecutionSpec(canonical_json=spec.canonical_json, spec_sha256="0" * 64)


def test_dispatch_projection_preserves_legacy_run_payload_and_keeps_attempt_separate():
    payload = _spec_payload()
    spec = compile_execution_spec_for_dispatch(
        run_identity={
            key: payload[key]
            for key in (
                "tenant_id",
                "workspace_id",
                "user_id",
                "session_id",
                "run_id",
                "agent_id",
                "execution_kind",
                "skill_id",
            )
        },
        queue_payload=SimpleNamespace(
            **{
                **payload,
                "schema_version": payload["run_payload_schema_version"],
            }
        ),
        trace_id=payload["trace_id"],
        context_snapshot_id=payload["context_snapshot_id"],
        context_snapshot=payload["context_snapshot"],
        context_pack=payload["context_pack"],
        run_model_snapshot={"model_id": "model-a", "model_value": "model-a", "model_gateway_revision": 7,
                            "auto_compact_window": 200_000, "max_output_tokens": 2048},
    )

    assert spec.to_mapping()["schema_version"] == EXECUTION_SPEC_SCHEMA_VERSION_V3
    assert "attempt_id" not in spec.to_mapping()
    run_payload = project_execution_spec_to_run_payload(spec, attempt_id="attempt-a")
    assert run_payload.attempt_id == "attempt-a"
    assert run_payload.schema_version == RUN_PAYLOAD_SCHEMA_VERSION
    assert run_payload.file_ids == ["file-a"]
    assert run_payload.input == payload["input"]


@pytest.mark.parametrize("grouped", [False, True])
def test_dispatch_binds_budget_and_group_to_locked_run_not_queue_body(grouped):
    payload = _spec_payload()
    queue = SimpleNamespace(
        **{**payload, "schema_version": payload["run_payload_schema_version"]}
    )
    identity = {
        key: payload[key]
        for key in (
            "tenant_id", "workspace_id", "user_id", "session_id",
            "run_id", "agent_id", "execution_kind", "skill_id",
        )
    }
    locked_model = {
        "model_id": "model-a",
        "model_value": "model-a",
        "model_gateway_revision": 7,
        "auto_compact_window": 200_000,
        "max_output_tokens": 2048,
    }
    def dispatch(snapshot):
        return compile_execution_spec_for_dispatch(
            run_identity=identity,
            queue_payload=queue,
            trace_id=payload["trace_id"],
            context_snapshot_id=payload["context_snapshot_id"],
            context_snapshot=payload["context_snapshot"],
            context_pack=payload["context_pack"],
            run_model_snapshot=snapshot,
        )

    if grouped:
        locked_model.update(model_group_id="group-a", model_group_code="alpha", model_group_type="business",
                            model_group_revision=3, model_selection_mode="explicit", usage_scene="expert", model_policy_version="model-groups.v1")
    spec = dispatch(locked_model)
    if grouped:
        from app.executors.base import RunPayload
        from app.models import QueueRunPayload
        from app.execution.application.executor_reconciliation import sandbox_reconciliation_payload, restored_executor_reconciliation_queue_payload
        runtime = project_execution_spec_to_run_payload(spec, attempt_id="attempt-a")
        snapshot = sandbox_reconciliation_payload(runtime)
        restored, attempt = restored_executor_reconciliation_queue_payload(
            {"adapter_context":{}, "adapter_name":"claude-agent-worker", "run_payload":snapshot}, result={},
            run_payload_factory=RunPayload, queue_payload_factory=QueueRunPayload,
        )
        assert attempt == "attempt-a"
        for field in ("model_group_id", "model_group_code", "model_group_type", "model_group_revision",
                      "model_selection_mode", "usage_scene", "model_policy_version"):
            assert getattr(restored, field) == locked_model[field]
    with pytest.raises(ExecutionSpecError, match="execution_spec_model_snapshot_missing"):
        dispatch(None)
    assert spec.to_mapping()["schema_version"] == ("ai-platform.execution-spec.v4" if grouped else EXECUTION_SPEC_SCHEMA_VERSION_V3)
    assert spec.to_mapping()["model_auto_compact_window"] == 200_000
    assert spec.to_mapping()["model_max_output_tokens"] == 2048
    assert project_execution_spec_to_run_payload(spec, attempt_id="attempt-a").model_auto_compact_window == 200_000
    assert ExecutionSpec.from_canonical_json(spec.canonical_json) == spec
    for corrupt in (
        {**locked_model, "max_output_tokens": None},
        {**locked_model, "auto_compact_window": True},
        {**locked_model, "model_value": "other"},
    ):
        with pytest.raises(ExecutionSpecError, match="execution_spec_model_snapshot"):
            dispatch(corrupt)


def test_dispatch_compiler_rejects_queue_skill_identity_drift():
    payload = _spec_payload()
    queue_payload = SimpleNamespace(
        **{
            **payload,
            "schema_version": payload["run_payload_schema_version"],
            "skill_id": "skill-other",
        }
    )

    with pytest.raises(
        ExecutionSpecError,
        match="execution_spec_skill_identity_mismatch",
    ) as exc_info:
        compile_execution_spec_for_dispatch(
            run_identity={
                key: payload[key]
                for key in (
                    "tenant_id",
                    "workspace_id",
                    "user_id",
                    "session_id",
                    "run_id",
                    "agent_id",
                    "execution_kind",
                    "skill_id",
                )
            },
            queue_payload=queue_payload,
            trace_id=payload["trace_id"],
            context_snapshot_id=payload["context_snapshot_id"],
            context_snapshot=payload["context_snapshot"],
            context_pack=payload["context_pack"],
        )

    assert exc_info.value.code == "execution_spec_skill_identity_mismatch"


def test_dispatch_compiler_normalizes_skillless_harness_empty_string():
    payload = _spec_payload(
        run_payload_schema_version="ai-platform.run-payload.v2",
        execution_kind="harness_chat",
        skill_id=None,
        skill_version="",
        release_decision={},
        skill_manifests=[],
    )
    queue_payload = SimpleNamespace(
        **{
            **payload,
            "schema_version": payload["run_payload_schema_version"],
            "skill_id": "",
        }
    )

    spec = compile_execution_spec_for_dispatch(
        run_identity={
            key: "" if key == "skill_id" else payload[key]
            for key in (
                "tenant_id",
                "workspace_id",
                "user_id",
                "session_id",
                "run_id",
                "agent_id",
                "execution_kind",
                "skill_id",
            )
        },
        queue_payload=queue_payload,
        trace_id=payload["trace_id"],
        context_snapshot_id=payload["context_snapshot_id"],
        context_snapshot=payload["context_snapshot"],
        context_pack=payload["context_pack"],
        run_model_snapshot={"model_id": "model-a", "model_value": "model-a", "model_gateway_revision": 7,
                            "auto_compact_window": 200_000, "max_output_tokens": 2048},
    )

    assert spec.to_mapping()["skill_id"] is None


def test_v3_freezes_the_compaction_window_and_v2_stays_byte_identical():
    """The field migration must not rewrite admitted v2 canonical bytes."""

    v2 = compile_execution_spec({
        **_spec_payload(),
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V2,
        "run_payload_schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2,
        "model_gateway_revision": 4,
        "model_max_input_tokens": 95_000,
        "model_max_output_tokens": 4_096,
    })
    assert v2.to_mapping()["schema_version"] == EXECUTION_SPEC_SCHEMA_VERSION_V2
    assert "model_auto_compact_window" not in v2.to_mapping()
    # A historical 95k input capacity produced the 100k CLI floor.
    assert model_auto_compact_window(v2.to_mapping()) == 100_000
    assert ExecutionSpec.from_canonical_json(
        v2.canonical_json, expected_sha256=v2.spec_sha256
    ) == v2

    v3 = compile_execution_spec({
        **_spec_payload(),
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V3,
        "run_payload_schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2,
        "model_gateway_revision": 4,
        "model_auto_compact_window": 200_000,
        "model_max_output_tokens": 4_096,
    })
    assert v3.to_mapping()["model_auto_compact_window"] == 200_000
    assert "model_max_input_tokens" not in v3.to_mapping()
    assert model_auto_compact_window(v3.to_mapping()) == 200_000
    assert v3.canonical_json != v2.canonical_json


def test_frozen_spec_codec_is_independent_of_live_execution_policy(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def without_execution_policy(name, *args, **kwargs):
        if name == "app.execution" or name.startswith("app.execution."):
            raise AssertionError("the frozen Runs codec must not import Execution policy")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_execution_policy)
    for window in (100_000, 1_000_000):
        spec = compile_execution_spec({
            **_spec_payload(),
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V3,
            "run_payload_schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2,
            "model_gateway_revision": 4,
            "model_auto_compact_window": window,
            "model_max_output_tokens": 4_096,
        })
        assert ExecutionSpec.from_canonical_json(
            spec.canonical_json, expected_sha256=spec.spec_sha256
        ) == spec
        assert spec.to_mapping()["model_auto_compact_window"] == window


def test_v3_rejects_a_window_outside_the_engine_range():
    for window in (99_999, 1_000_001, 0, True):
        with pytest.raises(ExecutionSpecError, match="model_auto_compact_window_invalid"):
            compile_execution_spec({
                **_spec_payload(),
                "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V3,
                "run_payload_schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2,
                "model_gateway_revision": 4,
                "model_auto_compact_window": window,
                "model_max_output_tokens": 4_096,
            })


def test_configured_window_reaches_the_runner_unchanged_end_to_end():
    """The published window must survive spec, projection, and transport verbatim."""

    from app.execution.domain.model_catalog import normalize_model_token_limits
    from app.executors.claude_agent_sdk_runner import _sdk_autocompact_window
    from app.runtime.sandbox.contracts import ModelTokenLimits

    window, output_limit = normalize_model_token_limits(200_000, 36_500)
    assert (window, output_limit) == (200_000, 36_500)

    spec = compile_execution_spec_for_dispatch(
        run_identity={
            "tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
            "session_id": "session-a", "run_id": "run-a", "agent_id": "agent-a",
            "execution_kind": "harness_chat", "skill_id": None,
        },
        queue_payload=SimpleNamespace(
            schema_version=RUN_PAYLOAD_SCHEMA_VERSION_V2, skill_id=None, file_ids=[],
            input={"message": "hello"}, executor_type="claude-agent-worker",
            skill_version=None, release_decision={}, skill_manifests=[],
            model_id="model-a", model_value="claude-test", agent_profile=None,
        ),
        trace_id="trace-a",
        context_snapshot_id="context-a",
        context_snapshot={"context_snapshot_id": "context-a"},
        context_pack={"conversation_context": {"execution_mode": "empty_start"}},
        run_model_snapshot={
            "model_id": "model-a", "model_value": "claude-test",
            "model_gateway_revision": 4,
            "auto_compact_window": window, "max_output_tokens": output_limit,
        },
    )
    assert spec.to_mapping()["model_auto_compact_window"] == 200_000

    run_payload = project_execution_spec_to_run_payload(spec, attempt_id="attempt-a")
    transport = ModelTokenLimits(
        auto_compact_window=run_payload.model_auto_compact_window,
        max_output_tokens=run_payload.model_max_output_tokens,
    )
    assert transport.auto_compact_window == 200_000
    assert _sdk_autocompact_window(transport.auto_compact_window) == 200_000


def test_an_unconfigured_model_window_defaults_to_100000():
    from app.execution.domain.model_catalog import (
        AUTO_COMPACT_WINDOW_DEFAULT_TOKENS,
        normalize_model_token_limits,
    )

    assert AUTO_COMPACT_WINDOW_DEFAULT_TOKENS == 100_000
    assert normalize_model_token_limits(None, 36_500)[0] == 100_000
    # A legal custom window is preserved rather than reset to the default.
    assert normalize_model_token_limits(500_000, 36_500)[0] == 500_000


def test_historical_v2_spec_projects_to_the_window_the_runner_derived():
    """A frozen v2 capacity must decode into the DTO, not raise on its old key."""

    from tests.support.frozen_receipts import frozen_spec

    for capacity, expected in ((95_000, 100_000), (200_000, 200_000), (2_000_000, 1_000_000)):
        spec = frozen_spec(legacy_v2_input_capacity=capacity)
        canonical_before, digest_before = spec.canonical_json, spec.spec_sha256
        assert "model_max_input_tokens" in spec.to_mapping()

        run_payload = project_execution_spec_to_run_payload(spec, attempt_id="attempt-a")

        assert run_payload.model_auto_compact_window == expected
        assert run_payload.model_max_output_tokens == 4096
        # The immutable spec bytes and digest are never rewritten by projection.
        assert (spec.canonical_json, spec.spec_sha256) == (canonical_before, digest_before)
        assert "model_max_input_tokens" in spec.to_mapping()


def test_current_v3_spec_projects_its_own_window_without_a_legacy_key():
    from tests.support.frozen_receipts import frozen_spec

    spec = frozen_spec()
    assert "model_max_input_tokens" not in spec.to_mapping()
    assert project_execution_spec_to_run_payload(
        spec, attempt_id="attempt-a"
    ).model_auto_compact_window == 200_000


def test_persisted_pre_rename_snapshot_restores_through_the_same_window_mapping():
    """A stored snapshot still carrying the retired key must restore, not crash."""

    from app.execution.application.executor_reconciliation import (
        restored_sandbox_run_payload,
    )
    from app.executors.base import RunPayload

    historical = {
        "tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
        "session_id": "session-a", "run_id": "run-a", "attempt_id": "attempt-a",
        "agent_id": "agent-a", "skill_id": None, "file_ids": [], "input": {"message": "hi"},
        "owner_generation": 4, "execution_kind": "harness_chat", "trace_id": "trace-a",
        "skill_version": "", "release_decision": {}, "skill_manifests": [],
        "context_snapshot_id": "context-a", "context_snapshot": {}, "context_pack": {},
        "model_id": "model-a", "model_value": "claude-test", "model_gateway_revision": 3,
        "model_max_input_tokens": 95_000, "model_max_output_tokens": 4096,
        "schema_version": "ai-platform.run-payload.v2",
    }
    restored = restored_sandbox_run_payload(dict(historical), RunPayload, {})
    assert restored.model_auto_compact_window == 100_000

    versioned = {
        "schema_version": "ai-platform.executor-reconciliation-snapshot.v2",
        "execution_payload": dict(historical),
        "metadata": {"agent_profile_expected": False},
    }
    assert restored_sandbox_run_payload(
        versioned, RunPayload, {}
    ).model_auto_compact_window == 100_000

    # A current snapshot keeps its own window and never accepts a conflict silently.
    current = {key: val for key, val in historical.items() if key != "model_max_input_tokens"}
    current["model_auto_compact_window"] = 200_000
    assert restored_sandbox_run_payload(dict(current), RunPayload, {}).model_auto_compact_window == 200_000
    with pytest.raises(TypeError):
        restored_sandbox_run_payload({**current, "model_bogus_tokens": 1}, RunPayload, {})


def test_window_policy_reaches_other_contexts_only_through_the_execution_api():
    """AGENTS: cross-context imports go through the public api, not domain modules."""

    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for relative in (
        "app/runs/domain/execution_spec.py",
        "app/runs/application/attempt_lifecycle.py",
        "app/executors/claude_agent_sdk_runner.py",
        "app/executors/base.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app.execution.domain."):
                    offenders.append(f"{relative}: {node.module}")
    assert offenders == []

    from app.execution import api as execution_api

    for name in (
        "AUTO_COMPACT_WINDOW_MIN_TOKENS",
        "AUTO_COMPACT_WINDOW_MAX_TOKENS",
        "AUTO_COMPACT_WINDOW_DEFAULT_TOKENS",
        "legacy_input_capacity_to_auto_compact_window",
    ):
        assert name in execution_api.__all__
        assert getattr(execution_api, name) is not None
