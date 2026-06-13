import importlib.util
import json
import os
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


def test_verify_multiuser_poc_help_runs_as_standalone_script(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools" / "verify_multiuser_poc.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--foundation-runtime-evidence" in result.stdout


def test_psql_json_rows_skips_postgres_command_tags(monkeypatch):
    module = load_verify_multiuser_poc()

    def fake_run(command, check, capture_output, text, timeout):
        assert command[:3] == ["sudo", "-n", "docker"]
        assert check is False
        assert capture_output is True
        assert text is True
        assert timeout == 30.0
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='DO\nINSERT 0 2\n{"prepared_tenant_count": 2}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    rows = module.psql_json_rows(
        container="ai-platform-postgres",
        db_user="ai_platform",
        db_name="ai_platform",
        sql="select 1",
    )

    assert rows == [{"prepared_tenant_count": 2}]


def test_foundation_runtime_case_specs_cover_12_cases_across_two_tenants():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="tenant-a"),
        module.Account(label="tenant-a-user-2", username="a2", password="secret", tenant_id="tenant-a"),
        module.Account(label="tenant-b-user-1", username="b1", password="secret", tenant_id="tenant-b"),
        module.Account(label="tenant-b-user-2", username="b2", password="secret", tenant_id="tenant-b"),
    ]

    specs = module.build_foundation_runtime_case_specs(accounts, min_cases=12)

    assert len(specs) == 12
    assert {spec.account.tenant_id for spec in specs} == {"tenant-a", "tenant-b"}
    assert {spec.scenario for spec in specs} >= {"run_creation", "execution", "cancel", "retry"}
    assert all(spec.message for spec in specs)


def test_foundation_runtime_case_specs_distribute_retry_sources_across_users():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a"),
        module.Account(label="tenant-a-user-2", username="a2", password="secret", tenant_id="frc-test-tenant-a"),
        module.Account(label="tenant-b-user-1", username="b1", password="secret", tenant_id="frc-test-tenant-b"),
        module.Account(label="tenant-b-user-2", username="b2", password="secret", tenant_id="frc-test-tenant-b"),
    ]

    specs = module.build_foundation_runtime_case_specs(accounts, min_cases=12, use_fixture_agents=True)
    retry_specs = [spec for spec in specs if spec.scenario == "retry"]

    assert len(retry_specs) == 3
    assert len({spec.account.username for spec in retry_specs}) == 3
    assert len({module.fixture_retry_source_run_id(spec.account) for spec in retry_specs}) == 3


def test_foundation_runtime_case_specs_can_use_fixture_agent_ids():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a"),
        module.Account(label="tenant-a-user-2", username="a2", password="secret", tenant_id="frc-test-tenant-a"),
        module.Account(label="tenant-b-user-1", username="b1", password="secret", tenant_id="frc-test-tenant-b"),
        module.Account(label="tenant-b-user-2", username="b2", password="secret", tenant_id="frc-test-tenant-b"),
    ]

    specs = module.build_foundation_runtime_case_specs(accounts, min_cases=4, use_fixture_agents=True)

    for spec in specs:
        expected_skill = {
            "run_creation": "general-chat",
            "execution": "qa-file-reviewer",
            "cancel": "general-chat",
            "retry": "baoyu-translate",
        }[spec.scenario]
        assert spec.agent_id == module.fixture_agent_id_for_skill(spec.account, expected_skill)
        assert spec.skill_id == expected_skill
        assert spec.workspace_id == module.fixture_workspace_id(spec.account.tenant_id)


def test_foundation_runtime_evidence_from_results_is_secret_safe_and_complete():
    module = load_verify_multiuser_poc()
    results = []
    for index in range(12):
        tenant = "tenant-a" if index < 6 else "tenant-b"
        user = f"{tenant}-user-{index % 2}"
        scenario = ["run_creation", "execution", "cancel", "retry"][index % 4]
        results.append(
            {
                "tenant_id": tenant,
                "account": user,
                "case": f"case-{index}",
                "scenario": scenario,
                "session_id": f"s-{index}",
                "run_id": f"r-{index}",
                "status": "completed" if scenario != "cancel" else "cancelled",
                "queue_position": index + 1,
                "artifact_ids": [f"art_{index}"] if scenario == "execution" else [],
                "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 10}],
                "cross_user_download_statuses": [404],
                "cross_tenant_download_statuses": [404],
                "cross_user_preview_statuses": [404],
                "cross_tenant_preview_statuses": [404],
                "cancel_action_statuses": [200] if scenario == "cancel" else [],
                "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
                "retry_action_statuses": [409] if scenario == "retry" else [],
                "retry_created_run_ids": [f"retry-{index}"] if scenario == "retry" else [],
                "context_snapshot_id": f"ctx-{index}",
                "sandbox_lease_id": f"lease-{index}",
                "workspace_fingerprint": f"workspace-{tenant}-{index}",
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
                },
                "playback": {"event_order_violations": 0, "private_payload_leak_count": 0},
            }
        )

    evidence = module.build_foundation_runtime_concurrency_evidence(
        results,
        commit_sha="3843395b180324b165cbca7c59b6d7e1a934e290",
        runtime_subject_commit_sha="ac9a86bbea14a28748867cade8d80b2f9ff420ec",
    )

    assert evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert evidence["artifact_kind"] == "foundation_runtime_concurrency"
    assert evidence["summary"]["tenant_count"] == 2
    assert evidence["summary"]["user_count"] == 4
    assert evidence["summary"]["run_count"] == 12
    assert evidence["summary"]["concurrent_request_count"] == 12
    assert evidence["scenario_counts"]["cancel"] == 3
    assert evidence["scenario_counts"]["retry"] == 3
    assert evidence["checks"]["queue_admission"]["cancel_action_statuses"] == [200, 200, 200]
    assert evidence["checks"]["queue_admission"]["cancel_effect_statuses"] == [
        "cancel_requested",
        "cancel_requested",
        "cancel_requested",
    ]
    assert evidence["checks"]["queue_admission"]["cancel_effect_run_count"] == 3
    assert evidence["checks"]["queue_admission"]["retry_action_statuses"] == [409, 409, 409]
    assert evidence["checks"]["queue_admission"]["retry_created_run_count"] == 3
    assert evidence["checks"]["sandbox_workspace"]["workspace_scope_sample_count"] == 12
    assert evidence["checks"]["memory_context"]["context_snapshot_count"] == 12
    cross_tenant_statuses = evidence["checks"]["artifact_acl"]["cross_tenant_statuses"]
    assert len(cross_tenant_statuses) == 12
    assert set(cross_tenant_statuses) == {404}
    assert evidence["checks"]["tool_permission"]["decision_sample_count"] == 12
    assert evidence["checks"]["tool_permission"]["allow_once_reuse_violations"] == 0
    assert evidence["checks"]["skill_snapshots"]["run_skill_snapshot_count"] == 12
    assert evidence["role_provenance"] == {
        "run_creation_role": "user",
        "public_probe_role": "user",
        "admin_probe_role": None,
        "ordinary_user_multi_agent_opened": False,
        "developer_role_used_only_for_fixture_agent_selection": False,
    }
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    assert "secret" not in serialized
    assert "authorization" not in serialized
    assert "bearer " not in serialized


