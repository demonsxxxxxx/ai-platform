import importlib.util
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_verifier():
    path = Path("scripts/verify_executor_context_pack_211.py")
    spec = importlib.util.spec_from_file_location("verify_executor_context_pack_211", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_generator():
    path = Path("scripts/generate_executor_context_pack_evidence_211.py")
    spec = importlib.util.spec_from_file_location("generate_executor_context_pack_evidence_211", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_executor_context_pack_verifier_accepts_safe_runtime_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.executor-context-pack-211.v1",
                "run_id": "run-a",
                "runtime_mode": "worker",
                "evidence_strength": "source_probe_on_target_runtime",
                "does_not_close_211_acceptance": True,
                "runtime_acceptance_requires_real_run_payload": True,
                "runtime_run_payload_verified": False,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_schema_version": "ai-platform.executor-context-pack.v1",
                "source_functions": [
                    "app.repositories.get_context_snapshot_for_worker",
                    "app.context_builder.executor_context_pack_from_snapshot",
                    "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
                    "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
                    "app.worker._context_snapshot_ref_from_row",
                ],
                "prompt_checks": {
                    "bounded_summary_present": True,
                    "context_pack_version_present": True,
                    "context_pack_generated_at_present": True,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                },
                "scope_checks": {
                    "tenant_id_scoped": True,
                    "workspace_id_scoped": True,
                    "user_id_scoped": True,
                    "session_id_scoped": True,
                    "source_run_artifact_count_positive": True,
                    "source_run_artifact_scope_verified": True,
                },
                "non_expansion_invariants": {
                    "ordinary_user_multi_agent_allowed": False,
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "lightweight_office_tasks_start_sandbox_by_default": False,
                    "long_term_cross_session_memory_enabled": False,
                    "public_projection_only_for_ordinary_users": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_executor_context_pack_evidence(evidence, run_id="run-a")

    assert result.passed is True
    assert verifier.check_no_secret_leakage(evidence).passed is True

    strict = verifier.check_executor_context_pack_evidence(
        evidence,
        run_id="run-a",
        require_live_run_payload=True,
    )
    assert strict.passed is False
    assert "live worker run payload" in strict.message


def test_executor_context_pack_verifier_accepts_live_worker_run_payload_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-live-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.executor-context-pack-211.v1",
                "run_id": "run-a",
                "runtime_mode": "worker",
                "evidence_strength": "live_worker_run_payload",
                "does_not_close_211_acceptance": False,
                "runtime_acceptance_requires_real_run_payload": False,
                "runtime_run_payload_verified": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_schema_version": "ai-platform.executor-context-pack.v1",
                "source_functions": [
                    "app.repositories.get_context_snapshot_for_worker",
                    "app.context_builder.executor_context_pack_from_snapshot",
                    "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
                    "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
                    "app.worker._context_snapshot_ref_from_row",
                ],
                "prompt_checks": {
                    "bounded_summary_present": True,
                    "context_pack_version_present": True,
                    "context_pack_generated_at_present": True,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                },
                "scope_checks": {
                    "tenant_id_scoped": True,
                    "workspace_id_scoped": True,
                    "user_id_scoped": True,
                    "session_id_scoped": True,
                    "source_run_artifact_count_positive": True,
                    "source_run_artifact_scope_verified": True,
                },
                "live_run_checks": {
                    "run_row_loaded": True,
                    "context_snapshot_id_present": True,
                    "scoped_context_snapshot_loaded": True,
                    "worker_context_ref_rebuilt_from_db_snapshot": True,
                    "context_pack_schema_present": True,
                },
                "runtime_evidence": {
                    "live_worker_run_payload": True,
                    "run_row_loaded": True,
                    "context_snapshot_id_present": True,
                    "scoped_context_snapshot_loaded": True,
                    "worker_context_ref_rebuilt_from_db_snapshot": True,
                    "prompt_includes_bounded_summary": True,
                    "prompt_includes_context_pack_version": True,
                    "prompt_includes_context_pack_generated_at": True,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                    "source_run_artifact_scope_tenant_workspace_user_session": True,
                    "source_run_artifact_count_positive": True,
                    "fresh_generated_at": True,
                    "source_functions_bound_to_current_runtime": True,
                },
                "public_context_summary": {
                    "execution_tier": "document_worker",
                    "context_pack_version": "v1",
                    "context_pack_generated_at_present": True,
                    "referenced_material_counts": {
                        "message_count": 1,
                        "file_count": 1,
                        "artifact_count": 1,
                        "memory_record_count": 0,
                    },
                    "input_keys": ["attachments", "message"],
                },
                "non_expansion_invariants": {
                    "ordinary_user_multi_agent_allowed": False,
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "lightweight_office_tasks_start_sandbox_by_default": False,
                    "long_term_cross_session_memory_enabled": False,
                    "public_projection_only_for_ordinary_users": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_executor_context_pack_evidence(
        evidence,
        run_id="run-a",
        require_live_run_payload=True,
    )

    assert result.passed is True
    assert "live worker-run evidence" in result.message

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["public_context_summary"]["input_keys"] = ["attachments", "copied_from_run_id", "message"]
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    leaked = verifier.check_executor_context_pack_evidence(
        evidence,
        run_id="run-a",
        require_live_run_payload=True,
    )

    assert leaked.passed is False
    assert "source run identifiers" in leaked.message


def test_executor_context_pack_verifier_rejects_live_evidence_without_source_artifact_count(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-live-evidence.json"
    payload = {
        "schema_version": "ai-platform.executor-context-pack-211.v1",
        "run_id": "run-a",
        "runtime_mode": "worker",
        "evidence_strength": "live_worker_run_payload",
        "does_not_close_211_acceptance": False,
        "runtime_acceptance_requires_real_run_payload": False,
        "runtime_run_payload_verified": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_schema_version": "ai-platform.executor-context-pack.v1",
        "source_functions": [
            "app.repositories.get_context_snapshot_for_worker",
            "app.context_builder.executor_context_pack_from_snapshot",
            "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
            "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
            "app.worker._context_snapshot_ref_from_row",
        ],
        "prompt_checks": {
            "bounded_summary_present": True,
            "context_pack_version_present": True,
            "context_pack_generated_at_present": True,
            "raw_storage_identifiers_absent": True,
            "sandbox_runtime_paths_absent": True,
            "executor_private_content_absent": True,
            "long_term_memory_read_false": True,
        },
        "scope_checks": {
            "tenant_id_scoped": True,
            "workspace_id_scoped": True,
            "user_id_scoped": True,
            "session_id_scoped": True,
            "source_run_artifact_count_positive": True,
            "source_run_artifact_scope_verified": True,
        },
        "live_run_checks": {
            "run_row_loaded": True,
            "context_snapshot_id_present": True,
            "scoped_context_snapshot_loaded": True,
            "worker_context_ref_rebuilt_from_db_snapshot": True,
            "context_pack_schema_present": True,
        },
        "runtime_evidence": {
            "live_worker_run_payload": True,
            "run_row_loaded": True,
            "context_snapshot_id_present": True,
            "scoped_context_snapshot_loaded": True,
            "worker_context_ref_rebuilt_from_db_snapshot": True,
            "prompt_includes_bounded_summary": True,
            "prompt_includes_context_pack_version": True,
            "prompt_includes_context_pack_generated_at": True,
            "raw_storage_identifiers_absent": True,
            "sandbox_runtime_paths_absent": True,
            "executor_private_content_absent": True,
            "long_term_memory_read_false": True,
            "source_run_artifact_scope_tenant_workspace_user_session": True,
            "source_run_artifact_count_positive": True,
            "fresh_generated_at": True,
            "source_functions_bound_to_current_runtime": True,
        },
        "public_context_summary": {
            "execution_tier": "document_worker",
            "context_pack_version": "v1",
            "context_pack_generated_at_present": True,
            "referenced_material_counts": {
                "message_count": 1,
                "file_count": 1,
                "artifact_count": 0,
                "memory_record_count": 0,
            },
            "input_keys": ["attachments", "message"],
        },
        "non_expansion_invariants": {
            "ordinary_user_multi_agent_allowed": False,
            "ordinary_user_high_risk_sandbox_allowed": False,
            "lightweight_office_tasks_start_sandbox_by_default": False,
            "long_term_cross_session_memory_enabled": False,
            "public_projection_only_for_ordinary_users": True,
        },
    }
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = verifier.check_executor_context_pack_evidence(
        evidence,
        run_id="run-a",
        require_live_run_payload=True,
    )

    assert result.passed is False
    assert "positive artifact_count" in result.message


def test_executor_context_pack_verifier_rejects_live_evidence_without_runtime_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-live-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.executor-context-pack-211.v1",
                "run_id": "run-a",
                "runtime_mode": "worker",
                "evidence_strength": "live_worker_run_payload",
                "does_not_close_211_acceptance": False,
                "runtime_acceptance_requires_real_run_payload": False,
                "runtime_run_payload_verified": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_schema_version": "ai-platform.executor-context-pack.v1",
                "source_functions": [
                    "app.repositories.get_context_snapshot_for_worker",
                    "app.context_builder.executor_context_pack_from_snapshot",
                    "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
                    "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
                    "app.worker._context_snapshot_ref_from_row",
                ],
                "prompt_checks": {
                    "bounded_summary_present": True,
                    "context_pack_version_present": True,
                    "context_pack_generated_at_present": True,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                },
                "scope_checks": {
                    "tenant_id_scoped": True,
                    "workspace_id_scoped": True,
                    "user_id_scoped": True,
                    "session_id_scoped": True,
                    "source_run_artifact_count_positive": True,
                    "source_run_artifact_scope_verified": True,
                },
                "live_run_checks": {
                    "run_row_loaded": True,
                    "context_snapshot_id_present": True,
                    "scoped_context_snapshot_loaded": True,
                    "worker_context_ref_rebuilt_from_db_snapshot": True,
                    "context_pack_schema_present": True,
                },
                "non_expansion_invariants": {
                    "ordinary_user_multi_agent_allowed": False,
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "lightweight_office_tasks_start_sandbox_by_default": False,
                    "long_term_cross_session_memory_enabled": False,
                    "public_projection_only_for_ordinary_users": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_executor_context_pack_evidence(
        evidence,
        run_id="run-a",
        require_live_run_payload=True,
    )

    assert result.passed is False
    assert "runtime_evidence" in result.message


def test_executor_context_pack_verifier_requires_scoped_repository_loader_source(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-live-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.executor-context-pack-211.v1",
                "run_id": "run-a",
                "runtime_mode": "worker",
                "evidence_strength": "live_worker_run_payload",
                "does_not_close_211_acceptance": False,
                "runtime_acceptance_requires_real_run_payload": False,
                "runtime_run_payload_verified": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_schema_version": "ai-platform.executor-context-pack.v1",
                "source_functions": [
                    "app.context_builder.executor_context_pack_from_snapshot",
                    "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
                    "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
                    "app.worker._context_snapshot_ref_from_row",
                ],
                "prompt_checks": {
                    "bounded_summary_present": True,
                    "context_pack_version_present": True,
                    "context_pack_generated_at_present": True,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                },
                "scope_checks": {
                    "tenant_id_scoped": True,
                    "workspace_id_scoped": True,
                    "user_id_scoped": True,
                    "session_id_scoped": True,
                    "source_run_artifact_count_positive": True,
                    "source_run_artifact_scope_verified": True,
                },
                "live_run_checks": {
                    "run_row_loaded": True,
                    "context_snapshot_id_present": True,
                    "scoped_context_snapshot_loaded": True,
                    "worker_context_ref_rebuilt_from_db_snapshot": True,
                    "context_pack_schema_present": True,
                },
                "runtime_evidence": {
                    "live_worker_run_payload": True,
                    "run_row_loaded": True,
                    "context_snapshot_id_present": True,
                    "scoped_context_snapshot_loaded": True,
                    "worker_context_ref_rebuilt_from_db_snapshot": True,
                    "prompt_includes_bounded_summary": True,
                    "prompt_includes_context_pack_version": True,
                    "prompt_includes_context_pack_generated_at": True,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                    "source_run_artifact_scope_tenant_workspace_user_session": True,
                    "source_run_artifact_count_positive": True,
                    "fresh_generated_at": True,
                    "source_functions_bound_to_current_runtime": True,
                },
                "public_context_summary": {
                    "execution_tier": "document_worker",
                    "context_pack_version": "v1",
                    "context_pack_generated_at_present": True,
                    "referenced_material_counts": {
                        "message_count": 1,
                        "file_count": 1,
                        "artifact_count": 1,
                        "memory_record_count": 0,
                    },
                    "input_keys": ["attachments", "message"],
                },
                "non_expansion_invariants": {
                    "ordinary_user_multi_agent_allowed": False,
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "lightweight_office_tasks_start_sandbox_by_default": False,
                    "long_term_cross_session_memory_enabled": False,
                    "public_projection_only_for_ordinary_users": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_executor_context_pack_evidence(
        evidence,
        run_id="run-a",
        require_live_run_payload=True,
    )

    assert result.passed is False
    assert "app.repositories.get_context_snapshot_for_worker" in result.message


def test_executor_context_pack_verifier_rejects_missing_generated_at_and_expansion(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.executor-context-pack-211.v1",
                "run_id": "run-a",
                "runtime_mode": "worker",
                "evidence_strength": "source_probe_on_target_runtime",
                "does_not_close_211_acceptance": True,
                "runtime_acceptance_requires_real_run_payload": True,
                "runtime_run_payload_verified": False,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_schema_version": "ai-platform.executor-context-pack.v1",
                "source_functions": [
                    "app.repositories.get_context_snapshot_for_worker",
                    "app.context_builder.executor_context_pack_from_snapshot",
                    "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
                    "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
                    "app.worker._context_snapshot_ref_from_row",
                ],
                "prompt_checks": {
                    "bounded_summary_present": True,
                    "context_pack_version_present": True,
                    "context_pack_generated_at_present": False,
                    "raw_storage_identifiers_absent": True,
                    "sandbox_runtime_paths_absent": True,
                    "executor_private_content_absent": True,
                    "long_term_memory_read_false": True,
                },
                "scope_checks": {
                    "tenant_id_scoped": True,
                    "workspace_id_scoped": True,
                    "user_id_scoped": True,
                    "session_id_scoped": True,
                    "source_run_artifact_scope_verified": True,
                },
                "non_expansion_invariants": {
                    "ordinary_user_multi_agent_allowed": True,
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "lightweight_office_tasks_start_sandbox_by_default": False,
                    "long_term_cross_session_memory_enabled": False,
                    "public_projection_only_for_ordinary_users": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_executor_context_pack_evidence(evidence, run_id="run-a")

    assert result.passed is False
    assert "context_pack_generated_at_present" in result.message


def test_executor_context_pack_verifier_rejects_stale_or_unbound_source_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "executor-context-pack-evidence.json"
    base = {
        "schema_version": "ai-platform.executor-context-pack-211.v1",
        "run_id": "run-a",
        "runtime_mode": "worker",
        "evidence_strength": "source_probe_on_target_runtime",
        "does_not_close_211_acceptance": True,
        "runtime_acceptance_requires_real_run_payload": True,
        "runtime_run_payload_verified": False,
        "source_schema_version": "ai-platform.executor-context-pack.v1",
        "source_functions": [
            "app.repositories.get_context_snapshot_for_worker",
            "app.context_builder.executor_context_pack_from_snapshot",
            "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
            "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
            "app.worker._context_snapshot_ref_from_row",
        ],
        "prompt_checks": {
            "bounded_summary_present": True,
            "context_pack_version_present": True,
            "context_pack_generated_at_present": True,
            "raw_storage_identifiers_absent": True,
            "sandbox_runtime_paths_absent": True,
            "executor_private_content_absent": True,
            "long_term_memory_read_false": True,
        },
        "scope_checks": {
            "tenant_id_scoped": True,
            "workspace_id_scoped": True,
            "user_id_scoped": True,
            "session_id_scoped": True,
            "source_run_artifact_count_positive": True,
            "source_run_artifact_scope_verified": True,
        },
        "non_expansion_invariants": {
            "ordinary_user_multi_agent_allowed": False,
            "ordinary_user_high_risk_sandbox_allowed": False,
            "lightweight_office_tasks_start_sandbox_by_default": False,
            "long_term_cross_session_memory_enabled": False,
            "public_projection_only_for_ordinary_users": True,
        },
    }

    evidence.write_text(
        json.dumps({**base, "generated_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}),
        encoding="utf-8",
    )
    stale = verifier.check_executor_context_pack_evidence(evidence, run_id="run-a")
    assert stale.passed is False
    assert "stale" in stale.message

    missing_source = dict(base)
    missing_source["source_functions"] = [
        "app.context_builder.executor_context_pack_from_snapshot",
    ]
    evidence.write_text(
        json.dumps({**missing_source, "generated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    unbound = verifier.check_executor_context_pack_evidence(evidence, run_id="run-a")
    assert unbound.passed is False
    assert "source_functions missing" in unbound.message


def test_executor_context_pack_generator_writes_secret_safe_local_evidence(tmp_path):
    generator = load_generator()
    evidence = tmp_path / "executor-context-pack-evidence.json"

    exit_code = generator.main(
        [
            "--run-id",
            "run-a",
            "--evidence-file",
            str(evidence),
            "--json",
        ]
    )

    assert exit_code == 0
    raw = evidence.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["schema_version"] == "ai-platform.executor-context-pack-211.v1"
    assert payload["run_id"] == "run-a"
    assert payload["evidence_strength"] == "source_probe_on_target_runtime"
    assert payload["does_not_close_211_acceptance"] is True
    assert payload["runtime_acceptance_requires_real_run_payload"] is True
    assert payload["runtime_run_payload_verified"] is False
    assert payload["source_schema_version"] == "ai-platform.executor-context-pack.v1"
    assert payload["prompt_checks"]["bounded_summary_present"] is True
    assert payload["prompt_checks"]["context_pack_generated_at_present"] is True
    assert payload["scope_checks"]["source_run_artifact_count_positive"] is True
    assert payload["scope_checks"]["source_run_artifact_scope_verified"] is True
    assert payload["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert "s3://private" not in raw
    assert "/tmp/private" not in raw


def test_executor_context_pack_generator_builds_live_run_evidence_from_scoped_db_snapshot(monkeypatch):
    generator = load_generator()

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Conn:
        async def execute(self, sql, params):
            if "from runs" in sql:
                return Cursor(
                    {
                        "id": "run-live",
                        "tenant_id": "tenant-a",
                        "workspace_id": "workspace-a",
                        "user_id": "user-a",
                        "session_id": "session-a",
                        "agent_id": "qa-word-review",
                        "skill_id": "qa-file-reviewer",
                        "input_json": {"context_snapshot_id": "ctx-live"},
                    }
                )
            if "from run_context_snapshots" in sql:
                assert params == (
                    "tenant-a",
                    "workspace-a",
                    "user-a",
                    "session-a",
                    "run-live",
                    "ctx-live",
                )
                return Cursor(
                    {
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
                        "included_file_ids": ["file-a"],
                        "included_artifact_ids": ["art-a"],
                        "included_memory_record_ids": ["mem-a"],
                        "redaction_summary_json": {},
                        "payload_json": {
                            "schema_version": "ai-platform.context-snapshot.v1",
                            "source": "runs_api",
                            "referenced_materials": {
                                "message_count": 1,
                                "file_count": 1,
                                "artifact_count": 1,
                                "memory_record_count": 1,
                            },
                            "used_context_summary": {
                                "source": "runs_api",
                                "input_keys": ["attachments", "message", "raw_storage_key"],
                                "memory_policy_source": "stored",
                                "long_term_memory_read": True,
                            },
                            "execution_tier": "document_worker",
                            "context_pack_version": "v3",
                            "context_pack_generated_at": "2026-06-12T01:23:45Z",
                            "raw_storage_key": "s3://private/object",
                            "sandbox_workdir": "/tmp/private",
                            "executor_private_payload": {"token": "secret"},
                        },
                    }
                )
            raise AssertionError(sql)

    @asynccontextmanager
    async def fake_transaction():
        yield Conn()

    monkeypatch.setattr(generator, "transaction", fake_transaction)

    evidence = generator.asyncio.run(generator.build_live_run_evidence(run_id="run-live"))
    raw = json.dumps(evidence, ensure_ascii=False)

    assert evidence["run_id"] == "run-live"
    assert evidence["evidence_strength"] == "live_worker_run_payload"
    assert evidence["does_not_close_211_acceptance"] is False
    assert evidence["runtime_acceptance_requires_real_run_payload"] is False
    assert evidence["runtime_run_payload_verified"] is True
    assert all(evidence["live_run_checks"].values())
    assert all(evidence["runtime_evidence"].values())
    assert evidence["runtime_evidence"]["live_worker_run_payload"] is True
    assert evidence["runtime_evidence"]["prompt_includes_bounded_summary"] is True
    assert evidence["runtime_evidence"]["source_run_artifact_scope_tenant_workspace_user_session"] is True
    assert evidence["runtime_evidence"]["source_run_artifact_count_positive"] is True
    assert evidence["runtime_evidence"]["fresh_generated_at"] is True
    assert evidence["runtime_evidence"]["source_functions_bound_to_current_runtime"] is True
    assert evidence["prompt_checks"]["bounded_summary_present"] is True
    assert evidence["prompt_checks"]["long_term_memory_read_false"] is True
    assert "raw_storage_key" not in raw
    assert "s3://private" not in raw
    assert "/tmp/private" not in raw


def test_executor_context_pack_generator_reuses_repository_scoped_snapshot_loader(monkeypatch):
    generator = load_generator()
    calls = []

    class Cursor:
        async def fetchone(self):
            return {
                "id": "run-live",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "input_json": {"input": {"context_snapshot_id": "ctx-live"}},
            }

    class Conn:
        async def execute(self, sql, params):
            assert "from runs" in sql
            assert "run_context_snapshots" not in sql
            return Cursor()

    @asynccontextmanager
    async def fake_transaction():
        yield Conn()

    async def fake_get_context_snapshot_for_worker(conn, **kwargs):
        calls.append(kwargs)
        return {
            "id": kwargs["context_snapshot_id"],
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-live",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "source": "runs_api",
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message", "raw_storage_key"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": True,
                },
                "execution_tier": "document_worker",
                "context_pack_version": "v3",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
                "raw_storage_key": "s3://private/object",
            },
            "created_at": None,
        }

    monkeypatch.setattr(generator, "transaction", fake_transaction)
    monkeypatch.setattr(
        generator.repositories,
        "get_context_snapshot_for_worker",
        fake_get_context_snapshot_for_worker,
    )

    evidence = generator.asyncio.run(generator.build_live_run_evidence(run_id="run-live"))
    raw = json.dumps(evidence, ensure_ascii=False)

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
    assert evidence["live_run_checks"]["scoped_context_snapshot_loaded"] is True
    assert evidence["runtime_evidence"]["live_worker_run_payload"] is True
    assert evidence["runtime_evidence"]["source_run_artifact_count_positive"] is False
    assert evidence["public_context_summary"]["context_pack_version"] == "v3"
    assert "raw_storage_key" not in raw
    assert "s3://private" not in raw


def test_executor_context_pack_generator_cli_prefers_current_repo_when_pythonpath_is_polluted(tmp_path):
    evidence = tmp_path / "executor-context-pack-evidence.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(r"C:\stale\other-product\services\ai-platform"))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_executor_context_pack_evidence_211.py",
            "--run-id",
            "run-a",
            "--evidence-file",
            str(evidence),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    output = json.loads(result.stdout)
    assert output["schema_version"] == "ai-platform.executor-context-pack-211.v1"
    assert output["evidence_strength"] == "source_probe_on_target_runtime"
    assert output["runtime_run_payload_verified"] is False
    assert json.loads(evidence.read_text(encoding="utf-8"))["source_schema_version"] == (
        "ai-platform.executor-context-pack.v1"
    )


def test_executor_context_pack_211_help_separates_source_probe_from_live_acceptance():
    generator = load_generator()
    verifier = load_verifier()

    generator_help = generator.build_parser().format_help()
    verifier_help = verifier.build_parser().format_help()

    assert "source-probe evidence" in generator_help
    assert "does not close" in generator_help
    assert "211 acceptance" in generator_help
    assert "Use --live-run-id with a real 211 run" in generator_help
    assert "require" in verifier_help
    assert "live_worker_run_payload evidence" in verifier_help
    assert "211 acceptance" in verifier_help
