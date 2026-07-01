import json
import subprocess
import sys

import pytest

from tools.frontend_packaged_runtime_smoke import (
    build_frontend_packaged_runtime_smoke_readiness,
    render_frontend_packaged_runtime_smoke_markdown,
)


COMMIT = "305bc40c6430a5413547c4487b336aa9e174a5a1"


def complete_evidence():
    return {
        "commit_sha": COMMIT,
        "runtime_host": "211",
        "image_tag": "ai-platform-frontend:305bc40-smoke",
        "docker_build": {
            "exit_code": 0,
            "log_tail": "wrote frontend/web/dist/ai-platform-build-provenance.json",
        },
        "image_inspect": {
            "revision": COMMIT,
            "env": [
                "AI_PLATFORM_API_UPSTREAM=http://ai-platform-api:8020",
                "AI_PLATFORM_FRONTEND_MAX_BODY_SIZE=50m",
            ],
        },
        "build_provenance": {
            "schema_version": "ai-platform.frontend-build-provenance.v1",
            "git": {"commit": COMMIT, "dirty": False},
            "source_hashes": {
                "package_json_sha256": "a" * 64,
                "pnpm_lock_sha256": "b" * 64,
            },
        },
        "compose_service": {
            "service": "frontend",
            "container_name": "ai-platform-frontend",
            "host_port": 18001,
            "container_port": 8080,
            "state": "running",
        },
        "runtime_smoke": {
            "network": "ai-platform-phaseb_default",
            "healthz": {"status_code": 200, "body": "ok"},
            "index": {"status_code": 200},
            "api_health": {"status_code": 200, "body": {"status": "ok"}},
            "build_provenance_endpoint": {"status_code": 200},
        },
        "leak_scan": {
            "status": "passed",
            "forbidden_markers": [],
        },
        "cleanup": {
            "container_removed": True,
        },
    }


def test_frontend_packaged_runtime_smoke_accepts_complete_211_evidence_without_closing_other_gates():
    readiness = build_frontend_packaged_runtime_smoke_readiness(complete_evidence())

    assert readiness["schema_version"] == "ai-platform.frontend-packaged-runtime-smoke.v1"
    assert readiness["status"] == "ready_for_operator_review"
    assert readiness["gate"] == "#17 Packaged Frontend Runtime Smoke"
    assert readiness["does_not_close_g6_g9_or_21"] is True
    assert readiness["evidence_contract"]["schema_version"] == (
        "ai-platform.frontend-packaged-runtime-smoke-evidence.v1"
    )
    assert readiness["evidence_contract"]["required_fields"] == [
        "commit_sha",
        "runtime_host",
        "image_tag",
        "docker_build",
        "image_inspect",
        "build_provenance",
        "compose_service",
        "runtime_smoke",
        "leak_scan",
        "cleanup",
    ]
    assert readiness["checks"]["image_revision_matches_commit"] is True
    assert readiness["checks"]["build_provenance_matches_commit"] is True
    assert readiness["checks"]["compose_service_named_frontend"] is True
    assert readiness["checks"]["compose_container_named_ai_platform_frontend"] is True
    assert readiness["checks"]["compose_port_18001_bound"] is True
    assert readiness["checks"]["compose_service_running"] is True
    assert readiness["checks"]["healthz_ok"] is True
    assert readiness["checks"]["api_proxy_ok"] is True
    assert readiness["checks"]["leak_scan_passed"] is True
    assert readiness["checks"]["cleanup_complete"] is True
    assert readiness["blockers"] == []
    assert "packaged_frontend_runtime_smoke" in readiness["closed_evidence_items"]
    assert "211_packaged_frontend_runtime_smoke" in readiness["closed_evidence_items"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert ".env" not in serialized
    assert "database_url" not in serialized
    assert "api_key" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized


def test_frontend_packaged_runtime_smoke_blocks_docker_proxy_or_base_image_pull_failures():
    evidence = {
        "commit_sha": COMMIT,
        "runtime_host": "211",
        "image_tag": "ai-platform-frontend:305bc40-smoke",
        "docker_build": {
            "exit_code": 1,
            "log_tail": (
                "FROM node:22-alpine\n"
                "proxyconnect tcp: dial tcp 10.56.0.224:7897: connect: connection refused"
            ),
        },
    }

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_environment"
    assert "docker_registry_proxy_unreachable" in readiness["blockers"]
    assert "base_image_pull_failed" in readiness["blockers"]
    assert "runtime_smoke" in readiness["missing_evidence_fields"]
    assert readiness["closed_evidence_items"] == []
    assert readiness["does_not_close_g6_g9_or_21"] is True
    assert readiness["formal_frontend_compose_runtime_required"] is True

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "10.56.0.224:7897" not in serialized
    assert "connect: connection refused" not in serialized


def test_frontend_packaged_runtime_smoke_blocks_empty_identity_fields():
    evidence = complete_evidence()
    evidence["runtime_host"] = " "
    evidence["image_tag"] = ""

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_incomplete_runtime_evidence"
    assert "runtime_host" in readiness["missing_evidence_fields"]
    assert "image_tag" in readiness["missing_evidence_fields"]
    assert "missing_runtime_host" in readiness["blockers"]
    assert "missing_image_tag" in readiness["blockers"]
    assert readiness["closed_evidence_items"] == []


def test_frontend_packaged_runtime_smoke_blocks_image_tag_not_bound_to_commit():
    evidence = complete_evidence()
    evidence["image_tag"] = "ai-platform-frontend:wrong-smoke"

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_incomplete_runtime_evidence"
    assert readiness["checks"]["image_tag_matches_commit"] is False
    assert "failed_image_tag_matches_commit" in readiness["blockers"]
    assert readiness["closed_evidence_items"] == []


@pytest.mark.parametrize("bad_commit", ["unknown", "305bc40", "not-a-sha"])
def test_frontend_packaged_runtime_smoke_blocks_invalid_commit_sha(bad_commit):
    evidence = complete_evidence()
    evidence["commit_sha"] = bad_commit
    evidence["image_tag"] = f"ai-platform-frontend:{bad_commit[:7]}-smoke"
    evidence["image_inspect"]["revision"] = bad_commit
    evidence["build_provenance"]["git"]["commit"] = bad_commit

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_incomplete_runtime_evidence"
    assert readiness["checks"]["commit_sha_format_valid"] is False
    assert "failed_commit_sha_format_valid" in readiness["blockers"]
    assert readiness["closed_evidence_items"] == []


def test_frontend_packaged_runtime_smoke_rejects_tag_with_commit_short_as_substring():
    evidence = complete_evidence()
    evidence["image_tag"] = "ai-platform-frontend:not-305bc40-smoke"

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_incomplete_runtime_evidence"
    assert readiness["checks"]["image_tag_matches_commit"] is False
    assert "failed_image_tag_matches_commit" in readiness["blockers"]


def test_frontend_packaged_runtime_smoke_malformed_exit_code_is_fail_closed():
    evidence = complete_evidence()
    evidence["docker_build"]["exit_code"] = "not-int"

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_incomplete_runtime_evidence"
    assert "failed_docker_build_succeeded" in readiness["blockers"]
    assert readiness["checks"]["docker_build_succeeded"] is False
    assert readiness["closed_evidence_items"] == []


def test_frontend_packaged_runtime_smoke_non_211_host_does_not_claim_211_smoke():
    evidence = complete_evidence()
    evidence["runtime_host"] = "docker-lab"

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "ready_for_operator_review"
    assert "packaged_frontend_runtime_smoke" in readiness["closed_evidence_items"]
    assert "docker_lab_packaged_frontend_runtime_smoke" in readiness["closed_evidence_items"]
    assert "211_packaged_frontend_runtime_smoke" not in readiness["closed_evidence_items"]


def test_frontend_packaged_runtime_smoke_operator_commands_cover_required_evidence():
    readiness = build_frontend_packaged_runtime_smoke_readiness()
    commands = "\n".join(readiness["operator_commands"])

    assert "docker image inspect" in commands
    assert "http://127.0.0.1:<smoke_port>/" in commands
    assert "leak_scan" in commands
    assert "forbidden_marker_patterns" in commands
    assert "grep -E -i -q -f <forbidden_marker_patterns>" in commands
    assert "! grep" not in commands
    assert "exit \"$status\"" in commands
    assert "docker ps -a --filter name=ai-platform-frontend-smoke-<commit_short>" in commands
    assert "remaining=\"$(sudo -n docker ps -a" in commands
    assert "&& test -z \"$remaining\"" in commands
    assert "docker compose up -d --no-build frontend" in commands
    assert "docker compose ps frontend" in commands
    assert "http://127.0.0.1:18001/healthz" in commands
    assert "http://127.0.0.1:18001/auth/login" in commands
    assert "http://127.0.0.1:18001/api/ai/health" in commands


def test_frontend_packaged_runtime_smoke_does_not_misclassify_plain_connection_refused():
    evidence = {
        "commit_sha": COMMIT,
        "runtime_host": "211",
        "image_tag": "ai-platform-frontend:305bc40-smoke",
        "docker_build": {
            "exit_code": 1,
            "log_tail": "curl http://127.0.0.1:8080 failed: connection refused",
        },
    }

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)

    assert readiness["status"] == "blocked_environment"
    assert readiness["blockers"] == ["docker_build_failed"]


