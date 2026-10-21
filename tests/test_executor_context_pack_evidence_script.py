import app.context.infrastructure.snapshot_postgres as _owner_context_infrastructure_snapshot_postgres
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_generator():
    return _load(
        "scripts/generate_executor_context_pack_evidence.py",
        "generate_executor_context_pack_evidence",
    )


def load_verifier():
    return _load("scripts/verify_executor_context_pack.py", "verify_executor_context_pack")


def test_source_probe_is_redacted_and_never_closes_runtime_acceptance(tmp_path):
    generator = load_generator()
    verifier = load_verifier()
    evidence_path = tmp_path / "executor-context-pack-evidence.json"

    evidence = generator.build_evidence(run_id="run-source")
    generator.write_evidence(evidence, evidence_path)

    assert evidence["schema_version"] == "ai-platform.executor-context-pack-probe.v3"
    assert evidence["evidence_strength"] == "source_probe_on_target_runtime"
    assert evidence["does_not_close_runtime_acceptance"] is True
    assert evidence["runtime_acceptance_requires_observed_worker_dispatch"] is True
    assert evidence["observed_worker_dispatch"] is False
    assert evidence["prompt_rendering_mode"] == "manifest_materials"
    assert evidence["prompt_checks"]["manifest_refs_rendered"] is True
    assert evidence["prompt_checks"]["safe_retrieval_tools_rendered"] is True
    assert evidence["prompt_checks"]["manifest_summary_omitted"] is True
    assert evidence["prompt_checks"]["manifest_audit_metadata_omitted"] is True
    assert evidence["prompt_checks"]["generated_at_metadata_absent"] is True
    assert "app.executors.claude.prompts.build_skill_prompt" in evidence["source_functions"]
    assert verifier.check_executor_context_pack_evidence(evidence_path, run_id="run-source").passed is True
    assert verifier.check_no_secret_leakage(evidence_path).passed is True


def test_material_count_contract_requires_all_four_bounded_integer_fields():
    generator = load_generator()
    verifier = load_verifier()
    valid_counts = {
        "message_count": 1,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }

    assert generator._scope_checks_from_context_pack(
        {"referenced_materials": valid_counts}
    )["referenced_material_counts_bounded"] is True
    for invalid_counts in (
        {key: value for key, value in valid_counts.items() if key != "memory_record_count"},
        {**valid_counts, "file_count": True},
        {**valid_counts, "artifact_count": 10_001},
    ):
        assert generator._scope_checks_from_context_pack(
            {"referenced_materials": invalid_counts}
        )["referenced_material_counts_bounded"] is False
        error = verifier._public_context_summary_error(
            {
                "evidence_strength": (
                    "observed_worker_dispatch_with_scoped_context_reconstruction"
                ),
                "public_context_summary": {
                    "referenced_material_counts": invalid_counts,
                    "input_keys": ["message"],
                },
            }
        )
        assert error in {
            "referenced material counts evidence incomplete",
            "referenced material counts evidence invalid",
        }


def test_verifier_rejects_any_claim_of_observed_worker_dispatch(tmp_path):
    generator = load_generator()
    verifier = load_verifier()
    evidence_path = tmp_path / "executor-context-pack-evidence.json"
    evidence = generator.build_evidence(run_id="run-source")
    evidence["does_not_close_runtime_acceptance"] = False
    evidence["observed_worker_dispatch"] = True
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = verifier.check_executor_context_pack_evidence(evidence_path, run_id="run-source")

    assert result.passed is False
    assert "boundary" in result.message or "must not claim" in result.message


def test_observed_dispatch_requirement_cannot_be_satisfied_by_source_probe(tmp_path):
    generator = load_generator()
    verifier = load_verifier()
    evidence_path = tmp_path / "executor-context-pack-evidence.json"
    generator.write_evidence(generator.build_evidence(run_id="run-source"), evidence_path)

    result = verifier.check_executor_context_pack_evidence(
        evidence_path,
        run_id="run-source",
        require_observed_worker_dispatch=True,
    )

    assert result.passed is False
    assert "observed worker-dispatch evidence required" in result.message