def test_playback_private_payload_leak_count_allows_safe_token_metrics():
    module = load_verify_multiuser_poc()

    payload = {
        "contract_version": "ai-platform.run-playback.v1",
        "events": [
            {
                "event_type": "assistant_message_created",
                "token_counts": {"input": 12, "output": 8, "total": 20},
                "payload": {
                    "input_token_count": 12,
                    "output_token_count": 8,
                    "total_token_count": 20,
                    "remaining_token_budget": 100,
                    "message": "token_counts and token budget are public observability metadata",
                },
            }
        ],
    }

    assert module.playback_private_payload_leak_count(payload) == 0


def test_playback_private_payload_leak_count_rejects_sensitive_keys_and_values():
    module = load_verify_multiuser_poc()

    assert module.playback_private_payload_leak_count({"storage_key": "tenants/default/hidden.docx"}) == 1
    assert module.playback_private_payload_leak_count({"payload": {"private_payload": {"cwd": "/tmp/run"}}}) == 1
    assert module.playback_private_payload_leak_count({"message": "Authorization: Bearer abcdefgh12345678"}) == 1


def test_foundation_runtime_acl_probe_records_cross_scope_denials(monkeypatch):
    module = load_verify_multiuser_poc()
    owner = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="tenant-a")
    cross_user = module.Account(label="tenant-a-user-2", username="a2", password="secret", tenant_id="tenant-a")
    cross_tenant = module.Account(label="tenant-b-user-1", username="b1", password="secret", tenant_id="tenant-b")
    result = {
        "account": owner.label,
        "tenant_id": owner.tenant_id,
        "artifact_ids": ["art_1"],
        "downloads": [{"artifact_id": "art_1", "owner_status": 200, "owner_bytes": 4}],
    }
    calls = []

    def fake_login(api_url, account):
        return {"Authorization": f"Bearer token-{account.label}", "X-AI-Tenant-ID": account.tenant_id}

    def fake_get_bytes(url, headers):
        calls.append((url, headers))
        assert headers["Authorization"].startswith("Bearer token-")
        return (404, b"")

    monkeypatch.setattr(module, "login", fake_login)
    monkeypatch.setattr(module, "get_bytes", fake_get_bytes)

    module.attach_artifact_acl_probe_results("http://api.test", [result], [owner, cross_user, cross_tenant])

    assert result["cross_user_download_statuses"] == [404]
    assert result["cross_tenant_download_statuses"] == [404]
    assert result["cross_user_preview_statuses"] == [404]
    assert result["cross_tenant_preview_statuses"] == [404]
    assert len(calls) == 4
    assert all("token-" in headers["Authorization"] for _, headers in calls)


def test_foundation_runtime_cli_evidence_mode_outputs_schema_json(monkeypatch, capsys):
    module = load_verify_multiuser_poc()
    completed = []

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        workspace_id="default",
        gateway_secret="",
        **_kwargs,
    ):
        completed.append((account.label, scenario, docx_path is not None))
        index = len(completed) - 1
        return {
            "tenant_id": account.tenant_id,
            "account": account.label,
            "case": case_name,
            "scenario": scenario,
            "session_id": f"s-{index}",
            "run_id": f"r-{index}",
            "status": "completed" if scenario != "cancel" else "cancelled",
            "queue_position": index + 1,
            "artifact_ids": [f"art_{index}"],
            "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 8}],
            "cancel_action_statuses": [200] if scenario == "cancel" else [],
            "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
            "retry_action_statuses": [409] if scenario == "retry" else [],
            "retry_created_run_ids": [f"retry-{index}"] if scenario == "retry" else [],
            "has_tmp_path": False,
        }

    def fake_attach_acl(api_url, results, accounts, **_kwargs):
        for item in results:
            item["cross_user_download_statuses"] = [404]
            item["cross_tenant_download_statuses"] = [404]
            item["cross_user_preview_statuses"] = [404]
            item["cross_tenant_preview_statuses"] = [404]

    def fake_attach_run_details(api_url, results, _accounts, **_kwargs):
        for index, item in enumerate(results):
            item["context_snapshot_id"] = f"ctx-{index}"
            item["sandbox_lease_id"] = f"lease-{index}"
            item["workspace_fingerprint"] = f"workspace-{item['tenant_id']}-{index}"
            item["tool_permission"] = {
                "decision_sample_count": 1,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            }
            item["skill_snapshot"] = {
                "run_skill_snapshot_count": 1,
                "used_count": 1,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
            }
            item["playback"] = {"event_order_violations": 0, "private_payload_leak_count": 0}

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_run_details)
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
            str(Path(__file__).resolve()),
            "--account",
            "tenant-a/tenant-a-user-1=a1:secret",
            "--account",
            "tenant-a/tenant-a-user-2=a2:secret",
            "--account",
            "tenant-b/tenant-b-user-1=b1:secret",
            "--account",
            "tenant-b/tenant-b-user-2=b2:secret",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    assert len(completed) == 12
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert evidence["summary"]["tenant_count"] == 2
    assert evidence["summary"]["concurrent_request_count"] == 12
    assert evidence["role_provenance"]["run_creation_role"] == "user"
    assert evidence["role_provenance"]["public_probe_role"] == "user"
    readiness = build_foundation_runtime_concurrency_readiness(evidence)
    assert readiness["verified"] is True


