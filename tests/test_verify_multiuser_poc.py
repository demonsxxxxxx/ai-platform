import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from app.foundation_runtime_concurrency import build_foundation_runtime_concurrency_readiness


def load_verify_multiuser_poc():
    path = Path(__file__).resolve().parents[1] / "tools" / "verify_multiuser_poc.py"
    spec = importlib.util.spec_from_file_location("verify_multiuser_poc", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["verify_multiuser_poc"] = module
    spec.loader.exec_module(module)
    return module


def test_default_sample_docx_contains_translatable_text(tmp_path):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"

    module.write_minimal_docx(sample_path)

    with zipfile.ZipFile(sample_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "This document contains text" in document_xml
    assert "请将这段中文内容翻译为英文" in document_xml


def test_run_case_fetches_context_snapshot_public_projection(monkeypatch):
    module = load_verify_multiuser_poc()
    calls = []

    monkeypatch.setattr(module, "login", lambda api_url, account: {"X-Test-Auth": "test-token"})
    monkeypatch.setattr(
        module,
        "submit_chat",
        lambda api_url, headers, *, agent_id, message, attachment=None, **_kwargs: {
            "session_id": "session-a",
            "run_id": "run-a",
            "queue_position": 1,
        },
    )
    monkeypatch.setattr(
        module,
        "wait_status",
        lambda api_url, headers, session_id, run_id, **_kwargs: {"status": "completed", "raw_status": "succeeded"},
    )
    monkeypatch.setattr(module, "stream_answer", lambda api_url, headers, session_id, run_id: "ok")

    def fake_json_request(method, url, payload=None, headers=None, timeout=30.0):
        calls.append((method, url, headers))
        return 200, {
            "run_id": "run-a",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx-a",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 0,
                            "artifact_count": 0,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": ["message"],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_version": "v1",
                        "context_pack_generated_at": "2026-06-14T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(module, "json_request", fake_json_request)

    result = module.run_case(
        "http://api.local",
        module.Account(label="user-a", username="user-a", password="pw"),
        "general-chat",
        "general-agent",
        "hello",
        None,
    )

    assert calls == [
        (
            "GET",
            "http://api.local/api/ai/runs/run-a/context/snapshots",
            {"X-Test-Auth": "test-token"},
        )
    ]
    projection = result["context_snapshot_public_projection"]
    assert projection["ok"] is True
    assert projection["snapshot_count"] == 1
    assert projection["referenced_material_counts"] == {
        "message_count": 1,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert projection["input_keys"] == ["message"]
    assert projection["context_pack_version"] == "v1"
    assert projection["context_pack_generated_at_present"] is True
    serialized = json.dumps(projection)
    assert "ctx-a" not in serialized
    assert "context_snapshot_id" not in serialized
    assert "included_message_ids" not in serialized


def test_run_case_passes_configured_timeout_to_wait_status(monkeypatch):
    module = load_verify_multiuser_poc()
    captured = {}

    monkeypatch.setattr(module, "login", lambda api_url, account: {"X-Test-Auth": "test-token"})
    monkeypatch.setattr(
        module,
        "submit_chat",
        lambda api_url, headers, *, agent_id, message, attachment=None, **_kwargs: {
            "session_id": "session-a",
            "run_id": "run-a",
            "queue_position": 1,
        },
    )
    monkeypatch.setattr(module, "stream_answer", lambda api_url, headers, session_id, run_id: "ok")
    monkeypatch.setattr(
        module,
        "json_request",
        lambda method, url, payload=None, headers=None, timeout=30.0: (
            200,
            {"run_id": "run-a", "context_snapshots": []},
        ),
    )

    def fake_wait_status(api_url, headers, session_id, run_id, timeout_seconds=240.0):
        captured["timeout_seconds"] = timeout_seconds
        return {"status": "completed", "raw_status": "succeeded"}

    monkeypatch.setattr(module, "wait_status", fake_wait_status)

    module.run_case(
        "http://api.local",
        module.Account(label="user-a", username="user-a", password="pw"),
        "general-chat",
        "general-agent",
        "hello",
        None,
        run_timeout_seconds=420.0,
    )

    assert captured["timeout_seconds"] == 420.0


def test_context_snapshot_public_projection_rejects_unsafe_context_pack_version(monkeypatch):
    module = load_verify_multiuser_poc()

    def fake_json_request(method, url, payload=None, headers=None, timeout=30.0):
        return 200, {
            "run_id": "run-a",
            "context_snapshots": [
                {
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 0,
                            "artifact_count": 0,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": ["message"],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_version": "0123456789abcdef0123456789abcdef",
                        "context_pack_generated_at": "2026-06-14T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(module, "json_request", fake_json_request)

    projection = module.fetch_context_snapshot_public_projection("http://api.local", {}, "run-a")

    assert projection["ok"] is False
    assert projection["context_pack_version"] is None
    assert projection["missing_public_summary_fields"] == ["context_pack_version"]


def test_main_fails_closed_when_context_pack_version_is_missing(monkeypatch, tmp_path, capsys):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"
    module.write_minimal_docx(sample_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--api-url",
            "http://api.local",
            "--sample-docx",
            str(sample_path),
            "--account",
            "user-a=user-a:pw",
            "--account",
            "user-b=user-b:pw",
        ],
    )

    def fake_run_case(api_url, account, case_name, agent_id, message, docx_path):
        return {
            "account": account.label,
            "case": case_name,
            "agent_id": agent_id,
            "session_id": f"session-{account.label}-{case_name}",
            "run_id": f"run-{account.label}-{case_name}",
            "queue_position": 1,
            "status": "completed",
            "raw_status": "succeeded",
            "artifact_ids": ["art_a"] if case_name in {"word-review", "word-translate"} else [],
            "downloads": [{"artifact_id": "art_a", "owner_status": 200, "owner_bytes": 42}]
            if case_name in {"word-review", "word-translate"}
            else [],
            "has_tmp_path": False,
            "context_snapshot_public_projection": {
                "ok": False,
                "snapshot_count": 1,
                "context_pack_version": None,
                "missing_public_summary_fields": ["context_pack_version"],
            },
        }

    monkeypatch.setattr(module, "run_case", fake_run_case)

    assert module.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert {
        "case": "general-chat",
        "account": "user-a",
        "reason": "context_pack_version_missing_or_unsafe",
    } in payload["failures"]
    projection_failures = [
        failure
        for failure in payload["failures"]
        if failure["case"] == "general-chat"
        and failure["account"] == "user-a"
        and failure["reason"] == "context_snapshot_public_projection_failed"
    ]
    assert projection_failures
    assert projection_failures[0]["snapshot_count"] == 1
    assert projection_failures[0]["missing_public_summary_fields"] == ["context_pack_version"]


def test_foundation_runtime_memory_context_summary_counts_public_projection_versions():
    module = load_verify_multiuser_poc()
    results = []
    for index in range(12):
        results.append(
            {
                "account": f"user-{index % 4}",
                "case": f"case-{index}",
                "run_id": f"run-{index}",
                "session_id": f"session-{index}",
                "status": "completed",
                "context_snapshot_public_projection": {
                    "ok": True,
                    "snapshot_count": 1,
                    "context_pack_version": "v1",
                    "missing_public_summary_fields": [],
                    "scope_probe": {
                        "same_run_snapshot": True,
                        "cross_scope_leak": False,
                        "long_term_cross_session_memory_read": False,
                    },
                },
            }
        )

    summary = module.foundation_runtime_memory_context_summary(results)

    assert summary == {
        "status": "passed",
        "context_snapshot_count": 12,
        "context_snapshot_public_projection_count": 12,
        "context_pack_version_sample_count": 12,
        "missing_context_pack_version_count": 0,
        "unsafe_context_pack_version_count": 0,
        "missing_public_summary_fields": [],
        "context_scope_probe_count": 12,
        "cross_scope_context_leaks": 0,
        "long_term_cross_session_memory_read": False,
    }


def test_foundation_runtime_memory_context_summary_flags_missing_or_unsafe_versions():
    module = load_verify_multiuser_poc()
    results = [
        {
            "account": "user-a",
            "case": "case-a",
            "run_id": "run-a",
            "session_id": "session-a",
            "status": "completed",
            "context_snapshot_public_projection": {
                "ok": False,
                "snapshot_count": 1,
                "context_pack_version": None,
                "missing_public_summary_fields": ["context_pack_version"],
            },
        },
        {
            "account": "user-b",
            "case": "case-b",
            "run_id": "run-b",
            "session_id": "session-b",
            "status": "completed",
            "context_snapshot_public_projection": {
                "ok": False,
                "snapshot_count": 1,
                "context_pack_version": "0123456789abcdef0123456789abcdef",
                "missing_public_summary_fields": ["context_pack_version"],
            },
        },
    ]

    summary = module.foundation_runtime_memory_context_summary(results)

    assert summary["status"] == "failed"
    assert summary["context_snapshot_count"] == 2
    assert summary["context_snapshot_public_projection_count"] == 0
    assert summary["context_pack_version_sample_count"] == 0
    assert summary["missing_context_pack_version_count"] == 1
    assert summary["unsafe_context_pack_version_count"] == 1
    assert summary["missing_public_summary_fields"] == ["context_pack_version"]


def complete_foundation_runtime_results(*, context_projection: bool = True):
    results = []
    for index in range(12):
        tenant = "tenant-a" if index < 6 else "tenant-b"
        scenario = ["run_creation", "execution", "cancel", "retry"][index % 4]
        result = {
                "tenant_id": tenant,
                "account": f"{tenant}-user-{index % 2}",
                "case": f"case-{index}",
                "scenario": scenario,
                "session_id": f"session-{index}",
                "run_id": f"run-{index}",
                "status": "completed" if scenario != "cancel" else "cancelled",
                "queue_position": index + 1,
                "artifact_ids": [f"art_{index}"] if scenario == "execution" else [],
                "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 8}],
                "has_tmp_path": False,
                "cross_user_download_statuses": [404],
                "cross_tenant_download_statuses": [404],
                "cross_user_preview_statuses": [404],
                "cross_tenant_preview_statuses": [404],
                "cancel_action_statuses": [200] if scenario == "cancel" else [],
                "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
                "retry_action_statuses": [200] if scenario == "retry" else [],
                "retry_created_run_ids": [f"retry-{index}"] if scenario == "retry" else [],
                "workspace_fingerprint": f"workspace-{tenant}-{index}",
                "sandbox_lease_id": f"lease-{index}",
                "queue_probe": {
                    "source": "redis_metadata",
                    "queue_position": index + 1,
                    "stale_queue_entry": False,
                    "cross_tenant_queue_leak": False,
                    "admission_limit_violation": False,
                },
                "tool_permission": {
                    "decision_sample_count": 1,
                    "allow_once_reuse_violations": 0,
                    "wrong_decision_reuse_violations": 0,
                    "tool_call_id_mismatch_violations": 0,
                },
                "skill_snapshot": {
                    "run_skill_snapshot_count": 1,
                    "used_count": 1,
                    "missing_pinned_snapshots": [],
                    "mismatched_pinned_snapshots": [],
                    "global_mutable_skill_lookup_used": False,
                    "snapshot_binding_sample_count": 1,
                },
                "playback": {"event_order_violations": 0, "private_payload_leak_count": 0},
            }
        if context_projection:
            result["context_snapshot_public_projection"] = {
                "ok": True,
                "snapshot_count": 1,
                "context_pack_version": "v1",
                "missing_public_summary_fields": [],
                "scope_probe": {
                    "same_run_snapshot": True,
                    "cross_scope_leak": False,
                    "long_term_cross_session_memory_read": False,
                },
            }
        results.append(result)
    return results


def test_foundation_runtime_evidence_from_results_includes_context_pack_projection_counts():
    module = load_verify_multiuser_poc()

    evidence = module.build_foundation_runtime_concurrency_evidence(
        complete_foundation_runtime_results(),
        commit_sha="3843395b180324b165cbca7c59b6d7e1a934e290",
        runtime_subject_commit_sha="ac9a86bbea14a28748867cade8d80b2f9ff420ec",
    )

    assert evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert evidence["artifact_kind"] == "foundation_runtime_concurrency"
    assert evidence["summary"]["tenant_count"] == 2
    assert evidence["summary"]["user_count"] == 4
    assert evidence["summary"]["run_count"] == 12
    assert evidence["summary"]["concurrent_request_count"] == 12
    assert evidence["scenario_counts"] == {
        "run_creation": 3,
        "execution": 3,
        "cancel": 3,
        "retry": 3,
    }
    memory_context = evidence["checks"]["memory_context"]
    assert memory_context["context_snapshot_count"] == 12
    assert memory_context["context_snapshot_public_projection_count"] == 12
    assert memory_context["context_pack_version_sample_count"] == 12
    assert memory_context["missing_context_pack_version_count"] == 0
    assert memory_context["unsafe_context_pack_version_count"] == 0
    assert memory_context["missing_public_summary_fields"] == []
    assert memory_context["context_scope_probe_count"] == 12
    assert evidence["checks"]["queue_admission"]["cancel_effect_run_count"] == 3
    assert evidence["checks"]["queue_admission"]["queue_position_sample_count"] == 12
    assert evidence["checks"]["queue_admission"]["queue_probe_source"] == "redis_metadata"
    assert evidence["checks"]["artifact_acl"]["cross_tenant_statuses"] == [404] * 12
    assert evidence["checks"]["tool_permission"]["decision_sample_count"] == 12
    assert evidence["checks"]["skill_snapshots"]["run_skill_snapshot_count"] == 12
    assert evidence["checks"]["skill_snapshots"]["snapshot_binding_sample_count"] == 12
    assert evidence["checks"]["sandbox_workspace"]["sandbox_lease_sample_count"] == 12
    assert evidence["checks"]["sandbox_workspace"]["lease_probe_source"] == "sandbox_leases"
    assert evidence["role_provenance"]["ordinary_user_multi_agent_opened"] is False
    readiness = build_foundation_runtime_concurrency_readiness(evidence)
    assert readiness["verified"] is True
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    assert "authorization" not in serialized
    assert "bearer " not in serialized
    assert "secret" not in serialized


def test_foundation_runtime_evidence_from_results_fails_closed_without_probe_sources():
    module = load_verify_multiuser_poc()
    results = complete_foundation_runtime_results()
    for item in results:
        item.pop("queue_probe")
        item.pop("sandbox_lease_id")
        item["skill_snapshot"].pop("snapshot_binding_sample_count")
        item["context_snapshot_public_projection"].pop("scope_probe")

    evidence = module.build_foundation_runtime_concurrency_evidence(
        results,
        commit_sha="3843395b180324b165cbca7c59b6d7e1a934e290",
        runtime_subject_commit_sha="ac9a86bbea14a28748867cade8d80b2f9ff420ec",
    )

    readiness = build_foundation_runtime_concurrency_readiness(evidence)

    assert evidence["checks"]["queue_admission"]["status"] == "failed"
    assert evidence["checks"]["queue_admission"]["queue_probe_source"] == "missing"
    assert evidence["checks"]["sandbox_workspace"]["status"] == "failed"
    assert evidence["checks"]["sandbox_workspace"]["sandbox_lease_sample_count"] == 0
    assert evidence["checks"]["memory_context"]["status"] == "failed"
    assert evidence["checks"]["memory_context"]["context_scope_probe_count"] == 0
    assert evidence["checks"]["skill_snapshots"]["status"] == "failed"
    assert evidence["checks"]["skill_snapshots"]["snapshot_binding_sample_count"] == 0
    assert readiness["verified"] is False
    assert "check_queue_admission_not_passed" in readiness["failures"]
    assert "check_sandbox_workspace_not_passed" in readiness["failures"]
    assert "check_memory_context_not_passed" in readiness["failures"]
    assert "check_skill_snapshots_not_passed" in readiness["failures"]


def test_foundation_runtime_cli_evidence_mode_fails_closed_without_public_context_projection(
    monkeypatch, tmp_path, capsys
):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"
    module.write_minimal_docx(sample_path)

    def fake_run_case(api_url, account, case_name, agent_id, message, docx_path, scenario="execution", **_kwargs):
        index = len(fake_run_case.results)
        result = complete_foundation_runtime_results()[index]
        result.pop("context_snapshot_public_projection")
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "attach_tool_permission_probe_results", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(sample_path),
            "--account",
            "tenant-a/user-a=user-a:pw",
            "--account",
            "tenant-a/user-b=user-b:pw",
            "--account",
            "tenant-b/user-c=user-c:pw",
            "--account",
            "tenant-b/user-d=user-d:pw",
        ],
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    readiness = payload["readiness"]
    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "memory_context_public_projection_count_insufficient" in readiness["failures"]
    assert "memory_context_pack_version_samples_insufficient" in readiness["failures"]


def test_verify_multiuser_poc_help_names_foundation_runtime_evidence_flag(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools" / "verify_multiuser_poc.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--foundation-runtime-evidence" in result.stdout


def test_foundation_runtime_case_specs_cover_required_scenarios_across_two_tenants():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="tenant-a-user-1", username="a1", password="pw", tenant_id="tenant-a"),
        module.Account(label="tenant-a-user-2", username="a2", password="pw", tenant_id="tenant-a"),
        module.Account(label="tenant-b-user-1", username="b1", password="pw", tenant_id="tenant-b"),
        module.Account(label="tenant-b-user-2", username="b2", password="pw", tenant_id="tenant-b"),
    ]

    specs = module.build_foundation_runtime_case_specs(accounts, min_cases=12)

    assert len(specs) == 12
    assert {spec.account.tenant_id for spec in specs} == {"tenant-a", "tenant-b"}
    assert {spec.scenario for spec in specs} == {"run_creation", "execution", "cancel", "retry"}
    assert all(spec.message for spec in specs)


def test_foundation_runtime_fixture_agents_use_isolated_ids_and_workspaces():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="Tenant A User 1", username="frc_a1", password="pw", tenant_id="frc-test-tenant-a"),
        module.Account(label="Tenant A User 2", username="frc_a2", password="pw", tenant_id="frc-test-tenant-a"),
        module.Account(label="Tenant B User 1", username="frc_b1", password="pw", tenant_id="frc-test-tenant-b"),
        module.Account(label="Tenant B User 2", username="frc_b2", password="pw", tenant_id="frc-test-tenant-b"),
    ]

    specs = module.build_foundation_runtime_case_specs(accounts, min_cases=12, use_fixture_agents=True)

    assert len(specs) == 12
    assert all(spec.workspace_id.startswith("frc_test_") for spec in specs)
    assert all(spec.agent_id.startswith("frc_agent_") for spec in specs)
    assert {spec.skill_id for spec in specs} == {"general-chat", "qa-file-reviewer", "baoyu-translate"}
    assert {
        module.fixture_agent_id_for_skill(spec.account, spec.skill_id)
        for spec in specs
    } == {spec.agent_id for spec in specs}


def test_foundation_runtime_cleanup_rejects_non_test_tenant_ids():
    module = load_verify_multiuser_poc()

    try:
        module.build_foundation_runtime_cleanup_sql(["default"])
    except ValueError as exc:
        assert "cleanup only accepts test tenant ids" in str(exc)
    else:
        raise AssertionError("cleanup must reject non-test tenant ids")


def test_prepare_foundation_runtime_fixtures_executes_safe_sql(monkeypatch):
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="Tenant A User 1", username="frc_a1", password="pw", tenant_id="frc-test-tenant-a"),
        module.Account(label="Tenant B User 1", username="frc_b1", password="pw", tenant_id="frc-test-tenant-b"),
    ]
    captured = []

    def fake_psql_json_rows(*, container, db_user, db_name, sql, timeout_seconds=30.0):
        captured.append((container, db_user, db_name, sql))
        assert "frc-test-tenant-a" in sql
        assert "frc-test-tenant-b" in sql
        assert "'default'" not in sql
        return [{"prepared_tenant_count": 2, "prepared_failed_run_count": 2}]

    monkeypatch.setattr(module, "psql_json_rows", fake_psql_json_rows)

    proof = module.prepare_foundation_runtime_fixtures(
        accounts,
        postgres_container="pg",
        postgres_user="ai_platform",
        postgres_db="ai_platform",
    )

    assert proof["status"] == "prepared"
    assert proof["tenant_ids"] == ["frc-test-tenant-a", "frc-test-tenant-b"]
    assert proof["prepared_counts"] == {
        "prepared_tenant_count": 2,
        "prepared_failed_run_count": 2,
    }
    assert captured