def test_observed_worker_run_accepts_zero_artifacts_when_message_context_is_present(monkeypatch, tmp_path):
    generator = load_generator()
    verifier = load_verifier()

    class RunCursor:
        async def fetchone(self):
            return {
                "id": "run-live",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "succeeded",
                "input_json": {"context_snapshot_id": "ctx-live"},
            }

    class EventCursor:
        async def fetchall(self):
            return [
                {"event_type": "worker_started", "stage": "worker", "sequence": 3},
                {"event_type": "run_succeeded", "stage": "worker", "sequence": 9},
            ]

    class Conn:
        async def execute(self, sql, params):
            if "from runs" in sql:
                assert params == ("run-live",)
                return RunCursor()
            assert "from run_events" in sql
            assert params == ("tenant-a", "run-live")
            return EventCursor()

    @asynccontextmanager
    async def fake_transaction():
        yield Conn()

    calls = []

    async def fake_snapshot_loader(conn, **kwargs):
        calls.append(kwargs)
        return {
            "id": "ctx-live",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-live",
            "trace_id": "trace-live",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "source": "runs_api",
                "referenced_materials": {
                    "message_count": 1,
                    "file_count": 0,
                    "artifact_count": 0,
                    "memory_record_count": 0,
                },
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": False,
                },
                "execution_tier": "sdk_only_writing",
                "context_pack_version": "v3",
                "context_pack_generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "created_at": None,
        }

    monkeypatch.setattr(generator, "transaction", fake_transaction)
    monkeypatch.setattr(_owner_context_infrastructure_snapshot_postgres, 'get_context_snapshot_for_worker', fake_snapshot_loader)

    evidence = generator.asyncio.run(generator.build_live_run_evidence(run_id="run-live"))
    evidence_path = tmp_path / "executor-context-pack-runtime.json"
    generator.write_evidence(evidence, evidence_path)

    assert calls == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-live",
            "context_snapshot_id": "ctx-live",
        }
    ]
    assert evidence["schema_version"] == "ai-platform.executor-context-pack-runtime-acceptance.v3"
    assert evidence["evidence_strength"] == "observed_worker_dispatch_with_scoped_context_reconstruction"
    assert evidence["reconstruction_source"] == "persisted_worker_event_ledger"
    assert evidence["public_context_summary"]["referenced_material_counts"]["artifact_count"] == 0
    assert evidence["does_not_close_runtime_acceptance"] is False
    assert evidence["runtime_run_payload_verified"] is False
    assert evidence["observed_worker_dispatch"] is True
    assert evidence["prompt_rendering_mode"] == "empty_materials"
    assert evidence["prompt_checks"]["empty_material_section_omitted"] is True
    assert evidence["prompt_checks"]["generated_at_metadata_absent"] is True
    assert evidence["runtime_evidence"]["prompt_rendering_contract_verified"] is True
    assert verifier.check_executor_context_pack_evidence(
        evidence_path,
        run_id="run-live",
        require_observed_worker_dispatch=True,
    ).passed is True


def test_live_run_rejects_missing_worker_dispatch_events(monkeypatch):
    generator = load_generator()

    class RunCursor:
        async def fetchone(self):
            return {
                "id": "run-live",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "succeeded",
                "input_json": {"context_snapshot_id": "ctx-live"},
            }

    class EventCursor:
        async def fetchall(self):
            return []

    class Conn:
        async def execute(self, sql, params):
            return RunCursor() if "from runs" in sql else EventCursor()

    @asynccontextmanager
    async def fake_transaction():
        yield Conn()

    async def fake_snapshot_loader(conn, **kwargs):
        return {"id": "ctx-live", "payload_json": {}}

    monkeypatch.setattr(generator, "transaction", fake_transaction)
    monkeypatch.setattr(_owner_context_infrastructure_snapshot_postgres, 'get_context_snapshot_for_worker', fake_snapshot_loader)

    try:
        generator.asyncio.run(generator.build_live_run_evidence(run_id="run-live"))
    except RuntimeError as exc:
        assert str(exc) == "durable worker dispatch evidence missing"
    else:
        raise AssertionError("missing worker events must fail closed")