def test_foundation_runtime_cli_cleanup_mode_embeds_cleanup_proof(monkeypatch, capsys):
    module = load_verify_multiuser_poc()

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        workspace_id="default",
        gateway_secret="",
        **_kwargs,
    ):
        index = len(fake_run_case.results)
        result = {
            "tenant_id": account.tenant_id,
            "account": account.label,
            "case": case_name,
            "scenario": scenario,
            "session_id": f"s-{index}",
            "run_id": f"r-{index}",
            "status": "completed" if scenario != "cancel" else "cancelled",
            "queue_position": index + 1,
            "artifact_ids": [f"art_{index}"],
            "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 8}],
            "cancel_action_statuses": [200] if scenario == "cancel" else [],
            "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
            "retry_action_statuses": [200] if scenario == "retry" else [],
            "retry_created_run_ids": [f"retry-{index}"] if scenario == "retry" else [],
            "has_tmp_path": False,
        }
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    def fake_attach_acl(api_url, results, accounts, **_kwargs):
        for item in results:
            item["cross_user_download_statuses"] = [404]
            item["cross_tenant_download_statuses"] = [404]
            item["cross_user_preview_statuses"] = [404]
            item["cross_tenant_preview_statuses"] = [404]

    def fake_attach_run_details(api_url, results, _accounts, **_kwargs):
        for index, item in enumerate(results):
            item["context_snapshot_id"] = f"ctx-{index}"
            item["sandbox_lease_id"] = f"lease-{index}"
            item["workspace_fingerprint"] = f"workspace-{item['tenant_id']}-{index}"
            item["tool_permission"] = {
                "decision_sample_count": 1,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            }
            item["skill_snapshot"] = {
                "run_skill_snapshot_count": 1,
                "used_count": 1,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
            }
            item["playback"] = {"event_order_violations": 0, "private_payload_leak_count": 0}

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_run_details)
    monkeypatch.setattr(
        module,
        "build_foundation_runtime_cleanup_proof",
        lambda tenant_ids, **_kwargs: {
            "schema_version": "ai-platform.foundation-runtime-cleanup-proof.v1",
            "status": "verified",
            "tenant_ids": sorted(tenant_ids),
            "remaining_counts": {
                f"remaining_{table_name}_count": 0
                for table_name, _tenant_column in module.FOUNDATION_RUNTIME_CLEANUP_COUNT_TABLES
            },
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--cleanup-test-tenants",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(Path(__file__).resolve()),
            "--account",
            "frc-test-tenant-a/tenant-a-user-1=a1:secret",
            "--account",
            "frc-test-tenant-a/tenant-a-user-2=a2:secret",
            "--account",
            "frc-test-tenant-b/tenant-b-user-1=b1:secret",
            "--account",
            "frc-test-tenant-b/tenant-b-user-2=b2:secret",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["cleanup_proof"]["status"] == "verified"
    assert evidence["cleanup_proof"]["tenant_ids"] == ["frc-test-tenant-a", "frc-test-tenant-b"]
    assert evidence["cleanup_proof"]["remaining_counts"] == {
        f"remaining_{table_name}_count": 0
        for table_name, _tenant_column in module.FOUNDATION_RUNTIME_CLEANUP_COUNT_TABLES
    }


def test_verify_multiuser_poc_rejects_foundation_evidence_without_two_tenants():
    result = subprocess.run(
        [
            sys.executable,
            "tools/verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--account",
            "tenant-a/tenant-a-user-1=a1:secret",
            "--account",
            "tenant-a/tenant-a-user-2=a2:secret",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "at least two tenants" in result.stderr.lower() or "at least two tenants" in result.stdout.lower()


def test_foundation_runtime_evidence_rejects_retry_409_without_created_run():
    module = load_verify_multiuser_poc()
    results = []
    for index in range(12):
        tenant = "tenant-a" if index < 6 else "tenant-b"
        user = f"{tenant}-user-{index % 2}"
        scenario = ["run_creation", "execution", "cancel", "retry"][index % 4]
        results.append(
            {
                "tenant_id": tenant,
                "account": user,
                "case": f"case-{index}",
                "scenario": scenario,
                "session_id": f"s-{index}",
                "run_id": f"r-{index}",
                "status": "completed",
                "artifact_ids": [f"art_{index}"],
                "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 10}],
                "cross_user_download_statuses": [404],
                "cross_tenant_download_statuses": [404],
                "cross_user_preview_statuses": [404],
                "cross_tenant_preview_statuses": [404],
                "cancel_action_statuses": [200] if scenario == "cancel" else [],
                "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
                "retry_action_statuses": [409] if scenario == "retry" else [],
                "retry_created_run_ids": [],
                "context_snapshot_id": f"ctx-{index}",
                "sandbox_lease_id": f"lease-{index}",
                "workspace_fingerprint": f"workspace-{tenant}-{index}",
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
                },
                "playback": {"event_order_violations": 0, "private_payload_leak_count": 0},
            }
        )

    evidence = module.build_foundation_runtime_concurrency_evidence(
        results,
        commit_sha="3843395b180324b165cbca7c59b6d7e1a934e290",
        runtime_subject_commit_sha="ac9a86bbea14a28748867cade8d80b2f9ff420ec",
    )

    readiness = build_foundation_runtime_concurrency_readiness(evidence)

    assert evidence["checks"]["queue_admission"]["retry_action_statuses"] == [409, 409, 409]
    assert evidence["checks"]["queue_admission"]["retry_created_run_count"] == 0
    assert "run_control_retry_created_run_missing" in readiness["failures"]


def test_run_case_cancel_requests_control_before_waiting(monkeypatch, tmp_path):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="tenant-a")
    calls = []

    monkeypatch.setattr(module, "auth_headers", lambda *_args, **_kwargs: {"X-AI-User-ID": "a1"})
    monkeypatch.setattr(
        module,
        "submit_chat",
        lambda *_args, **_kwargs: {"session_id": "session-a", "run_id": "run-a", "queue_position": 1},
    )
    monkeypatch.setattr(module, "stream_answer", lambda *_args, **_kwargs: "")

    def fake_wait_status(*_args, **_kwargs):
        calls.append("wait")
        return {"status": "completed", "raw_status": "cancelled"}

    def fake_run_control_action(*_args, **_kwargs):
        calls.append("cancel")
        return 200, {"status": "cancel_requested"}

    monkeypatch.setattr(module, "wait_status", fake_wait_status)
    monkeypatch.setattr(module, "run_control_action", fake_run_control_action)

    result = module.run_case(
        "http://api.test",
        account,
        "cancel-probe",
        "general-agent",
        "cancel me",
        None,
        scenario="cancel",
    )

    assert calls == ["cancel", "wait"]
    assert result["cancel_action_statuses"] == [200]
    assert result["cancel_effect_statuses"][0] == "cancel_requested"
    assert "cancelled" in result["cancel_effect_statuses"]


def test_run_case_passes_workspace_to_docx_upload(monkeypatch, tmp_path):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="tenant-a")
    docx_path = tmp_path / "sample.docx"
    docx_path.write_bytes(b"docx")
    observed = {}

    monkeypatch.setattr(module, "auth_headers", lambda *_args, **_kwargs: {"X-AI-User-ID": "a1"})

    def fake_upload_docx(api_url, headers, path, *, workspace_id="default"):
        observed["upload_workspace_id"] = workspace_id
        return {
            "key": "file_1",
            "name": path.name,
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": 4,
        }

    def fake_submit_chat(api_url, headers, *, agent_id, message, attachment=None, workspace_id="default", skill_id=None):
        observed["submit_workspace_id"] = workspace_id
        observed["submit_skill_id"] = skill_id
        return {"session_id": "session-a", "run_id": "run-a", "queue_position": 1}

    monkeypatch.setattr(module, "upload_docx", fake_upload_docx)
    monkeypatch.setattr(module, "submit_chat", fake_submit_chat)
    monkeypatch.setattr(module, "wait_status", lambda *_args, **_kwargs: {"status": "completed"})
    monkeypatch.setattr(module, "stream_answer", lambda *_args, **_kwargs: "")

    result = module.run_case(
        "http://api.test",
        account,
        "docx-probe",
        "agent-a",
        "review this",
        docx_path,
        workspace_id="workspace-a",
        skill_id="qa-file-reviewer",
    )

    assert observed == {
        "upload_workspace_id": "workspace-a",
        "submit_workspace_id": "workspace-a",
        "submit_skill_id": "qa-file-reviewer",
    }
    assert result["workspace_id"] == "workspace-a"


def test_attach_admin_run_detail_probe_results_aggregates_safe_admin_projection(monkeypatch):
    module = load_verify_multiuser_poc()
    admin = module.Account(label="tenant-a-admin", username="admin-a", password="secret", tenant_id="tenant-a")
    results = [
        {
            "tenant_id": "tenant-a",
            "account": "tenant-a-user-1",
            "run_id": "run-a",
            "session_id": "session-a",
        }
    ]

    monkeypatch.setattr(module, "auth_headers", lambda *_args, **_kwargs: {"X-AI-Roles": "developer"})

    def fake_json_request(method, url, payload=None, headers=None, timeout=30.0):
        assert method == "GET"
        assert url.endswith("/api/ai/admin/runs/run-a")
        return 200, {
            "run": {
                "run_id": "run-a",
                "session_id": "session-a",
                "workspace_id": "default",
                "input": {"context_snapshot_id": "ctx-a"},
                "result": {"sandbox_lease_id": "lease-a"},
            },
            "events": [
                {
                    "event_type": "tool_permission_decided",
                    "payload": {
                        "request_id": "perm-a",
                        "tool_call_id": "tool-a",
                        "decision": "allow_once",
                    },
                }
            ],
            "skill_snapshots": [
                {
                    "skill_id": "qa-file-reviewer",
                    "content_hash": "hash-a",
                    "skill_version": "hash-a",
                    "used": True,
                    "usage": {"used_skills_source": "executor_hook"},
                }
            ],
        }

    monkeypatch.setattr(module, "json_request", fake_json_request)

    module.attach_admin_run_detail_probe_results(
        "http://api.test",
        results,
        [admin],
        auth_mode="trusted-header",
    )

    assert results[0]["context_snapshot_id"] == "ctx-a"
    assert results[0]["sandbox_lease_id"] == "lease-a"
    assert results[0]["tool_permission"]["decision_sample_count"] == 1
    assert results[0]["skill_snapshot"]["run_skill_snapshot_count"] == 1
    serialized = json.dumps(results, ensure_ascii=False).lower()
    assert "secret" not in serialized
    assert "bearer " not in serialized


def test_build_foundation_runtime_cleanup_sql_rejects_non_test_tenants():
    module = load_verify_multiuser_poc()

    try:
        module.build_foundation_runtime_cleanup_sql(["tenant-a"])
    except ValueError as exc:
        assert "test tenant" in str(exc).lower()
    else:
        raise AssertionError("non-test tenant cleanup must fail closed")


def test_build_foundation_runtime_cleanup_sql_deletes_only_test_tenant_scope():
    module = load_verify_multiuser_poc()

    sql = module.build_foundation_runtime_cleanup_sql(
        ["frc-test-tenant-a", "frc-test-tenant-b"],
        tenant_prefix="frc-test-",
    )

    assert "delete from tenants" in sql.lower()
    assert "frc-test-tenant-a" in sql
    assert "frc-test-tenant-b" in sql
    assert "('tenant-a'" not in sql.replace("frc-test-tenant-a", "").replace("frc-test-tenant-b", "")
    assert "delete from run_events" in sql.lower()
    assert "delete from run_context_snapshots" in sql.lower()
    assert "delete from run_tool_permission_requests" in sql.lower()
    assert "delete from sandbox_leases" in sql.lower()
    assert "delete from artifacts" in sql.lower()
    assert "delete from files" in sql.lower()
    assert "delete from runs" in sql.lower()
    assert "delete from sessions" in sql.lower()


def test_build_foundation_runtime_cleanup_count_sql_counts_all_deleted_tables():
    module = load_verify_multiuser_poc()

    sql = module.build_foundation_runtime_cleanup_count_sql(
        ["frc-test-tenant-a", "frc-test-tenant-b"],
        tenant_prefix="frc-test-",
    ).lower()

    for table_name in (
        "tenants",
        "workspaces",
        "users",
        "agents",
        "tenant_workbench_skills",
        "tool_policies",
        "sessions",
        "runs",
        "run_events",
        "run_context_snapshots",
        "run_tool_permission_requests",
        "sandbox_leases",
        "run_skill_snapshots",
        "run_steps",
        "artifacts",
        "files",
        "messages",
        "audit_logs",
        "memory_records",
        "memory_policies",
    ):
        assert f"from {table_name}" in sql
        assert f"remaining_{table_name}_count" in sql


def test_build_foundation_runtime_cleanup_proof_uses_psql_and_counts_remaining(monkeypatch):
    module = load_verify_multiuser_poc()
    calls = []

    def fake_psql_json_rows(*, container, db_user, db_name, sql, timeout_seconds=30.0):
        calls.append(
            {
                "container": container,
                "db_user": db_user,
                "db_name": db_name,
                "sql": sql,
                "timeout_seconds": timeout_seconds,
            }
        )
        return [
            {
                f"remaining_{table_name}_count": 0
                for table_name, _tenant_column in module.FOUNDATION_RUNTIME_CLEANUP_COUNT_TABLES
            }
        ]

    monkeypatch.setattr(module, "psql_json_rows", fake_psql_json_rows)

    proof = module.build_foundation_runtime_cleanup_proof(
        ["frc-test-tenant-a", "frc-test-tenant-b"],
        postgres_container="ai-platform-postgres",
        postgres_user="ai_platform",
        postgres_db="ai_platform",
        tenant_prefix="frc-test-",
    )

    assert proof["schema_version"] == "ai-platform.foundation-runtime-cleanup-proof.v1"
    assert proof["status"] == "verified"
    assert proof["tenant_ids"] == ["frc-test-tenant-a", "frc-test-tenant-b"]
    assert proof["remaining_counts"] == {
        f"remaining_{table_name}_count": 0
        for table_name, _tenant_column in module.FOUNDATION_RUNTIME_CLEANUP_COUNT_TABLES
    }
    assert calls[0]["container"] == "ai-platform-postgres"
    assert "delete from tenants" in calls[0]["sql"].lower()
    serialized = json.dumps(proof, ensure_ascii=False).lower()
    assert "secret" not in serialized
    assert "bearer " not in serialized


def test_foundation_runtime_cli_evidence_mode_fails_when_readiness_rejects_evidence(monkeypatch, capsys):
    module = load_verify_multiuser_poc()

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        workspace_id="default",
        gateway_secret="",
        **_kwargs,
    ):
        index = len(fake_run_case.results)
        result = {
            "tenant_id": account.tenant_id,
            "account": account.label,
            "case": case_name,
            "scenario": scenario,
            "session_id": f"s-{index}",
            "run_id": f"r-{index}",
            "status": "completed" if scenario != "cancel" else "cancelled",
            "queue_position": index + 1,
            "artifact_ids": [f"art_{index}"],
            "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 8}],
            "cancel_action_statuses": [200] if scenario == "cancel" else [],
            "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
            "retry_action_statuses": [409] if scenario == "retry" else [],
            "retry_created_run_ids": [],
            "has_tmp_path": False,
        }
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    def fake_attach_acl(api_url, results, accounts, **_kwargs):
        for item in results:
            item["cross_user_download_statuses"] = [404]
            item["cross_tenant_download_statuses"] = [404]
            item["cross_user_preview_statuses"] = [404]
            item["cross_tenant_preview_statuses"] = [404]

    def fake_attach_run_details(api_url, results, _accounts, **_kwargs):
        for index, item in enumerate(results):
            item["context_snapshot_id"] = f"ctx-{index}"
            item["sandbox_lease_id"] = f"lease-{index}"
            item["workspace_fingerprint"] = f"workspace-{item['tenant_id']}-{index}"
            item["tool_permission"] = {
                "decision_sample_count": 1,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            }
            item["skill_snapshot"] = {
                "run_skill_snapshot_count": 1,
                "used_count": 1,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
            }
            item["playback"] = {"event_order_violations": 0, "private_payload_leak_count": 0}

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_run_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--sample-docx",
            str(Path(__file__).resolve()),
            "--account",
            "tenant-a/tenant-a-user-1=a1:secret",
            "--account",
            "tenant-a/tenant-a-user-2=a2:secret",
            "--account",
            "tenant-b/tenant-b-user-1=b1:secret",
            "--account",
            "tenant-b/tenant-b-user-2=b2:secret",
        ],
    )

    exit_code = module.main()
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["readiness"]["verified"] is False
    assert "run_control_retry_created_run_missing" in output["readiness"]["failures"]