def test_run_case_cancel_and_retry_record_control_probes(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="pw", tenant_id="tenant-a")
    calls = []

    monkeypatch.setattr(module, "login", lambda *_args: {"X-AI-User-ID": "a1"})
    monkeypatch.setattr(
        module,
        "submit_chat",
        lambda *_args, **_kwargs: {"session_id": "session-a", "run_id": "run-a", "queue_position": 1},
    )
    monkeypatch.setattr(module, "stream_answer", lambda *_args: "")
    monkeypatch.setattr(
        module,
        "fetch_context_snapshot_public_projection",
        lambda *_args: {"ok": True, "snapshot_count": 1, "context_pack_version": "v1"},
    )

    def fake_run_control_action(api_url, headers, run_id, action):
        calls.append(action)
        if action == "cancel":
            return 200, {"status": "cancel_requested"}
        return 200, {"run_id": "run-retry-a"}

    monkeypatch.setattr(module, "run_control_action", fake_run_control_action)
    monkeypatch.setattr(module, "wait_status", lambda *_args, **_kwargs: {"status": "completed", "raw_status": "cancelled"})

    cancel_result = module.run_case("http://api.test", account, "cancel-probe", "general-agent", "cancel", None, "cancel")
    retry_result = module.run_case("http://api.test", account, "retry-probe", "general-agent", "retry", None, "retry")

    assert calls == ["cancel", "retry"]
    assert cancel_result["cancel_action_statuses"] == [200]
    assert cancel_result["cancel_effect_statuses"] == ["cancel_requested", "cancelled"]
    assert retry_result["retry_action_statuses"] == [200]
    assert retry_result["retry_created_run_ids"] == ["run-retry-a"]


