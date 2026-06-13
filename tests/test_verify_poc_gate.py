from __future__ import annotations

from pathlib import Path

from tools import verify_poc_gate


def test_api_compat_gate_requires_user_and_admin_permissions(monkeypatch):
    def fake_http_json(url: str):
        if url.endswith("/api/auth/oauth/providers"):
            return 200, {"registration_enabled": False}
        if url.endswith("/api/agent/models/available"):
            return 200, {"default_model_id": "deepseek-v4-flash"}
        if url.endswith("/api/auth/permissions"):
            return 200, {"all_permissions": [{"value": "agent:use"}, {"value": "artifact:download"}]}
        return 200, {}

    monkeypatch.setattr(verify_poc_gate, "http_json", fake_http_json)

    gate = verify_poc_gate.check_api_compat("http://api.local")

    assert gate.ok is False
    assert gate.evidence["missing_permissions"] == ["admin:status", "agent:admin", "model:admin", "settings:manage"]


def test_poc_gate_cli_bootstraps_repo_root_for_direct_script_execution():
    script_text = Path("tools/verify_poc_gate.py").read_text(encoding="utf-8")
    watcher_text = Path("tools/watch_poc_gate.py").read_text(encoding="utf-8")

    required = 'sys.path.insert(0, str(Path(__file__).resolve().parents[1]))'
    assert "import sys" in script_text
    assert required in script_text
    assert required in watcher_text