def test_build_foundation_runtime_cleanup_proof_counts_after_delete_in_separate_query(monkeypatch):
    module = load_verify_multiuser_poc()
    calls = []

    def fake_psql_json_rows(*, container, db_user, db_name, sql, timeout_seconds=30.0):
        calls.append(sql.lower())
        if len(calls) == 1:
            return []
        return [
            {
                f"remaining_{table_name}_count": 0
                for table_name, _tenant_column in module.FOUNDATION_RUNTIME_CLEANUP_COUNT_TABLES
            }
        ]

    monkeypatch.setattr(module, "psql_json_rows", fake_psql_json_rows)

    proof = module.build_foundation_runtime_cleanup_proof(
        ["frc-test-tenant-a", "frc-test-tenant-b"],
        postgres_container="ai-platform-postgres",
        postgres_user="ai_platform",
        postgres_db="ai_platform",
        tenant_prefix="frc-test-",
    )

    assert proof["status"] == "verified"
    assert len(calls) == 2
    assert "delete from tenants" in calls[0]
    assert "delete from tenants" not in calls[1]
    assert "remaining_tenants_count" in calls[1]
    assert "remaining_run_events_count" in calls[1]


def test_build_foundation_runtime_fixture_sql_rejects_non_test_tenants():
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="tenant-a")

    try:
        module.build_foundation_runtime_fixture_sql([account])
    except ValueError as exc:
        assert "test tenant" in str(exc).lower()
    else:
        raise AssertionError("foundation runtime fixture preparation must fail closed outside test tenants")