def test_run_case_retry_uses_configured_source_run_id(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="pw", tenant_id="tenant-a")
    calls = []

    monkeypatch.setattr(module, "login", lambda *_args: {"X-AI-User-ID": "a1"})
    monkeypatch.setattr(
        module,
        "submit_chat",
        lambda *_args, **_kwargs: {"session_id": "session-a", "run_id": "submitted-run", "queue_position": 1},
    )
    monkeypatch.setattr(module, "stream_answer", lambda *_args: "")
    monkeypatch.setattr(module, "wait_status", lambda *_args, **_kwargs: {"status": "completed", "raw_status": "succeeded"})
    monkeypatch.setattr(
        module,
        "fetch_context_snapshot_public_projection",
        lambda *_args: {"ok": True, "snapshot_count": 1, "context_pack_version": "v1"},
    )

    def fake_run_control_action(api_url, headers, run_id, action):
        calls.append((run_id, action))
        return 200, {"run_id": "retry-created-run"}

    monkeypatch.setattr(module, "run_control_action", fake_run_control_action)

    result = module.run_case(
        "http://api.test",
        account,
        "retry-probe",
        "general-agent",
        "retry",
        None,
        "retry",
        retry_source_run_id="fixture-failed-run",
    )

    assert calls == [("fixture-failed-run", "retry")]
    assert result["retry_action_statuses"] == [200]
    assert result["retry_created_run_ids"] == ["retry-created-run"]