def test_live_run_requires_every_worker_start_before_terminal_success():
    generator = load_generator()

    class EventCursor:
        async def fetchall(self):
            return [
                {"event_type": "worker_started", "stage": "worker", "sequence": 3},
                {"event_type": "run_succeeded", "stage": "worker", "sequence": 9},
                {"event_type": "worker_started", "stage": "worker", "sequence": 12},
            ]

    class Conn:
        async def execute(self, sql, params):
            assert "from run_events" in sql
            assert params == ("tenant-a", "run-live")
            return EventCursor()

    try:
        generator.asyncio.run(
            generator._load_worker_dispatch_proof(
                Conn(),
                run={"id": "run-live", "tenant_id": "tenant-a", "status": "succeeded"},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "durable worker dispatch event order invalid"
    else:
        raise AssertionError("a start after terminal success must fail closed")


def test_verifier_rejects_tampered_worker_dispatch_evidence(monkeypatch, tmp_path):
    generator = load_generator()
    verifier = load_verifier()

    evidence = generator.build_evidence(run_id="run-live")
    evidence.update(
        {
            "schema_version": "ai-platform.executor-context-pack-runtime-acceptance.v3",
            "evidence_strength": "observed_worker_dispatch_with_scoped_context_reconstruction",
            "reconstruction_source": "persisted_worker_event_ledger",
            "does_not_close_runtime_acceptance": False,
            "runtime_run_payload_verified": False,
            "observed_worker_dispatch": True,
            "prompt_rendering_mode": "manifest_materials",
            "live_run_checks": {field: True for field in verifier.REQUIRED_LIVE_RUN_CHECKS},
            "worker_dispatch_checks": {
                field: field != "worker_events_ordered"
                for field in verifier.REQUIRED_WORKER_DISPATCH_CHECKS
            },
            "runtime_evidence": {field: True for field in verifier.REQUIRED_RUNTIME_EVIDENCE},
            "public_context_summary": {
                "referenced_material_counts": {
                    "message_count": 1,
                    "file_count": 0,
                    "artifact_count": 0,
                    "memory_record_count": 0,
                },
                "input_keys": ["message"],
            },
        }
    )
    evidence_path = tmp_path / "tampered-runtime-evidence.json"
    generator.write_evidence(evidence, evidence_path)

    result = verifier.check_executor_context_pack_evidence(
        evidence_path,
        run_id="run-live",
        require_observed_worker_dispatch=True,
    )

    assert result.passed is False
    assert "worker_events_ordered" in result.message


def test_cli_help_states_evidence_ceiling():
    generator_help = load_generator().build_parser().format_help()
    verifier_help = load_verifier().build_parser().format_help()

    assert "observed worker-run" in generator_help
    assert "acceptance evidence" in generator_help
    assert "succeeded run with ordered worker events" in generator_help
    assert "ordered durable worker" in verifier_help
    assert "dispatch events" in verifier_help


def test_verifier_rejects_missing_false_and_invalid_rendering_contract(tmp_path):
    generator, verifier = load_generator(), load_verifier()
    path = tmp_path / "evidence.json"
    for mode, fields in verifier.PROMPT_CHECKS_BY_RENDERING_MODE.items():
        for field in [*verifier.REQUIRED_PROMPT_CHECKS, *fields]:
            for value in (None, False):
                evidence = generator.build_evidence(run_id="run-source")
                evidence["prompt_rendering_mode"] = mode
                evidence["prompt_checks"].update({name: True for name in fields})
                if value is None:
                    evidence["prompt_checks"].pop(field, None)
                else:
                    evidence["prompt_checks"][field] = value
                generator.write_evidence(evidence, path)
                assert not verifier.check_executor_context_pack_evidence(path, run_id="run-source").passed
    for mode in (None, "unknown", [], {}):
        evidence = generator.build_evidence(run_id="run-source")
        evidence["prompt_rendering_mode"] = mode
        generator.write_evidence(evidence, path)
        assert not verifier.check_executor_context_pack_evidence(path, run_id="run-source").passed
    evidence = generator.build_evidence(run_id="run-source")
    evidence["schema_version"] = "ai-platform.executor-context-pack-probe.v2"
    generator.write_evidence(evidence, path)
    assert not verifier.check_executor_context_pack_evidence(path, run_id="run-source").passed


def test_prompt_contract_detects_missing_refs_tools_and_unexpected_empty_section():
    generator = load_generator()
    pack = generator.executor_context_pack_from_snapshot(generator._sample_context_snapshot("run-source"))
    checks = generator._prompt_checks("", context_pack=pack)
    assert checks["manifest_refs_rendered"] is False
    assert checks["safe_retrieval_tools_rendered"] is False
    pack = {"referenced_materials": {"file_count": 0, "artifact_count": 0, "memory_record_count": 0}}
    assert generator._prompt_checks("Office context pack:", context_pack=pack)["empty_material_section_omitted"] is False
    for materials in ({}, {"file_count": 0}, {"message_count": 1}):
        pack = {"prompt_summary": "Safe summary", "referenced_materials": materials}
        assert generator._prompt_rendering_mode(pack) == "legacy_bounded_summary"
        checks = generator._prompt_checks("Office context pack:\nSafe summary", context_pack=pack)
        assert generator._prompt_rendering_contract_verified("legacy_bounded_summary", checks)


def test_generator_cli_uses_selected_mode_and_reports_failed_checks(tmp_path, capsys, monkeypatch):
    generator = load_generator()
    args = ["--run-id", "run-source", "--evidence-file", str(tmp_path / "evidence.json")]
    assert generator.main([*args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prompt_checks_passed"] is True
    evidence = generator.build_evidence(run_id="run-source")
    evidence["prompt_checks"]["manifest_refs_rendered"] = False
    monkeypatch.setattr(generator, "build_evidence", lambda **kwargs: evidence)
    assert generator.main(args) == 1
    assert capsys.readouterr().out.startswith("FAILED:")