def test_artifact_download_isolation_gate_accepts_owner_and_denies_cross_user(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_http_get_with_headers(url: str, headers: dict[str, str], timeout: float = 15.0):
        calls.append((url, headers))
        if headers["X-AI-User-ID"] == "artifact-owner" and headers["X-AI-Tenant-ID"] == "default":
            return 200, b"artifact-bytes"
        return 404, b""

    monkeypatch.setattr(verify_poc_gate, "http_get_with_headers", fake_http_get_with_headers)

    gate = verify_poc_gate.check_artifact_download_isolation(
        "http://api.local",
        [
            {
                "artifact_id": "art_1",
                "user_id": "artifact-owner",
                "artifact_size_bytes": 10,
                "artifact_storage_key": "tenants/default/workspaces/default/artifacts/file.docx",
            }
        ],
    )

    assert gate.name == "artifact_download_isolation"
    assert gate.ok is True
    assert gate.evidence["checked_artifacts"] == 1
    assert gate.evidence["results"][0]["owner_status"] == 200
    assert gate.evidence["results"][0]["cross_user_status"] == 404
    assert gate.evidence["results"][0]["cross_tenant_status"] == 404
    assert calls[0][0] == "http://api.local/api/ai/artifacts/art_1/download"
    assert calls[2][1]["X-AI-Tenant-ID"] == "tenant-b"


def test_artifact_download_isolation_gate_rejects_cross_user_access(monkeypatch):
    def fake_http_get_with_headers(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, b"artifact-bytes"

    monkeypatch.setattr(verify_poc_gate, "http_get_with_headers", fake_http_get_with_headers)

    gate = verify_poc_gate.check_artifact_download_isolation(
        "http://api.local",
        [
            {
                "artifact_id": "art_1",
                "user_id": "artifact-owner",
                "artifact_size_bytes": 10,
                "artifact_storage_key": "tenants/default/workspaces/default/artifacts/file.docx",
            }
        ],
    )

    assert gate.ok is False
    assert gate.evidence["results"][0]["cross_user_status"] == 200


def test_artifact_download_isolation_gate_rejects_cross_tenant_access(monkeypatch):
    def fake_http_get_with_headers(url: str, headers: dict[str, str], timeout: float = 15.0):
        if headers["X-AI-Tenant-ID"] == "tenant-b":
            return 200, b"foreign-tenant-bytes"
        if headers["X-AI-User-ID"] == "artifact-owner":
            return 200, b"artifact-bytes"
        return 404, b""

    monkeypatch.setattr(verify_poc_gate, "http_get_with_headers", fake_http_get_with_headers)

    gate = verify_poc_gate.check_artifact_download_isolation(
        "http://api.local",
        [
            {
                "artifact_id": "art_1",
                "user_id": "artifact-owner",
                "artifact_size_bytes": 10,
                "artifact_storage_key": "tenants/default/workspaces/default/artifacts/file.docx",
            }
        ],
    )

    assert gate.ok is False
    assert gate.evidence["results"][0]["cross_user_status"] == 404
    assert gate.evidence["results"][0]["cross_tenant_status"] == 200


def test_artifact_preview_isolation_gate_accepts_owner_preview_and_denies_cross_user(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_http_get_with_headers_and_response_headers(
        url: str,
        headers: dict[str, str],
        timeout: float = 15.0,
    ):
        calls.append((url, headers))
        if headers["X-AI-User-ID"] == "artifact-owner" and headers["X-AI-Tenant-ID"] == "default":
            return (
                200,
                b"preview-bytes",
                {
                    "Cache-Control": "no-store",
                    "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": "inline; filename=review.docx",
                },
            )
        return 404, b"", {}

    monkeypatch.setattr(
        verify_poc_gate,
        "http_get_with_headers_and_response_headers",
        fake_http_get_with_headers_and_response_headers,
    )

    gate = verify_poc_gate.check_artifact_preview_isolation(
        "http://api.local",
        [
            {
                "artifact_id": "art_1",
                "user_id": "artifact-owner",
                "artifact_content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ],
    )

    assert gate.name == "artifact_preview_isolation"
    assert gate.ok is True
    assert gate.evidence["checked_artifacts"] == 1
    assert gate.evidence["results"][0]["owner_status"] == 200
    assert gate.evidence["results"][0]["cross_user_status"] == 404
    assert gate.evidence["results"][0]["cross_tenant_status"] == 404
    assert calls[0][0] == "http://api.local/api/ai/artifacts/art_1/preview"
    assert calls[2][1]["X-AI-Tenant-ID"] == "tenant-b"


def test_artifact_preview_isolation_gate_rejects_missing_security_headers(monkeypatch):
    def fake_http_get_with_headers_and_response_headers(
        url: str,
        headers: dict[str, str],
        timeout: float = 15.0,
    ):
        return 200, b"preview-bytes", {"Cache-Control": "public"}

    monkeypatch.setattr(
        verify_poc_gate,
        "http_get_with_headers_and_response_headers",
        fake_http_get_with_headers_and_response_headers,
    )

    gate = verify_poc_gate.check_artifact_preview_isolation(
        "http://api.local",
        [
            {
                "artifact_id": "art_1",
                "user_id": "artifact-owner",
                "artifact_content_type": "application/pdf",
            }
        ],
    )

    assert gate.ok is False
    assert gate.evidence["results"][0]["owner_cache_control"] == "public"


def test_artifact_preview_isolation_gate_rejects_unallowlisted_response_content_type(monkeypatch):
    def fake_http_get_with_headers_and_response_headers(
        url: str,
        headers: dict[str, str],
        timeout: float = 15.0,
    ):
        if headers["X-AI-User-ID"] == "artifact-owner":
            return (
                200,
                b"preview-bytes",
                {
                    "Cache-Control": "no-store",
                    "Content-Type": "text/html",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": "inline; filename=review.docx",
                },
            )
        return 404, b"", {}

    monkeypatch.setattr(
        verify_poc_gate,
        "http_get_with_headers_and_response_headers",
        fake_http_get_with_headers_and_response_headers,
    )

    gate = verify_poc_gate.check_artifact_preview_isolation(
        "http://api.local",
        [
            {
                "artifact_id": "art_1",
                "user_id": "artifact-owner",
                "artifact_content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ],
    )

    assert gate.ok is False
    assert gate.evidence["results"][0]["owner_content_type"] == "text/html"


def test_frontend_dist_api_boundary_accepts_relative_api(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text('fetch("/api/ai/health")', encoding="utf-8")

    gate = verify_poc_gate.check_frontend_dist_api_boundary(str(tmp_path))

    assert gate.name == "lambchat_frontend_dist_api_boundary"
    assert gate.ok is True
    assert gate.evidence["api_reference_count"] == 1
    assert gate.evidence["forbidden_reference_count"] == 0


def test_frontend_dist_api_boundary_rejects_hardcoded_api_origin(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text('fetch("http://127.0.0.1:18080/api/ai/health")', encoding="utf-8")

    gate = verify_poc_gate.check_frontend_dist_api_boundary(str(tmp_path))

    assert gate.name == "lambchat_frontend_dist_api_boundary"
    assert gate.ok is False
    assert gate.evidence["forbidden_reference_count"] == 1


def test_frontend_origin_api_gate_uses_frontend_url(monkeypatch):
    called_urls: list[str] = []

    def fake_http_json(url: str):
        called_urls.append(url)
        return 200, {"status": "ok"}

    monkeypatch.setattr(verify_poc_gate, "http_json", fake_http_json)

    gate = verify_poc_gate.check_frontend_origin_api("http://frontend.local/")

    assert gate.name == "lambchat_frontend_origin_api"
    assert gate.ok is True
    assert called_urls == ["http://frontend.local/api/ai/health"]
    assert gate.evidence["status"] == 200
    assert gate.evidence["payload"] == {"status": "ok"}


def test_company_auth_bridge_gate_requires_existing_login_backend(monkeypatch):
    def fake_http_json(url: str, payload=None):
        if url == "http://auth.local/api/Login/":
            return 200, {"status": "unsuccessfully!", "workId": None}
        raise AssertionError(url)

    monkeypatch.setattr(verify_poc_gate, "http_json_post", fake_http_json)

    gate = verify_poc_gate.check_company_auth_bridge("http://auth.local")

    assert gate.name == "company_auth_bridge"
    assert gate.ok is True
    assert gate.evidence["login_probe_status"] == 200
    assert gate.evidence["login_probe_payload_status"] == "unsuccessfully!"


def test_container_env_reads_only_poc_whitelisted_runtime_keys(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 0,
            "stdout": (
                "CLAUDE_AGENT_SDK_ENABLED=true\n"
                "CLAUDE_AGENT_MODEL=deepseek-v4-flash\n"
                "OPENAI_MODEL=deepseek-v4-flash\n"
                "ANTHROPIC_MODEL=deepseek-v4-flash\n"
                "CLAUDE_AGENT_SDK_SKILLS=general-chat,qa-file-reviewer,baoyu-translate\n"
                "EXISTING_AUTH_BASE_URL=http://10.56.0.25:7263\n"
                "ANTHROPIC_AUTH_TOKEN=secret-token\n"
            ),
            "stderr": "",
        },
    )()

    def fake_run(command, check=False, capture_output=True, text=True, timeout=30):
        assert command[:4] == ["sudo", "-n", "docker", "exec"]
        return completed

    monkeypatch.setattr(verify_poc_gate.subprocess, "run", fake_run)

    values = verify_poc_gate.read_container_runtime_env("ai-platform-api")

    assert values == {
        "CLAUDE_AGENT_SDK_ENABLED": "true",
        "CLAUDE_AGENT_MODEL": "deepseek-v4-flash",
        "OPENAI_MODEL": "deepseek-v4-flash",
        "ANTHROPIC_MODEL": "deepseek-v4-flash",
        "CLAUDE_AGENT_SDK_SKILLS": "general-chat,qa-file-reviewer,baoyu-translate",
        "EXISTING_AUTH_BASE_URL": "http://10.56.0.25:7263",
    }


def test_runtime_env_values_prefers_live_container_over_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "CLAUDE_AGENT_SDK_ENABLED=true",
                "CLAUDE_AGENT_MODEL=stale-model",
                "OPENAI_MODEL=stale-model",
                "ANTHROPIC_MODEL=stale-model",
                "CLAUDE_AGENT_SDK_SKILLS=general-chat",
                "EXISTING_AUTH_BASE_URL=http://stale-auth.local",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "read_container_runtime_env",
        lambda container: {
            "CLAUDE_AGENT_SDK_ENABLED": "true",
            "CLAUDE_AGENT_MODEL": "deepseek-v4-flash",
            "OPENAI_MODEL": "deepseek-v4-flash",
            "ANTHROPIC_MODEL": "deepseek-v4-flash",
            "CLAUDE_AGENT_SDK_SKILLS": "general-chat,qa-file-reviewer,baoyu-translate",
            "EXISTING_AUTH_BASE_URL": "",
        },
    )

    values = verify_poc_gate.runtime_env_values(str(env_path), "ai-platform-api")

    assert values["CLAUDE_AGENT_MODEL"] == "deepseek-v4-flash"
    assert values["EXISTING_AUTH_BASE_URL"] == ""
    assert values["CLAUDE_AGENT_SDK_SKILLS"] == "general-chat,qa-file-reviewer,baoyu-translate"


def test_runtime_env_values_uses_env_file_only_when_container_env_unavailable(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_AUTH_BASE_URL=http://auth.local\n", encoding="utf-8")
    monkeypatch.setattr(verify_poc_gate, "read_container_runtime_env", lambda container: {})

    values = verify_poc_gate.runtime_env_values(str(env_path), "ai-platform-api")

    assert values["EXISTING_AUTH_BASE_URL"] == "http://auth.local"


def test_container_auth_url_source_of_truth_can_fail_gate_despite_valid_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_AUTH_BASE_URL=http://auth.local\n", encoding="utf-8")
    monkeypatch.setattr(
        verify_poc_gate,
        "read_container_runtime_env",
        lambda container: {"EXISTING_AUTH_BASE_URL": "/api/Login/"},
    )

    values = verify_poc_gate.runtime_env_values(str(env_path), "ai-platform-api")
    gate = verify_poc_gate.check_company_auth_bridge(values.get("EXISTING_AUTH_BASE_URL", ""))

    assert gate.ok is False
    assert gate.evidence["error"] == "missing_or_invalid_existing_auth_base_url"


def test_company_auth_bridge_gate_rejects_missing_login_backend_without_traceback():
    gate = verify_poc_gate.check_company_auth_bridge("")

    assert gate.name == "company_auth_bridge"
    assert gate.ok is False
    assert gate.evidence["configured"] is False
    assert gate.evidence["login_url"] is None


def test_company_auth_bridge_gate_rejects_wrong_login_backend(monkeypatch):
    def fake_http_json(url: str, payload=None):
        return 404, {"message": "Not Found"}

    monkeypatch.setattr(verify_poc_gate, "http_json_post", fake_http_json)

    gate = verify_poc_gate.check_company_auth_bridge("http://wrong.local")

    assert gate.ok is False
    assert gate.evidence["login_probe_status"] == 404


def test_word_review_attachment_chat_routes_to_qa_runner(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(verify_poc_gate, "sample_docx_bytes", lambda: ("review.docx", b"docx-bytes"))
    monkeypatch.setattr(verify_poc_gate.time, "sleep", lambda seconds: None)

    def fake_upload(url: str, **kwargs):
        calls["upload_url"] = url
        calls["upload_kwargs"] = kwargs
        return 200, {"key": "file_review_gate_1"}

    def fake_chat(url: str, payload=None, headers=None, timeout: float = 15.0):
        calls["chat_url"] = url
        calls["chat_payload"] = payload
        calls["chat_headers"] = headers
        return 200, {"run_id": "run_review_gate_1"}

    def fake_psql_rows(container: str, db_user: str, db_name: str, sql: str):
        calls["sql"] = sql
        return [
            {
                "run_id": "run_review_gate_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "succeeded",
                "file_ids": ["file_review_gate_1"],
                "error_message": None,
                "artifact_count": 1,
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
            }
        ]

    def fake_playback(url: str, headers: dict[str, str], timeout: float = 15.0):
        if url.endswith("/context/snapshots"):
            calls["context_url"] = url
            calls["context_headers"] = headers
            return 200, {
                "run_id": "run_review_gate_1",
                "context_snapshots": [
                    {
                        "context_snapshot_id": "ctx_public_1",
                        "payload": {
                            "referenced_materials": {
                                "message_count": 1,
                                "file_count": 1,
                                "artifact_count": 1,
                                "memory_record_count": 0,
                            },
                            "used_context_summary": {
                                "source": "stored_context_snapshot",
                                "input_keys": ["message", "attachments"],
                                "memory_policy_source": "stored",
                                "long_term_memory_read": False,
                            },
                            "execution_tier": "sdk_only_writing",
                            "context_pack_generated_at": "2026-06-12T01:00:00Z",
                        },
                    }
                ],
            }
        calls["playback_url"] = url
        calls["playback_headers"] = headers
        return 200, {
            "contract_version": "ai-platform.run-playback.v1",
            "artifacts": [
                {
                    "artifact_id": "artifact_review_1",
                    "artifact_type": "reviewed_docx",
                    "download_url": "/api/ai/artifacts/artifact_review_1/download",
                    "preview_url": "/api/ai/artifacts/artifact_review_1/preview",
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_multipart_file_post", fake_upload)
    monkeypatch.setattr(verify_poc_gate, "http_json_post_with_headers", fake_chat)
    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_playback)
    monkeypatch.setattr(verify_poc_gate, "psql_rows", fake_psql_rows)

    gate = verify_poc_gate.check_word_review_attachment_chat("http://api.local", "postgres", "user", "db")

    assert gate.name == "word_review_attachment_chat"
    assert gate.ok is True
    assert calls["upload_url"] == "http://api.local/api/upload/file?folder=uploads"
    assert calls["chat_url"] == "http://api.local/api/chat/stream?agent_id=general-agent"
    chat_payload = calls["chat_payload"]
    assert chat_payload["message"] == "审核一下这个文档"
    assert chat_payload["attachments"][0]["key"] == "file_review_gate_1"
    assert gate.evidence["run"]["agent_id"] == "qa-word-review"
    assert gate.evidence["run"]["skill_id"] == "qa-file-reviewer"
    assert gate.evidence["run"]["artifacts"][0]["artifact_type"] == "reviewed_docx"
    assert calls["playback_url"] == "http://api.local/api/ai/runs/run_review_gate_1/playback"
    assert calls["context_url"] == "http://api.local/api/ai/runs/run_review_gate_1/context/snapshots"
    assert gate.evidence["playback"]["preview_url_count"] == 1
    assert gate.evidence["playback"]["matched_preview_artifact_count"] == 1
    assert gate.evidence["playback"]["private_payload_leaked"] is False
    assert gate.evidence["context_snapshot_public_projection"]["ok"] is True


def test_governed_skill_runs_gate_summarizes_real_task_snapshot_pins(monkeypatch):
    queries: list[str] = []
    run_rows = [
        {
            "run_id": "run_review_gate_1",
            "tenant_id": "default",
            "skill_id": "qa-file-reviewer",
            "status": "succeeded",
        },
        {
            "run_id": "run_translate_gate_1",
            "tenant_id": "default",
            "skill_id": "baoyu-translate",
            "status": "succeeded",
        },
    ]

    def fake_psql_rows(container: str, db_user: str, db_name: str, sql: str):
        queries.append(sql)
        if "from run_skill_snapshots" not in sql:
            raise AssertionError(sql)
        return [
            {
                "row_count": 2,
                "used_count": 2,
                "used_skill_ids": ["qa-file-reviewer", "baoyu-translate"],
                "used_skills_sources": ["executor_hook"],
                "pinned_snapshot_count": 2,
                "missing_pinned_snapshots": [],
            }
        ]

    monkeypatch.setattr(verify_poc_gate, "psql_rows", fake_psql_rows)

    gate = verify_poc_gate.check_governed_skill_runs("postgres", "user", "db", run_rows)

    assert gate.name == "governed_skill_runs"
    assert gate.ok is True
    assert gate.evidence == {
        "verified": True,
        "real_task_statuses": {
            "qa-file-reviewer": "succeeded",
            "baoyu-translate": "succeeded",
        },
        "run_skill_snapshots": {
            "row_count": 2,
            "used_count": 2,
            "used_skill_ids": ["qa-file-reviewer", "baoyu-translate"],
            "used_skills_source": "executor_hook",
            "pinned_snapshot_count": 2,
            "pinned_snapshot_source": "release_decision",
            "missing_pinned_snapshots": [],
            "mismatched_pinned_snapshots": [],
        },
    }
    assert "run_review_gate_1" in queries[0]
    assert "run_translate_gate_1" in queries[0]
    assert "r.input_json ? 'release_decision'" in queries[0]


def test_governed_skill_runs_gate_fails_closed_when_pinned_snapshot_is_missing(monkeypatch):
    run_rows = [
        {
            "run_id": "run_review_gate_1",
            "tenant_id": "default",
            "skill_id": "qa-file-reviewer",
            "status": "succeeded",
        }
    ]

    monkeypatch.setattr(
        verify_poc_gate,
        "psql_rows",
        lambda *args, **kwargs: [
            {
                "row_count": 0,
                "used_count": 0,
                "used_skill_ids": [],
                "used_skills_sources": [],
                "pinned_snapshot_count": 0,
                "missing_pinned_snapshots": ["qa-file-reviewer"],
            }
        ],
    )

    gate = verify_poc_gate.check_governed_skill_runs("postgres", "user", "db", run_rows)

    assert gate.ok is False
    assert gate.evidence["verified"] is False
    assert gate.evidence["real_task_statuses"] == {"qa-file-reviewer": "succeeded"}
    assert gate.evidence["run_skill_snapshots"]["missing_pinned_snapshots"] == ["qa-file-reviewer"]


def test_governed_skill_runs_gate_fails_closed_when_snapshot_version_does_not_match_release_decision(
    monkeypatch,
):
    run_rows = [
        {
            "run_id": "run_review_gate_1",
            "tenant_id": "default",
            "skill_id": "qa-file-reviewer",
            "status": "succeeded",
        }
    ]

    monkeypatch.setattr(
        verify_poc_gate,
        "psql_rows",
        lambda *args, **kwargs: [
            {
                "row_count": 1,
                "used_count": 1,
                "used_skill_ids": ["qa-file-reviewer"],
                "used_skills_sources": ["executor_hook"],
                "pinned_snapshot_count": 0,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": ["qa-file-reviewer"],
            }
        ],
    )

    gate = verify_poc_gate.check_governed_skill_runs("postgres", "user", "db", run_rows)

    assert gate.ok is False
    assert gate.evidence["verified"] is False
    assert gate.evidence["run_skill_snapshots"]["mismatched_pinned_snapshots"] == ["qa-file-reviewer"]


def test_context_snapshot_public_projection_gate_requires_safe_explainable_summary(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        calls.append((url, headers))
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_public_1",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 1,
                            "artifact_count": 0,
                            "memory_record_count": 1,
                        },
                        "used_context_summary": {
                            "source": "stored_context_snapshot",
                            "input_keys": ["message", "attachments"],
                            "memory_policy_source": "stored",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.name == "context_snapshot_public_projection"
    assert gate.ok is True
    assert calls[0][0] == "http://api.local/api/ai/runs/run_review_gate_1/context/snapshots"
    assert gate.evidence == {
        "status": 200,
        "ok": True,
        "snapshot_count": 1,
        "referenced_material_counts": {
            "message_count": 1,
            "file_count": 1,
            "artifact_count": 0,
            "memory_record_count": 1,
        },
        "raw_material_id_fields_present": False,
        "forbidden_projection_leaks": [],
        "summary_source": "stored_context_snapshot",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": False,
        "execution_tier": "sdk_only_writing",
        "context_pack_generated_at_present": True,
    }


def test_context_snapshot_public_projection_gate_rejects_counts_only_summary(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_counts_only",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 1,
                            "artifact_count": 1,
                            "memory_record_count": 1,
                        },
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["missing_public_summary_fields"] == [
        "attachments_input_key",
        "context_pack_generated_at",
        "execution_tier",
        "input_keys",
        "long_term_memory_read",
        "memory_policy_source",
        "summary_source",
    ]


def test_context_snapshot_public_projection_gate_requires_attachment_signal_for_file_context(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_missing_attachment_signal",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 1,
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
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["input_keys"] == ["message"]
    assert gate.evidence["missing_public_summary_fields"] == ["attachments_input_key"]


def test_context_snapshot_public_projection_gate_fails_closed_on_malformed_file_count(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_malformed_count",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": "one",
                            "artifact_count": 0,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": ["attachments", "message"],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["referenced_material_counts"]["file_count"] == 0
    assert "file_count" in gate.evidence["invalid_referenced_material_count_fields"]


def test_context_snapshot_public_projection_gate_rejects_non_integer_material_counts(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_non_integer_counts",
                    "payload": {
                        "referenced_materials": {
                            "message_count": True,
                            "file_count": "1",
                            "artifact_count": 1.2,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": ["attachments", "message"],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["referenced_material_counts"] == {
        "message_count": 0,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert gate.evidence["invalid_referenced_material_count_fields"] == [
        "artifact_count",
        "file_count",
        "message_count",
    ]


def test_context_snapshot_public_projection_gate_rejects_unsafe_input_keys(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_unsafe_input_keys",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 1,
                            "artifact_count": 0,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": [
                                "attachments",
                                "fileIds",
                                "raw_storage_key",
                                "absoluteRuntimePaths",
                                "secretLikeValues",
                                "file id",
                                "message",
                            ],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["input_keys"] == ["attachments", "message"]
    assert gate.evidence["unsafe_input_keys"] == [
        "absoluteRuntimePaths",
        "file id",
        "fileIds",
        "raw_storage_key",
        "secretLikeValues",
    ]
    assert gate.evidence["missing_public_summary_fields"] == ["unsafe_input_keys"]


def test_context_snapshot_public_projection_gate_reports_all_snapshot_followups(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_public_ok",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 1,
                            "artifact_count": 0,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": ["attachments", "message"],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                },
                {
                    "context_snapshot_id": "ctx_public_followup",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": "one",
                            "artifact_count": 0,
                            "memory_record_count": 0,
                        },
                        "used_context_summary": {
                            "source": "chat_stream",
                            "input_keys": ["attachments", "includedFileIds", "message"],
                            "memory_policy_source": "default",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                    },
                },
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["invalid_referenced_material_count_fields"] == ["file_count"]
    assert gate.evidence["unsafe_input_keys"] == ["includedFileIds"]
    assert gate.evidence["missing_public_summary_fields"] == ["unsafe_input_keys"]


def test_context_snapshot_public_projection_gate_rejects_raw_id_and_private_leaks(monkeypatch):
    def fake_get(url: str, headers: dict[str, str], timeout: float = 15.0):
        return 200, {
            "run_id": "run_review_gate_1",
            "context_snapshots": [
                {
                    "context_snapshot_id": "ctx_leaky",
                    "payload": {
                        "referenced_materials": {
                            "message_count": 1,
                            "file_count": 1,
                            "artifact_count": 1,
                            "memory_record_count": 1,
                        },
                        "used_context_summary": {
                            "source": "stored_context_snapshot",
                            "input_keys": ["message"],
                            "memory_policy_source": "stored",
                            "long_term_memory_read": False,
                        },
                        "execution_tier": "sdk_only_writing",
                        "context_pack_generated_at": "2026-06-12T01:00:00Z",
                        "includedFileIds": ["file-secret"],
                        "sandbox_workdir": "/tmp/tenant/run",
                    },
                }
            ],
        }

    monkeypatch.setattr(verify_poc_gate, "http_json_get_with_headers", fake_get)

    gate = verify_poc_gate.check_context_snapshot_public_projection(
        "http://api.local",
        {"run_id": "run_review_gate_1", "file_ids": ["file_review_gate_1"], "artifact_count": 1},
        headers=verify_poc_gate.principal_headers("upload-review-gate-user", "Upload Review Gate User"),
    )

    assert gate.ok is False
    assert gate.evidence["raw_material_id_fields_present"] is True
    assert "sandbox_workdir" in gate.evidence["forbidden_projection_leaks"]


def test_word_review_attachment_chat_rejects_playback_without_preview_projection(monkeypatch):
    monkeypatch.setattr(verify_poc_gate, "sample_docx_bytes", lambda: ("review.docx", b"docx-bytes"))
    monkeypatch.setattr(verify_poc_gate.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verify_poc_gate, "http_multipart_file_post", lambda *args, **kwargs: (200, {"key": "file_review_gate_1"}))
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_post_with_headers",
        lambda *args, **kwargs: (200, {"run_id": "run_review_gate_1"}),
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "psql_rows",
        lambda *args, **kwargs: [
            {
                "run_id": "run_review_gate_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "succeeded",
                "file_ids": ["file_review_gate_1"],
                "error_message": None,
                "artifact_count": 1,
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_get_with_headers",
        lambda *args, **kwargs: (
            200,
            {
                "contract_version": "ai-platform.run-playback.v1",
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "download_url": "/api/ai/artifacts/artifact_review_1/download",
                        "preview_url": None,
                    }
                ],
            },
        ),
    )

    gate = verify_poc_gate.check_word_review_attachment_chat("http://api.local", "postgres", "user", "db")

    assert gate.ok is False
    assert gate.evidence["playback"]["preview_url_count"] == 0


def test_word_review_attachment_chat_rejects_preview_on_unrelated_artifact(monkeypatch):
    monkeypatch.setattr(verify_poc_gate, "sample_docx_bytes", lambda: ("review.docx", b"docx-bytes"))
    monkeypatch.setattr(verify_poc_gate.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verify_poc_gate, "http_multipart_file_post", lambda *args, **kwargs: (200, {"key": "file_review_gate_1"}))
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_post_with_headers",
        lambda *args, **kwargs: (200, {"run_id": "run_review_gate_1"}),
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "psql_rows",
        lambda *args, **kwargs: [
            {
                "run_id": "run_review_gate_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "succeeded",
                "file_ids": ["file_review_gate_1"],
                "error_message": None,
                "artifact_count": 2,
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    },
                    {
                        "artifact_id": "artifact_other_1",
                        "artifact_type": "summary_pdf",
                        "content_type": "application/pdf",
                    },
                ],
            }
        ],
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_get_with_headers",
        lambda *args, **kwargs: (
            200,
            {
                "contract_version": "ai-platform.run-playback.v1",
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "download_url": "/api/ai/artifacts/artifact_review_1/download",
                        "preview_url": None,
                    },
                    {
                        "artifact_id": "artifact_other_1",
                        "artifact_type": "summary_pdf",
                        "download_url": "/api/ai/artifacts/artifact_other_1/download",
                        "preview_url": "/api/ai/artifacts/artifact_other_1/preview",
                    },
                ],
            },
        ),
    )

    gate = verify_poc_gate.check_word_review_attachment_chat("http://api.local", "postgres", "user", "db")

    assert gate.ok is False
    assert gate.evidence["playback"]["preview_url_count"] == 1
    assert gate.evidence["playback"]["matched_preview_artifact_count"] == 0


def test_word_review_attachment_chat_rejects_playback_private_payload_leak(monkeypatch):
    monkeypatch.setattr(verify_poc_gate, "sample_docx_bytes", lambda: ("review.docx", b"docx-bytes"))
    monkeypatch.setattr(verify_poc_gate.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verify_poc_gate, "http_multipart_file_post", lambda *args, **kwargs: (200, {"key": "file_review_gate_1"}))
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_post_with_headers",
        lambda *args, **kwargs: (200, {"run_id": "run_review_gate_1"}),
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "psql_rows",
        lambda *args, **kwargs: [
            {
                "run_id": "run_review_gate_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "succeeded",
                "file_ids": ["file_review_gate_1"],
                "error_message": None,
                "artifact_count": 1,
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_get_with_headers",
        lambda *args, **kwargs: (
            200,
            {
                "contract_version": "ai-platform.run-playback.v1",
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "download_url": "/api/ai/artifacts/artifact_review_1/download",
                        "preview_url": "/api/ai/artifacts/artifact_review_1/preview",
                        "storage_key": "tenants/default/private/review.docx",
                    }
                ],
            },
        ),
    )

    gate = verify_poc_gate.check_word_review_attachment_chat("http://api.local", "postgres", "user", "db")

    assert gate.ok is False
    assert gate.evidence["playback"]["private_payload_leaked"] is True


def test_word_review_attachment_chat_rejects_runtime_private_payload_key_leak(monkeypatch):
    monkeypatch.setattr(verify_poc_gate, "sample_docx_bytes", lambda: ("review.docx", b"docx-bytes"))
    monkeypatch.setattr(verify_poc_gate.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verify_poc_gate, "http_multipart_file_post", lambda *args, **kwargs: (200, {"key": "file_review_gate_1"}))
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_post_with_headers",
        lambda *args, **kwargs: (200, {"run_id": "run_review_gate_1"}),
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "psql_rows",
        lambda *args, **kwargs: [
            {
                "run_id": "run_review_gate_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "succeeded",
                "file_ids": ["file_review_gate_1"],
                "error_message": None,
                "artifact_count": 1,
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        verify_poc_gate,
        "http_json_get_with_headers",
        lambda *args, **kwargs: (
            200,
            {
                "contract_version": "ai-platform.run-playback.v1",
                "artifacts": [
                    {
                        "artifact_id": "artifact_review_1",
                        "artifact_type": "reviewed_docx",
                        "download_url": "/api/ai/artifacts/artifact_review_1/download",
                        "preview_url": "/api/ai/artifacts/artifact_review_1/preview",
                        "runtime_private_payload": {"adapter": "hidden"},
                    }
                ],
            },
        ),
    )

    gate = verify_poc_gate.check_word_review_attachment_chat("http://api.local", "postgres", "user", "db")

    assert gate.ok is False
    assert gate.evidence["playback"]["private_payload_leaked"] is True



def test_upload_attachment_chat_reports_worker_runtime_evidence(monkeypatch):
    monkeypatch.setattr(verify_poc_gate.time, "sleep", lambda seconds: None)

    def fake_json_post(url: str, payload=None, headers=None, timeout: float = 15.0):
        if url == "http://api.local/api/upload/check":
            return 200, {"exists": False}
        if url == "http://api.local/api/chat/stream?agent_id=general-agent":
            return 200, {"run_id": "run_upload"}
        raise AssertionError(url)

    def fake_upload(url: str, **kwargs):
        return 200, {
            "key": "file_upload",
            "file_id": "file_upload",
            "name": "upload-gate.txt",
            "mimeType": "text/plain",
            "size": 18,
        }

    def fake_psql_rows(container: str, db_user: str, db_name: str, sql: str):
        assert "worker_events" in sql
        assert "run_events" in sql
        return [
            {
                "run_id": "run_upload",
                "status": "failed",
                "file_ids": ["file_upload"],
                "error_code": "claude_agent_sdk_disabled",
                "error_message": "Claude Agent SDK is required for general chat runs.",
                "executor_type": "claude-agent-worker",
                "worker_events": [
                    {
                        "worker_id": "worker-old",
                        "executor_type": "claude-agent-worker",
                        "claude_agent_sdk_enabled": False,
                        "claude_agent_model": "deepseek-v4-flash",
                        "claude_agent_sdk_import": "ok",
                    }
                ],
            }
        ]

    monkeypatch.setattr(verify_poc_gate, "http_json_post_with_headers", fake_json_post)
    monkeypatch.setattr(verify_poc_gate, "http_multipart_file_post", fake_upload)
    monkeypatch.setattr(verify_poc_gate, "psql_rows", fake_psql_rows)

    gate = verify_poc_gate.check_upload_attachment_chat("http://api.local", "postgres", "user", "db")

    assert gate.ok is False
    assert gate.evidence["run"]["error_code"] == "claude_agent_sdk_disabled"
    assert gate.evidence["run"]["worker_events"][0]["worker_id"] == "worker-old"
    assert gate.evidence["run"]["worker_events"][0]["claude_agent_sdk_enabled"] is False


def test_auth_audit_gate_reports_raw_login_diagnostics(monkeypatch):
    def fake_psql_rows(container: str, db_user: str, db_name: str, sql: str):
        if "payload_json->>'source' = 'company-login'" in sql:
            return [
                {
                    "count": 0,
                    "ordinary_user_count": 0,
                    "admin_user_count": 0,
                    "latest_user_id": None,
                    "latest_payload": None,
                }
            ]
        if "where action = 'auth.login'" in sql:
            return [
                {
                    "all_auth_login_count": 2,
                    "latest_any_user_id": "user001",
                    "latest_any_payload": {"source": "legacy-login"},
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(verify_poc_gate, "psql_rows", fake_psql_rows)

    gate = verify_poc_gate.check_auth_audit("postgres", "user", "db", allow_missing=False)

    assert gate.ok is False
    assert gate.evidence["count"] == 0
    assert gate.evidence["all_auth_login_count"] == 2
    assert gate.evidence["latest_any_user_id"] == "user001"
    assert gate.evidence["missing_requirements"] == [
        "ordinary_company_login_audit",
        "admin_company_login_audit",
    ]


def test_auth_audit_gate_returns_boolean_ok_for_valid_company_login(monkeypatch):
    def fake_psql_rows(container: str, db_user: str, db_name: str, sql: str):
        payload = {
            "source": "company-login",
            "work_id": "ZX2834",
            "permissions": ["agent:use"],
            "is_admin": True,
        }
        if "payload_json->>'source' = 'company-login'" in sql:
            return [
                {
                    "count": 2,
                    "ordinary_user_count": 1,
                    "admin_user_count": 1,
                    "latest_user_id": "ZX2834",
                    "latest_payload": payload,
                }
            ]
        if "where action = 'auth.login'" in sql:
            return [
                {
                    "all_auth_login_count": 2,
                    "latest_any_user_id": "ZX2834",
                    "latest_any_payload": payload,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(verify_poc_gate, "psql_rows", fake_psql_rows)

    gate = verify_poc_gate.check_auth_audit("postgres", "user", "db", allow_missing=False)

    assert gate.ok is True