def test_build_foundation_runtime_fixture_sql_prepares_retry_and_permission_scope():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a"),
        module.Account(label="tenant-b-user-1", username="b1", password="secret", tenant_id="frc-test-tenant-b"),
    ]

    sql = module.build_foundation_runtime_fixture_sql(accounts, tenant_prefix="frc-test-")

    normalized = sql.lower()
    assert "insert into tenants" in normalized
    assert "insert into workspaces" in normalized
    assert "insert into users" in normalized
    assert "insert into tenant_workbench_skills" in normalized
    assert "insert into mcp_tools" in normalized
    assert "insert into tool_policies" in normalized
    assert "insert into agents" in normalized
    assert "insert into sessions" in normalized
    assert "insert into runs" in normalized
    assert "status, input_json, result_json" in normalized
    assert "'failed'" in normalized
    assert "insert into run_events" in normalized
    assert "insert into run_context_snapshots" in normalized
    assert "insert into sandbox_leases" in normalized
    assert "insert into run_skill_snapshots" in normalized
    assert "frc-test-tenant-a" in sql
    assert "frc-test-tenant-b" in sql
    assert "('tenant-a'" not in sql.replace("frc-test-tenant-a", "").replace("frc-test-tenant-b", "")
    serialized = json.dumps({"sql": sql}, ensure_ascii=False).lower()
    assert "secret" not in serialized
    assert "bearer " not in serialized