def test_attach_artifact_acl_probe_results_records_cross_scope_denials(monkeypatch):
    module = load_verify_multiuser_poc()
    owner = module.Account(label="tenant-a-user-1", username="a1", password="pw", tenant_id="tenant-a")
    cross_user = module.Account(label="tenant-a-user-2", username="a2", password="pw", tenant_id="tenant-a")
    cross_tenant = module.Account(label="tenant-b-user-1", username="b1", password="pw", tenant_id="tenant-b")
    result = {"account": owner.label, "tenant_id": owner.tenant_id, "artifact_ids": ["art_1"]}
    calls = []

    monkeypatch.setattr(module, "login", lambda api_url, account: {"X-AI-User-ID": account.username})

    def fake_get_bytes(url, headers):
        calls.append((url, headers["X-AI-User-ID"]))
        return 404, b""

    monkeypatch.setattr(module, "get_bytes", fake_get_bytes)

    module.attach_artifact_acl_probe_results("http://api.test", [result], [owner, cross_user, cross_tenant])

    assert result["cross_user_download_statuses"] == [404]
    assert result["cross_tenant_download_statuses"] == [404]
    assert result["cross_user_preview_statuses"] == [404]
    assert result["cross_tenant_preview_statuses"] == [404]
    assert len(calls) == 4


