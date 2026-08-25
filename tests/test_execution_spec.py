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
from app.runs.api import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    ExecutionSpec,
    ExecutionSpecError,
    compile_execution_spec_for_dispatch,
    compile_execution_spec,
)


ROOT = Path(__file__).resolve().parents[1]
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
    assert "It does not yet persist the canonical JSON or digest" in decision
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
    )

    assert "attempt_id" not in spec.to_mapping()
    run_payload = project_execution_spec_to_run_payload(spec, attempt_id="attempt-a")
    assert run_payload.attempt_id == "attempt-a"
    assert run_payload.schema_version == RUN_PAYLOAD_SCHEMA_VERSION
    assert run_payload.file_ids == ["file-a"]
    assert run_payload.input == payload["input"]


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
    )

    assert spec.to_mapping()["skill_id"] is None