def test_build_foundation_runtime_fixture_sql_fails_closed_on_global_id_conflicts():
    module = load_verify_multiuser_poc()
    accounts = [
        module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a"),
        module.Account(label="tenant-b-user-1", username="b1", password="secret", tenant_id="frc-test-tenant-b"),
    ]

    sql = module.build_foundation_runtime_fixture_sql(accounts, tenant_prefix="frc-test-")

    normalized = sql.lower()
    assert "fixture_global_workspace_id_conflict" in normalized
    assert "fixture_global_agent_id_conflict" in normalized
    assert "fixture_global_user_id_conflict" in normalized
    assert "on conflict (id) do update set tenant_id = excluded.tenant_id" not in normalized


def test_prepare_foundation_runtime_fixtures_uses_psql(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a")
    calls = []

    def fake_psql_json_rows(*, container, db_user, db_name, sql, timeout_seconds=30.0):
        calls.append(
            {
                "container": container,
                "db_user": db_user,
                "db_name": db_name,
                "sql": sql,
                "timeout_seconds": timeout_seconds,
            }
        )
        return [{"prepared_tenant_count": 1, "prepared_failed_run_count": 1}]

    monkeypatch.setattr(module, "psql_json_rows", fake_psql_json_rows)

    proof = module.prepare_foundation_runtime_fixtures(
        [account],
        postgres_container="ai-platform-postgres",
        postgres_user="ai_platform",
        postgres_db="ai_platform",
        tenant_prefix="frc-test-",
    )

    assert proof["schema_version"] == "ai-platform.foundation-runtime-fixture-proof.v1"
    assert proof["status"] == "prepared"
    assert proof["tenant_ids"] == ["frc-test-tenant-a"]
    assert proof["prepared_counts"] == {"prepared_tenant_count": 1, "prepared_failed_run_count": 1}
    assert calls[0]["container"] == "ai-platform-postgres"
    assert "insert into runs" in calls[0]["sql"].lower()


def test_attach_retry_fixture_probe_results_creates_retry_from_failed_fixture_run(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a")
    results = [
        {
            "tenant_id": "frc-test-tenant-a",
            "account": "tenant-a-user-1",
            "case": "retry-probe-1",
            "scenario": "retry",
            "run_id": "runtime-run-a",
            "retry_action_statuses": [409],
            "retry_created_run_ids": [],
        }
    ]

    monkeypatch.setattr(module, "auth_headers", lambda *_args, **_kwargs: {"X-AI-User-ID": "a1"})

    def fake_run_control_action(api_url, headers, run_id, action):
        assert api_url == "http://api.test"
        assert run_id == "run_frc_test_tenant_a_a1_retry_source"
        assert action == "retry"
        return 200, {"run_id": "run_retry_created"}

    monkeypatch.setattr(module, "run_control_action", fake_run_control_action)

    module.attach_retry_fixture_probe_results(
        "http://api.test",
        results,
        [account],
        auth_mode="trusted-header",
    )

    assert results[0]["retry_source_run_id"] == "run_frc_test_tenant_a_a1_retry_source"
    assert results[0]["retry_action_statuses"] == [409, 200]
    assert results[0]["retry_created_run_ids"] == ["run_retry_created"]


def test_attach_tool_permission_probe_results_uses_real_request_and_decision_routes(monkeypatch):
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a")
    results = [
        {
            "tenant_id": "frc-test-tenant-a",
            "account": "tenant-a-user-1",
            "case": "general-chat-1",
            "run_id": "run-a",
        }
    ]
    calls = []

    monkeypatch.setattr(module, "auth_headers", lambda *_args, **_kwargs: {"X-AI-User-ID": "a1"})

    def fake_json_request(method, url, payload=None, headers=None, timeout=30.0):
        calls.append((method, url, payload))
        if url.endswith("/api/ai/runs/run-a/tool-permissions/request"):
            return 200, {"permission_request": {"request_id": "perm-a", "status": "pending"}}
        if url.endswith("/api/ai/runs/run-a/tool-permissions/perm-a/decision"):
            return 200, {"permission_request": {"request_id": "perm-a", "status": "decided"}}
        raise AssertionError(url)

    monkeypatch.setattr(module, "json_request", fake_json_request)

    module.attach_tool_permission_probe_results(
        "http://api.test",
        results,
        [account],
        auth_mode="trusted-header",
    )

    assert results[0]["tool_permission_probe"] == {
        "request_status": 200,
        "decision_status": 200,
        "request_id": "perm-a",
    }
    assert [call[0] for call in calls] == ["POST", "POST"]
    serialized = json.dumps(results, ensure_ascii=False).lower()
    assert "secret" not in serialized
    assert "bearer " not in serialized


def test_trusted_principal_headers_include_gateway_secret_when_provided():
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a")

    headers = module.trusted_principal_headers(account, gateway_secret="gateway-secret", role="developer")

    assert headers["X-AI-User-ID"] == "a1"
    assert headers["X-AI-Tenant-ID"] == "frc-test-tenant-a"
    assert headers["X-AI-Roles"] == "developer"
    assert headers["X-AI-Gateway-Secret"] == "gateway-secret"


def test_auth_headers_pass_gateway_secret_to_trusted_header_mode():
    module = load_verify_multiuser_poc()
    account = module.Account(label="tenant-a-user-1", username="a1", password="secret", tenant_id="frc-test-tenant-a")

    headers = module.auth_headers(
        "http://api.test",
        account,
        auth_mode="trusted-header",
        gateway_secret="gateway-secret",
        trusted_header_role="developer",
    )

    assert headers["X-AI-Gateway-Secret"] == "gateway-secret"
    assert headers["X-AI-Roles"] == "developer"


def test_foundation_runtime_cli_trusted_header_mode_reads_gateway_secret_env(monkeypatch, capsys):
    module = load_verify_multiuser_poc()
    observed = []

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        workspace_id="default",
        gateway_secret="",
        trusted_header_role="user",
        skill_id=None,
    ):
        observed.append((auth_mode, gateway_secret, trusted_header_role))
        index = len(observed) - 1
        return {
            "tenant_id": account.tenant_id,
            "account": account.label,
            "case": case_name,
            "scenario": scenario,
            "session_id": f"s-{index}",
            "run_id": f"r-{index}",
            "status": "completed" if scenario != "cancel" else "cancelled",
            "queue_position": index + 1,
            "artifact_ids": [],
            "downloads": [],
            "cancel_action_statuses": [200] if scenario == "cancel" else [],
            "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
            "retry_action_statuses": [200] if scenario == "retry" else [],
            "retry_created_run_ids": [f"retry-{index}"] if scenario == "retry" else [],
            "has_tmp_path": False,
        }

    def fake_attach_acl(api_url, results, accounts, **kwargs):
        observed.append(("acl", kwargs.get("gateway_secret")))
        for item in results:
            item["cross_user_download_statuses"] = [404]
            item["cross_tenant_download_statuses"] = [404]
            item["cross_user_preview_statuses"] = [404]
            item["cross_tenant_preview_statuses"] = [404]

    def fake_attach_run_details(api_url, results, _accounts, **kwargs):
        observed.append(("details", kwargs.get("gateway_secret")))
        for index, item in enumerate(results):
            item["context_snapshot_id"] = f"ctx-{index}"
            item["sandbox_lease_id"] = f"lease-{index}"
            item["workspace_fingerprint"] = f"workspace-{item['tenant_id']}-{index}"
            item["tool_permission"] = {
                "decision_sample_count": 1,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            }
            item["skill_snapshot"] = {
                "run_skill_snapshot_count": 1,
                "used_count": 1,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
            }
            item["playback"] = {"event_order_violations": 0, "private_payload_leak_count": 0}

    monkeypatch.setenv("AI_PLATFORM_TRUSTED_PRINCIPAL_SECRET", "gateway-secret")
    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_run_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--auth-mode",
            "trusted-header",
            "--trusted-header-role",
            "developer",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(Path(__file__).resolve()),
            "--account",
            "tenant-a/tenant-a-user-1=a1:unused",
            "--account",
            "tenant-a/tenant-a-user-2=a2:unused",
            "--account",
            "tenant-b/tenant-b-user-1=b1:unused",
            "--account",
            "tenant-b/tenant-b-user-2=b2:unused",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    assert ("trusted-header", "gateway-secret", "developer") in observed
    assert ("acl", "gateway-secret") in observed
    assert ("details", "gateway-secret") in observed
    serialized = capsys.readouterr().out.lower()
    assert "gateway-secret" not in serialized


def test_foundation_runtime_cli_uses_user_role_for_public_probes_when_creation_uses_developer(monkeypatch, capsys):
    module = load_verify_multiuser_poc()
    observed = []

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        workspace_id="default",
        gateway_secret="",
        trusted_header_role="user",
        skill_id=None,
    ):
        observed.append(("run_case", trusted_header_role, skill_id))
        index = len([item for item in observed if item[0] == "run_case"]) - 1
        return {
            "tenant_id": account.tenant_id,
            "account": account.label,
            "case": case_name,
            "scenario": scenario,
            "session_id": f"s-{index}",
            "run_id": f"r-{index}",
            "status": "completed" if scenario != "cancel" else "cancelled",
            "queue_position": index + 1,
            "artifact_ids": [f"art_{index}"],
            "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 8}],
            "cancel_action_statuses": [200] if scenario == "cancel" else [],
            "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
            "retry_action_statuses": [409] if scenario == "retry" else [],
            "retry_created_run_ids": [],
            "has_tmp_path": False,
        }

    def fake_prepare(accounts, **_kwargs):
        return {
            "schema_version": "ai-platform.foundation-runtime-fixture-proof.v1",
            "status": "prepared",
            "tenant_ids": sorted({account.tenant_id for account in accounts}),
            "prepared_counts": {"prepared_tenant_count": 2, "prepared_failed_run_count": 4},
        }

    def fake_attach_acl(api_url, results, accounts, **kwargs):
        observed.append(("acl", kwargs.get("trusted_header_role")))
        for item in results:
            item["cross_user_download_statuses"] = [404]
            item["cross_tenant_download_statuses"] = [404]
            item["cross_user_preview_statuses"] = [404]
            item["cross_tenant_preview_statuses"] = [404]

    def fake_attach_retry(api_url, results, accounts, **kwargs):
        observed.append(("retry", kwargs.get("trusted_header_role")))
        for item in results:
            if item["scenario"] == "retry":
                item["retry_action_statuses"].append(200)
                item["retry_created_run_ids"].append(f"retry-{item['run_id']}")

    def fake_attach_tool_permissions(api_url, results, accounts, **kwargs):
        observed.append(("tool", kwargs.get("trusted_header_role")))

    def fake_attach_run_details(api_url, results, _accounts, **kwargs):
        observed.append(("details", kwargs.get("trusted_header_role")))
        for index, item in enumerate(results):
            item["context_snapshot_id"] = f"ctx-{index}"
            item["sandbox_lease_id"] = f"lease-{index}"
            item["workspace_fingerprint"] = f"workspace-{item['tenant_id']}-{index}"
            item["tool_permission"] = {
                "decision_sample_count": 1,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            }
            item["skill_snapshot"] = {
                "run_skill_snapshot_count": 1,
                "used_count": 1,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
            }
            item["playback"] = {"event_order_violations": 0, "private_payload_leak_count": 0}

    monkeypatch.setenv("AI_PLATFORM_TRUSTED_PRINCIPAL_SECRET", "gateway-secret")
    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "prepare_foundation_runtime_fixtures", fake_prepare)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_retry_fixture_probe_results", fake_attach_retry)
    monkeypatch.setattr(module, "attach_tool_permission_probe_results", fake_attach_tool_permissions)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_run_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--prepare-foundation-runtime-fixtures",
            "--auth-mode",
            "trusted-header",
            "--trusted-header-role",
            "developer",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(Path(__file__).resolve()),
            "--account",
            "frc-test-tenant-a/tenant-a-user-1=a1:unused",
            "--account",
            "frc-test-tenant-a/tenant-a-user-2=a2:unused",
            "--account",
            "frc-test-tenant-b/tenant-b-user-1=b1:unused",
            "--account",
            "frc-test-tenant-b/tenant-b-user-2=b2:unused",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    run_case_calls = [item for item in observed if item[0] == "run_case"]
    assert {item[1] for item in run_case_calls} == {"developer"}
    assert all(item[2] is not None for item in run_case_calls)
    assert ("acl", "user") in observed
    assert ("retry", "user") in observed
    assert ("tool", "user") in observed
    assert ("details", "user") in observed
    serialized = capsys.readouterr().out.lower()
    assert "gateway-secret" not in serialized