def test_attach_run_detail_probe_results_aggregates_safe_projection_and_context(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="pw", tenant_id="tenant-a")
    results = [
        {
            "account": account.label,
            "tenant_id": account.tenant_id,
            "session_id": "session-a",
            "run_id": "run-a",
        }
    ]

    monkeypatch.setattr(module, "login", lambda *_args: {"X-AI-User-ID": "a1"})

    def fake_json_request(method, url, payload=None, headers=None, timeout=30.0):
        assert method == "GET"
        if url.endswith("/api/ai/admin/runs/run-a"):
            return 200, {
                "run": {
                    "workspace_id": "default",
                    "input": {"context_snapshot_id": "ctx-a"},
                    "result": {"sandbox_lease_id": "lease-a"},
                },
                "events": [
                    {
                        "event_type": "tool_permission_decided",
                        "payload": {"request_id": "perm-a", "tool_call_id": "tool-a", "decision": "allow_once"},
                    }
                ],
                "skill_snapshots": [{"skill_id": "general-chat", "used": True}],
            }
        if url.endswith("/api/ai/runs/run-a/playback"):
            return 200, {
                "events": [
                    {"sequence": 1, "event_type": "run_started", "payload": {"message": "safe"}},
                    {"sequence": 2, "event_type": "run_completed", "payload": {"token_counts": {"total": 3}}},
                ]
            }
        raise AssertionError(url)

    monkeypatch.setattr(module, "json_request", fake_json_request)

    module.attach_run_detail_probe_results(
        "http://api.test",
        results,
        [account],
        trusted_header_role="developer",
    )

    assert results[0]["workspace_fingerprint"] == "tenant-a:default:session-a:run-a"
    assert results[0]["context_snapshot_id"] == "ctx-a"
    assert results[0]["sandbox_lease_id"] == "lease-a"
    assert results[0]["tool_permission"]["decision_sample_count"] == 1
    assert results[0]["skill_snapshot"]["run_skill_snapshot_count"] == 1
    assert results[0]["playback"]["event_order_violations"] == 0
    assert results[0]["playback"]["private_payload_leak_count"] == 0
    serialized = json.dumps(results, ensure_ascii=False).lower()
    assert "bearer " not in serialized
    assert "authorization" not in serialized