def test_frontend_packaged_runtime_smoke_missing_evidence_is_fail_closed():
    readiness = build_frontend_packaged_runtime_smoke_readiness()

    assert readiness["status"] == "blocked_missing_runtime_evidence"
    assert readiness["missing_evidence_fields"] == readiness["evidence_contract"]["required_fields"]
    assert readiness["blockers"] == ["packaged_frontend_runtime_smoke_evidence_missing"]
    assert readiness["operator_commands"][0].startswith("sudo -n docker build")
    assert "docker compose" in "\n".join(readiness["operator_commands"]).lower()


def test_frontend_packaged_runtime_smoke_cli_outputs_json_from_evidence(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(complete_evidence()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "tools/frontend_packaged_runtime_smoke.py", "--evidence-json", str(evidence_path), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.frontend-packaged-runtime-smoke.v1"
    assert payload["status"] == "ready_for_operator_review"
    assert "c:\\users" not in result.stdout.lower()


def test_frontend_packaged_runtime_smoke_cli_sanitizes_bad_evidence_json(tmp_path):
    evidence_path = tmp_path / "bad-evidence.json"
    evidence_path.write_text("{", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "tools/frontend_packaged_runtime_smoke.py", "--evidence-json", str(evidence_path), "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "frontend_packaged_runtime_smoke_error: invalid_evidence_json" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(evidence_path) not in result.stderr


def test_render_frontend_packaged_runtime_smoke_markdown_is_operator_readable():
    markdown = render_frontend_packaged_runtime_smoke_markdown(
        build_frontend_packaged_runtime_smoke_readiness(complete_evidence())
    )

    assert "# ai-platform Packaged Frontend Runtime Smoke" in markdown
    assert "ready_for_operator_review" in markdown
    assert "ai-platform.frontend-packaged-runtime-smoke-evidence.v1" in markdown
    assert "211_packaged_frontend_runtime_smoke" in markdown
    assert "docker compose" in markdown.lower()