def test_foundation_runtime_cli_fixture_mode_prepares_before_probe(monkeypatch, capsys):
    module = load_verify_multiuser_poc()
    calls = []

    def fake_run_case(
        api_url,
        account,
        case_name,
        agent_id,
        message,
        docx_path,
        scenario="execution",
        auth_mode="login",
        workspace_id="default",
        gateway_secret="",
        **_kwargs,
    ):
        index = len(fake_run_case.results)
        result = {
            "tenant_id": account.tenant_id,
            "account": account.label,
            "case": case_name,
            "scenario": scenario,
            "session_id": f"s-{index}",
            "run_id": f"r-{index}",
            "status": "completed" if scenario != "cancel" else "cancelled",
            "queue_position": index + 1,
            "artifact_ids": [f"art_{index}"],
            "downloads": [{"artifact_id": f"art_{index}", "owner_status": 200, "owner_bytes": 8}],
            "cancel_action_statuses": [200] if scenario == "cancel" else [],
            "cancel_effect_statuses": ["cancel_requested"] if scenario == "cancel" else [],
            "retry_action_statuses": [409] if scenario == "retry" else [],
            "retry_created_run_ids": [],
            "has_tmp_path": False,
        }
        fake_run_case.results.append(result)
        return result

    fake_run_case.results = []

    def fake_prepare(accounts, **_kwargs):
        calls.append(("prepare", sorted(account.tenant_id for account in accounts)))
        return {
            "schema_version": "ai-platform.foundation-runtime-fixture-proof.v1",
            "status": "prepared",
            "tenant_ids": sorted({account.tenant_id for account in accounts}),
            "prepared_counts": {"prepared_tenant_count": 2, "prepared_failed_run_count": 4},
        }

    def fake_attach_retry(api_url, results, accounts, **_kwargs):
        calls.append(("retry", len(results)))
        for item in results:
            if item["scenario"] == "retry":
                item["retry_action_statuses"].append(200)
                item["retry_created_run_ids"].append(f"retry-{item['run_id']}")

    def fake_attach_tool_permissions(api_url, results, accounts, **_kwargs):
        calls.append(("tool", len(results)))

    def fake_attach_acl(api_url, results, accounts, **_kwargs):
        for item in results:
            item["cross_user_download_statuses"] = [404]
            item["cross_tenant_download_statuses"] = [404]
            item["cross_user_preview_statuses"] = [404]
            item["cross_tenant_preview_statuses"] = [404]

    def fake_attach_run_details(api_url, results, _accounts, **_kwargs):
        for index, item in enumerate(results):
            item["context_snapshot_id"] = f"ctx-{index}"
            item["sandbox_lease_id"] = f"lease-{index}"
            item["workspace_fingerprint"] = f"workspace-{item['tenant_id']}-{index}"
            item["tool_permission"] = {
                "decision_sample_count": 1,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            }
            item["skill_snapshot"] = {
                "run_skill_snapshot_count": 1,
                "used_count": 1,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
            }
            item["playback"] = {"event_order_violations": 0, "private_payload_leak_count": 0}

    monkeypatch.setattr(module, "run_case", fake_run_case)
    monkeypatch.setattr(module, "prepare_foundation_runtime_fixtures", fake_prepare)
    monkeypatch.setattr(module, "attach_retry_fixture_probe_results", fake_attach_retry)
    monkeypatch.setattr(module, "attach_tool_permission_probe_results", fake_attach_tool_permissions)
    monkeypatch.setattr(module, "attach_artifact_acl_probe_results", fake_attach_acl)
    monkeypatch.setattr(module, "attach_run_detail_probe_results", fake_attach_run_details)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_multiuser_poc.py",
            "--foundation-runtime-evidence",
            "--prepare-foundation-runtime-fixtures",
            "--commit-sha",
            "3843395b180324b165cbca7c59b6d7e1a934e290",
            "--runtime-subject-commit-sha",
            "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
            "--sample-docx",
            str(Path(__file__).resolve()),
            "--account",
            "frc-test-tenant-a/tenant-a-user-1=a1:secret",
            "--account",
            "frc-test-tenant-a/tenant-a-user-2=a2:secret",
            "--account",
            "frc-test-tenant-b/tenant-b-user-1=b1:secret",
            "--account",
            "frc-test-tenant-b/tenant-b-user-2=b2:secret",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    assert calls[0][0] == "prepare"
    assert ("retry", 12) in calls
    assert ("tool", 12) in calls
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["fixture_proof"]["status"] == "prepared"
    assert evidence["checks"]["queue_admission"]["retry_created_run_count"] == 3