def test_foundation_runtime_evidence_counts_tool_probe_and_skill_snapshot_samples():
    module = load_verify_multiuser_poc()
    results = complete_foundation_runtime_results()
    for item in results:
        item["tool_permission_probe"] = {
            "request_status": 200,
            "decision_status": 200,
            "request_id": f"perm-{item['run_id']}",
        }
        item["tool_permission"] = {
            "decision_sample_count": 0,
            "allow_once_reuse_violations": 0,
            "wrong_decision_reuse_violations": 0,
            "tool_call_id_mismatch_violations": 0,
        }

    evidence = module.build_foundation_runtime_concurrency_evidence(
        results,
        commit_sha="3843395b180324b165cbca7c59b6d7e1a934e290",
        runtime_subject_commit_sha="ac9a86bbea14a28748867cade8d80b2f9ff420ec",
    )

    assert evidence["checks"]["tool_permission"]["decision_sample_count"] == 12
    assert evidence["checks"]["skill_snapshots"]["run_skill_snapshot_count"] == 12
    readiness = build_foundation_runtime_concurrency_readiness(evidence)
    assert "tool_permission_decision_samples_missing" not in readiness["failures"]
    assert "skill_snapshots_missing_for_runs" not in readiness["failures"]


def test_attach_tool_permission_probe_results_uses_request_and_decision_routes(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="pw", tenant_id="tenant-a")
    results = [{"account": account.label, "tenant_id": account.tenant_id, "run_id": "run-a"}]
    calls = []

    monkeypatch.setattr(module, "login", lambda *_args: {"X-AI-User-ID": "a1"})

    def fake_json_request(method, url, payload=None, headers=None, timeout=30.0):
        calls.append((method, url, payload))
        if url.endswith("/api/ai/runs/run-a/tool-permissions/request"):
            return 200, {"permission_request": {"request_id": "perm-a"}}
        if url.endswith("/api/ai/runs/run-a/tool-permissions/perm-a/decision"):
            return 200, {"permission_request": {"request_id": "perm-a", "status": "decided"}}
        raise AssertionError(url)

    monkeypatch.setattr(module, "json_request", fake_json_request)

    module.attach_tool_permission_probe_results("http://api.test", results, [account])

    assert results[0]["tool_permission_probe"] == {
        "request_status": 200,
        "decision_status": 200,
        "request_id": "perm-a",
    }
    assert [call[0] for call in calls] == ["POST", "POST"]


def test_foundation_runtime_cli_evidence_mode_runs_live_probe_attachments(monkeypatch, tmp_path, capsys):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"
    module.write_minimal_docx(sample_path)
    calls = []

    def fake_run_case(api_url, account, case_name, agent_id, message, docx_path, scenario="execution", **_kwargs):
        index = len(fake_run_case.results)
        result = complete_foundation_runtime_results(context_projection=True)[index]
        result["account"] = account.label
        result["tenant_id"] = account.tenant_id
        result["case"] = case_name
        result["scenario"] = scenario
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    def fake_attach_acl(api_url, results, accounts, **_kwargs):
        calls.append("acl")

    def fake_attach_tool(api_url, results, accounts, **_kwargs):
        calls.append("tool")

    def fake_attach_details(api_url, results, accounts, **_kwargs):
        calls.append("details")

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_tool_permission_probe_results", fake_attach_tool)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(sample_path),
            "--account",
            "tenant-a/user-a=user-a:pw",
            "--account",
            "tenant-a/user-b=user-b:pw",
            "--account",
            "tenant-b/user-c=user-c:pw",
            "--account",
            "tenant-b/user-d=user-d:pw",
        ],
    )

    assert module.main() == 0
    assert calls == ["acl", "tool", "details"]
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["checks"]["memory_context"]["context_pack_version_sample_count"] == 12


def test_trusted_principal_headers_do_not_emit_gateway_secret_by_default():
    module = load_verify_multiuser_poc()
    account = module.Account(label="Tenant A User 1", username="a1", password="unused", tenant_id="tenant-a")

    headers = module.trusted_principal_headers(account)

    assert headers == {
        "X-AI-User-ID": "a1",
        "X-AI-User-Name": "Tenant A User 1",
        "X-AI-Tenant-ID": "tenant-a",
        "X-AI-Roles": "user",
    }
    assert "X-AI-Gateway-Secret" not in headers


def test_foundation_runtime_cli_trusted_header_mode_reaches_run_and_probe_calls(monkeypatch, tmp_path, capsys):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"
    module.write_minimal_docx(sample_path)
    observed_roles = []

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        trusted_header_role="user",
        **_kwargs,
    ):
        observed_roles.append(("run", auth_mode, trusted_header_role))
        index = len(fake_run_case.results)
        result = complete_foundation_runtime_results()[index]
        result["account"] = account.label
        result["tenant_id"] = account.tenant_id
        result["case"] = case_name
        result["scenario"] = scenario
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    def fake_attach_acl(api_url, results, accounts, *, auth_mode="login", trusted_header_role="user"):
        observed_roles.append(("acl", auth_mode, trusted_header_role))

    def fake_attach_tool(api_url, results, accounts, *, auth_mode="login", trusted_header_role="user"):
        observed_roles.append(("tool", auth_mode, trusted_header_role))

    def fake_attach_details(api_url, results, accounts, *, auth_mode="login", trusted_header_role="user"):
        observed_roles.append(("details", auth_mode, trusted_header_role))

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_tool_permission_probe_results", fake_attach_tool)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--auth-mode",
            "trusted-header",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(sample_path),
            "--account",
            "tenant-a/user-a=user-a:unused",
            "--account",
            "tenant-a/user-b=user-b:unused",
            "--account",
            "tenant-b/user-c=user-c:unused",
            "--account",
            "tenant-b/user-d=user-d:unused",
        ],
    )

    assert module.main() == 0
    assert {item for item in observed_roles if item[0] != "run"} == {
        ("acl", "trusted-header", "user"),
        ("tool", "trusted-header", "user"),
        ("details", "trusted-header", "user"),
    }
    assert {item[1:] for item in observed_roles if item[0] == "run"} == {("trusted-header", "user")}
    serialized = capsys.readouterr().out.lower()
    assert "gateway-secret" not in serialized


def test_foundation_runtime_cli_can_prepare_and_cleanup_test_fixtures(monkeypatch, tmp_path, capsys):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"
    module.write_minimal_docx(sample_path)
    calls = []
    run_calls = []
    probe_calls = []

    def fake_prepare(accounts, **kwargs):
        calls.append(("prepare", [account.tenant_id for account in accounts], kwargs))
        return {
            "schema_version": "ai-platform.foundation-runtime-fixture-proof.v1",
            "status": "prepared",
            "tenant_ids": sorted({account.tenant_id for account in accounts}),
            "tenant_prefix": "frc-test-",
            "prepared_counts": {"prepared_tenant_count": 2, "prepared_failed_run_count": 4},
        }

    def fake_cleanup(tenant_ids, **kwargs):
        calls.append(("cleanup", list(tenant_ids), kwargs))
        return {
            "schema_version": "ai-platform.foundation-runtime-cleanup-proof.v1",
            "status": "verified",
            "tenant_ids": sorted(tenant_ids),
            "tenant_prefix": "frc-test-",
            "remaining_counts": {
                "remaining_tenant_count": 0,
                "remaining_run_count": 0,
                "remaining_artifact_count": 0,
            },
        }

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        **kwargs,
    ):
        index = len(fake_run_case.results)
        result = complete_foundation_runtime_results()[index]
        result["account"] = account.label
        result["tenant_id"] = account.tenant_id
        result["case"] = case_name
        result["scenario"] = scenario
        result["workspace_fingerprint"] = f"{account.tenant_id}:{kwargs.get('workspace_id')}:session-{index}:run-{index}"
        run_calls.append(
            {
                "account": account,
                "agent_id": agent_id,
                "auth_mode": kwargs.get("auth_mode"),
                "trusted_header_role": kwargs.get("trusted_header_role"),
                "skill_id": kwargs.get("skill_id"),
                "workspace_id": kwargs.get("workspace_id"),
            }
        )
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    def fake_attach_acl(api_url, results, accounts, *, auth_mode="login", trusted_header_role="user"):
        probe_calls.append(("acl", auth_mode, trusted_header_role))

    def fake_attach_tool(api_url, results, accounts, *, auth_mode="login", trusted_header_role="user"):
        probe_calls.append(("tool", auth_mode, trusted_header_role))

    def fake_attach_details(api_url, results, accounts, *, auth_mode="login", trusted_header_role="user"):
        probe_calls.append(("details", auth_mode, trusted_header_role))

    monkeypatch.setattr(module, "prepare_foundation_runtime_fixtures", fake_prepare)
    monkeypatch.setattr(module, "build_foundation_runtime_cleanup_proof", fake_cleanup)
    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_tool_permission_probe_results", fake_attach_tool)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--auth-mode",
            "trusted-header",
            "--trusted-header-role",
            "user",
            "--prepare-fixtures",
            "--cleanup-before",
            "--cleanup-after",
            "--use-fixture-agents",
            "--postgres-container",
            "pg",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(sample_path),
            "--account",
            "frc-test-tenant-a/user-a=frc_a1:unused",
            "--account",
            "frc-test-tenant-a/user-b=frc_a2:unused",
            "--account",
            "frc-test-tenant-b/user-c=frc_b1:unused",
            "--account",
            "frc-test-tenant-b/user-d=frc_b2:unused",
        ],
    )

    assert module.main() == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["fixture_proof"]["status"] == "prepared"
    assert evidence["cleanup_proof"]["before"]["status"] == "verified"
    assert evidence["cleanup_proof"]["after"]["status"] == "verified"
    assert [call[0] for call in calls] == ["cleanup", "prepare", "cleanup"]
    assert {call[2]["postgres_container"] for call in calls} == {"pg"}
    assert len(run_calls) == 12
    assert {
        (call["auth_mode"], call["trusted_header_role"], call["skill_id"] is not None)
        for call in run_calls
    } == {("trusted-header", "developer", True)}
    assert all(
        call["agent_id"] == module.fixture_agent_id_for_skill(call["account"], call["skill_id"])
        for call in run_calls
    )
    assert all(call["workspace_id"] == module.fixture_workspace_id(call["account"].tenant_id) for call in run_calls)
    assert ("acl", "trusted-header", "user") in probe_calls
    assert ("tool", "trusted-header", "user") in probe_calls
    assert ("details", "trusted-header", "developer") in probe_calls
    assert evidence["role_provenance"]["run_creation_role"] == "developer"
    assert evidence["role_provenance"]["public_probe_role"] == "user"
    assert evidence["role_provenance"]["admin_probe_role"] == "developer"


def test_foundation_runtime_cli_rejects_fixture_agents_without_trusted_header_auth(monkeypatch, tmp_path):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"
    module.write_minimal_docx(sample_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--auth-mode",
            "login",
            "--use-fixture-agents",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(sample_path),
            "--account",
            "frc-test-tenant-a/user-a=frc_a1:unused",
            "--account",
            "frc-test-tenant-a/user-b=frc_a2:unused",
            "--account",
            "frc-test-tenant-b/user-c=frc_b1:unused",
            "--account",
            "frc-test-tenant-b/user-d=frc_b2:unused",
        ],
    )

    try:
        module.main()
    except SystemExit as exc:
        assert "fixture agents require trusted-header auth" in str(exc)
    else:
        raise AssertionError("expected fixture-agent login mode to fail closed")
