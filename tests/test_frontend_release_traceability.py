import json
from pathlib import Path
import subprocess
import sys

from tools.frontend_release_traceability import DIST_BUILD_PROVENANCE_FILENAME
from tools.frontend_release_traceability import (
    build_frontend_release_traceability,
    render_frontend_release_traceability_markdown,
)


EXPECTED_CI_VERIFY = (
    "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json "
    "&& corepack pnpm run test:prd-closure-smoke-source && eslint . && tsc -b && vite build "
    "&& node scripts/write-build-provenance.mjs"
)
EXPECTED_WORKFLOW_PYTEST = (
    "python -m pytest tests/test_deploy_frontend_static.py "
    "tests/test_frontend_release_traceability.py "
    "tests/test_frontend_packaged_runtime_smoke.py "
    "tests/test_frontend_ci_workflow.py "
    "tests/test_runtime_launch_script.py "
    "tests/test_source_authority_docs.py "
    "tests/test_governance_readiness.py "
    "-q --basetemp .pytest-tmp"
)


def test_frontend_release_traceability_records_ci_contract_without_local_paths():
    trace = build_frontend_release_traceability()

    assert trace["schema_version"] == "ai-platform.frontend-release-traceability.v1"
    assert trace["frontend_path"] == "frontend/web"
    assert trace["package_manager"] == "pnpm@10.32.1"
    assert trace["scripts"]["ci:verify"] == EXPECTED_CI_VERIFY
    assert trace["scripts"]["projection:audit"] == (
        "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json"
    )
    assert trace["commands"] == [
        "corepack pnpm install --frozen-lockfile",
        "corepack pnpm run ci:verify",
    ]
    assert trace["workflow"]["path"] == ".github/workflows/ai-platform-frontend.yml"
    assert trace["workflow"]["status"] == "present"
    assert len(trace["workflow"]["sha256"]) == 64
    assert "corepack pnpm run ci:verify" in trace["workflow"]["enforced_commands"]
    assert "python -m pip install pytest" in trace["workflow"]["enforced_commands"]
    assert EXPECTED_WORKFLOW_PYTEST in trace["workflow"]["enforced_commands"]
    assert "python tools/deploy_frontend_static.py --help" in trace["workflow"]["enforced_commands"]
    assert "python tools/frontend_release_traceability.py --format json" in trace["workflow"]["enforced_commands"]
    assert "python tools/frontend_packaged_runtime_smoke.py --format json" in trace["workflow"]["enforced_commands"]
    assert "docs/operations/frontend-static-release-deploy.md" in trace["workflow"]["required_path_filters"]
    assert "docs/operations/ai-platform-gate-status.md" in trace["workflow"]["required_path_filters"]
    assert "docs/operations/ai-platform-governance-readiness.md" in trace["workflow"]["required_path_filters"]
    assert "docs/superpowers/plans/**" in trace["workflow"]["required_path_filters"]
    assert "deploy/ai-platform/.env.example" in trace["workflow"]["required_path_filters"]
    assert "deploy/ai-platform/docker-compose.yml" in trace["workflow"]["required_path_filters"]
    assert "deploy/ai-platform/docker-compose.frontend.yml" in trace["workflow"]["required_path_filters"]
    assert "tests/test_foundation_alpha_readiness.py" in trace["workflow"]["required_path_filters"]
    assert "tests/test_deploy_frontend_static.py" in trace["workflow"]["required_path_filters"]
    assert "tools/deploy_frontend_static.py" in trace["workflow"]["required_path_filters"]
    assert "tools/frontend_packaged_runtime_smoke.py" in trace["workflow"]["required_path_filters"]
    assert trace["workflow"]["missing_path_filters"] == []
    assert len(trace["source_hashes"]["package_json_sha256"]) == 64
    assert len(trace["source_hashes"]["pnpm_lock_sha256"]) == 64
    assert trace["dist"]["status"] in {"built", "built_unverified", "missing"}
    assert trace["dist"]["build_provenance"]["path"] == "dist/ai-platform-build-provenance.json"
    if trace["dist"]["status"] == "built":
        assert trace["dist"]["release_trace"]["verified_same_commit"] is True
    assert trace["packaged_frontend_image"]["artifact_kind"] == "frontend_static_image"
    assert trace["packaged_frontend_image"]["status"] == "configured"
    assert trace["packaged_frontend_image"]["dockerfile"]["path"] == "frontend/web/Dockerfile"
    assert trace["packaged_frontend_image"]["dockerfile"]["status"] == "present"
    assert len(trace["packaged_frontend_image"]["dockerfile"]["sha256"]) == 64
    assert trace["packaged_frontend_image"]["compose_overlay"]["path"] == "deploy/ai-platform/docker-compose.frontend.yml"
    assert trace["packaged_frontend_image"]["compose_overlay"]["status"] == "present"
    assert len(trace["packaged_frontend_image"]["compose_overlay"]["sha256"]) == 64
    assert trace["packaged_frontend_image"]["release_trace"]["backend_worker_commit"] == trace["git"]["commit"]
    assert trace["packaged_frontend_image"]["blockers"] == []
    assert trace["packaged_frontend_image"]["contract_scan"]["status"] == "pass"
    assert trace["packaged_frontend_image"]["contract_scan"]["forbidden_findings"] == []
    assert trace["formal_frontend_runtime"]["artifact_kind"] == "frontend_compose_service"
    assert trace["formal_frontend_runtime"]["status"] == "configured"
    assert trace["formal_frontend_runtime"]["compose"]["path"] == "deploy/ai-platform/docker-compose.yml"
    assert trace["formal_frontend_runtime"]["compose"]["status"] == "present"
    assert trace["formal_frontend_runtime"]["service"] == {
        "name": "frontend",
        "container_name": "ai-platform-frontend",
        "host_port": 18001,
        "container_port": 8080,
        "api_upstream_default": "http://api:8020",
    }
    assert trace["formal_frontend_runtime"]["contract_scan"]["status"] == "pass"
    assert trace["formal_frontend_runtime"]["contract_scan"]["forbidden_findings"] == []
    assert trace["formal_frontend_runtime"]["blockers"] == []

    serialized = json.dumps(trace, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "database_url" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert "deploy/ai-platform/.env.example" in serialized
    assert "deploy/ai-platform/.env\"" not in serialized
    assert "deploy/ai-platform/.env'" not in serialized


def test_frontend_release_traceability_records_static_dist_manifest(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    assets_root = dist_root / "assets"
    assets_root.mkdir(parents=True)
    (frontend_root / "package.json").write_text(
        json.dumps(
            {
                "name": "lamb-agent-frontend",
                "version": "2.3.0",
                "packageManager": "pnpm@10.32.1",
                "scripts": {
                    "lint": "eslint .",
                    "build": "tsc -b && vite build && node scripts/write-build-provenance.mjs",
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": EXPECTED_CI_VERIFY,
                },
            }
        ),
        encoding="utf-8",
    )
    (frontend_root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (dist_root / "index.html").write_text("<script src='/assets/app.js'></script>", encoding="utf-8")
    (assets_root / "app.js").write_text("console.log('release')", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("frontend/web/dist/\n", encoding="utf-8")
    git_commit = initialize_git_repo(tmp_path)
    (dist_root / DIST_BUILD_PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": git_commit, "dirty": False},
                "source_hashes": {
                    "package_json_sha256": trace_package_json_sha256(frontend_root),
                    "pnpm_lock_sha256": trace_pnpm_lock_sha256(frontend_root),
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["dist"]["status"] == "built"
    assert trace["dist"]["artifact_kind"] == "static_dist"
    assert trace["dist"]["file_count"] == 3
    assert trace["dist"]["total_bytes"] > 0
    assert len(trace["dist"]["manifest_sha256"]) == 64
    assert len(trace["dist"]["entrypoints"]["index_html_sha256"]) == 64
    assert trace["dist"]["build_provenance"]["status"] == "verified"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is True
    assert trace["dist"]["release_trace"] == {
        "frontend_artifact": "static_dist_manifest",
        "backend_worker_commit": trace["git"]["commit"],
        "policy": "same_git_commit_for_api_worker_frontend_artifacts",
        "verified_same_commit": True,
    }
    assert trace["packaged_frontend_image"]["status"] == "not_configured"
    assert "packaged_frontend_image_trace_missing" in trace["packaged_frontend_image"]["blockers"]
    serialized = json.dumps(trace, ensure_ascii=False).lower()
    assert str(tmp_path).lower() not in serialized
    assert "secret" not in serialized


def test_frontend_release_traceability_uses_source_marker_when_git_metadata_is_absent(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    dist_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    source_commit = "0d9fd4dbc9645577ea6149aa731b7d3cb7d719b8"
    (tmp_path / ".ai-platform-source-revision").write_text(f"{source_commit}\n", encoding="utf-8")
    (tmp_path / ".ai-platform-source-snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.source-snapshot.v1",
                "source_tree_commit_sha": source_commit,
                "runtime_subject_commit_sha": "2384e19dcac2e39fbcf9c27dc990f5774d391422",
                "source_tree_dirty": False,
                "runtime_affecting_changes_since_runtime_subject": [],
                "runtime_affecting_dirty_paths": [],
            }
        ),
        encoding="utf-8",
    )
    (dist_root / "index.html").write_text("<main>source archive frontend</main>", encoding="utf-8")
    (dist_root / DIST_BUILD_PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": source_commit, "dirty": False},
                "source_hashes": {
                    "package_json_sha256": trace_package_json_sha256(frontend_root),
                    "pnpm_lock_sha256": trace_pnpm_lock_sha256(frontend_root),
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["git"] == {
        "commit": source_commit,
        "dirty": False,
        "source": "source_snapshot_marker",
    }
    assert trace["dist"]["status"] == "built"
    assert trace["dist"]["build_provenance"]["status"] == "verified"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is True
    assert trace["dist"]["release_trace"]["backend_worker_commit"] == source_commit


def test_frontend_release_traceability_fails_closed_for_missing_dist_provenance(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    dist_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    initialize_git_repo(tmp_path)
    (dist_root / "index.html").write_text("<main>stale dist</main>", encoding="utf-8")

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["dist"]["status"] == "built_unverified"
    assert trace["dist"]["build_provenance"]["status"] == "missing"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is False
    assert trace["dist"]["release_trace"]["verified_same_commit"] is False
    assert "dist_build_provenance_missing" in trace["dist"]["blockers"]


def test_frontend_release_traceability_fails_closed_for_stale_dist_commit(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    dist_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    initialize_git_repo(tmp_path)
    (dist_root / "index.html").write_text("<main>stale dist</main>", encoding="utf-8")
    (dist_root / DIST_BUILD_PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": "older-commit", "dirty": False},
                "source_hashes": {
                    "package_json_sha256": trace_package_json_sha256(frontend_root),
                    "pnpm_lock_sha256": trace_pnpm_lock_sha256(frontend_root),
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["dist"]["status"] == "built_unverified"
    assert trace["dist"]["build_provenance"]["status"] == "mismatch"
    assert trace["dist"]["build_provenance"]["build_commit"] == "older-commit"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is False
    assert "dist_build_commit_mismatch" in trace["dist"]["blockers"]
    assert trace["dist"]["artifact_scope"] == "ignored_local_build_output"
    assert trace["dist"]["remediation_commands"] == [
        "cd frontend/web",
        "corepack pnpm run ci:verify",
        "python ../../tools/frontend_release_traceability.py --format json",
    ]


def test_frontend_release_traceability_fails_closed_for_unknown_dist_commit(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    dist_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    (dist_root / "index.html").write_text("<main>unknown dist</main>", encoding="utf-8")
    (dist_root / DIST_BUILD_PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": "unknown", "dirty": False},
                "source_hashes": {
                    "package_json_sha256": trace_package_json_sha256(frontend_root),
                    "pnpm_lock_sha256": trace_pnpm_lock_sha256(frontend_root),
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["dist"]["status"] == "built_unverified"
    assert trace["dist"]["build_provenance"]["status"] == "mismatch"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is False
    assert "dist_build_commit_unknown" in trace["dist"]["blockers"]


def test_frontend_release_traceability_fails_closed_for_unknown_dirty_state(tmp_path, dirty_value):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    dist_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    git_commit = initialize_git_repo(tmp_path)
    (dist_root / "index.html").write_text("<main>unknown dirty state</main>", encoding="utf-8")
    (dist_root / DIST_BUILD_PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": git_commit, **dirty_value},
                "source_hashes": {
                    "package_json_sha256": trace_package_json_sha256(frontend_root),
                    "pnpm_lock_sha256": trace_pnpm_lock_sha256(frontend_root),
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["dist"]["status"] == "built_unverified"
    assert trace["dist"]["build_provenance"]["status"] == "mismatch"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is False
    assert "dist_build_dirty_state_unknown" in trace["dist"]["blockers"]


def test_frontend_release_traceability_fails_closed_when_current_source_is_dirty(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    dist_root = frontend_root / "dist"
    dist_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    git_commit = initialize_git_repo(tmp_path)
    (frontend_root / "dirty-source.ts").write_text("export const dirty = true;\n", encoding="utf-8")
    (dist_root / "index.html").write_text("<main>dirty source dist</main>", encoding="utf-8")
    (dist_root / DIST_BUILD_PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": git_commit, "dirty": False},
                "source_hashes": {
                    "package_json_sha256": trace_package_json_sha256(frontend_root),
                    "pnpm_lock_sha256": trace_pnpm_lock_sha256(frontend_root),
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["git"]["dirty"] is True
    assert trace["dist"]["status"] == "built_unverified"
    assert trace["dist"]["build_provenance"]["verified_same_commit"] is False
    assert "source_tree_dirty" in trace["dist"]["blockers"]


def test_frontend_release_traceability_cli_outputs_json():
    result = subprocess.run(
        [sys.executable, "tools/frontend_release_traceability.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.frontend-release-traceability.v1"
    assert payload["scripts"]["ci:verify"] == EXPECTED_CI_VERIFY
    assert "corepack pnpm run ci:verify" in payload["commands"]
    assert payload["packaged_frontend_image"]["status"] == "configured"
    assert payload["packaged_frontend_image"]["blockers"] == []
    assert payload["packaged_frontend_image"]["contract_scan"]["status"] == "pass"
    assert "c:\\users" not in result.stdout.lower()


def test_render_frontend_release_traceability_markdown_is_operator_readable():
    markdown = render_frontend_release_traceability_markdown(build_frontend_release_traceability())

    assert "# ai-platform Frontend Release Traceability" in markdown
    assert "frontend/web" in markdown
    assert "`corepack pnpm run ci:verify`" in markdown
    assert "frontend_projection_audit.py" in markdown
    assert "package_json_sha256" in markdown
    assert "manifest_sha256" in markdown
    assert "Build provenance" in markdown
    assert "verified_same_commit" in markdown
    assert "artifact_kind" in markdown
    assert "## Packaged Frontend Image" in markdown
    assert "- status: `configured`" in markdown
    assert "artifact_scope" in markdown
    assert "corepack pnpm run ci:verify" in markdown
    assert "ai-platform-frontend.yml" in markdown
    assert "c:\\users" not in markdown.lower()


def test_frontend_release_traceability_flags_workflow_missing_enforced_commands(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    write_frontend_package(frontend_root)
    workflow_root = tmp_path / ".github" / "workflows"
    workflow_root.mkdir(parents=True)
    (workflow_root / "ai-platform-frontend.yml").write_text("name: frontend\n", encoding="utf-8")

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["workflow"]["status"] == "present_with_policy_gaps"
    assert trace["workflow"]["blockers"] == [
        "frontend_workflow_enforced_commands_missing",
        "frontend_workflow_path_filters_missing",
    ]
    assert trace["workflow"]["missing_commands"] == [
        "corepack pnpm install --frozen-lockfile",
        "python -m pip install pytest",
        EXPECTED_WORKFLOW_PYTEST,
        "corepack pnpm run ci:verify",
        "python tools/frontend_release_traceability.py --format json",
        "python tools/deploy_frontend_static.py --help",
        "python tools/frontend_packaged_runtime_smoke.py --format json",
        "docker build",
        "--build-arg AI_PLATFORM_BUILD_COMMIT=${{ github.sha }}",
        "--build-arg AI_PLATFORM_BUILD_DIRTY=false",
        "-f frontend/web/Dockerfile",
        "docker run --rm --entrypoint cat",
        "ai-platform-build-provenance.json",
    ]
    assert "docs/operations/frontend-static-release-deploy.md" in trace["workflow"]["missing_path_filters"]
    assert "deploy/ai-platform/docker-compose.yml" in trace["workflow"]["missing_path_filters"]
    assert "deploy/ai-platform/docker-compose.frontend.yml" in trace["workflow"]["missing_path_filters"]
    assert "tests/test_deploy_frontend_static.py" in trace["workflow"]["missing_path_filters"]
    assert "tools/deploy_frontend_static.py" in trace["workflow"]["missing_path_filters"]
    assert "tools/frontend_packaged_runtime_smoke.py" in trace["workflow"]["missing_path_filters"]


def test_frontend_packaged_image_files_define_static_proxy_contract():
    dockerfile = Path("frontend/web/Dockerfile").read_text(encoding="utf-8")
    npmrc = Path("frontend/web/.npmrc").read_text(encoding="utf-8")
    nginx_template = Path("frontend/web/nginx.conf.template").read_text(encoding="utf-8")
    compose_overlay = Path("deploy/ai-platform/docker-compose.frontend.yml").read_text(encoding="utf-8")
    runtime_compose = Path("deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")
    provenance_script = Path("frontend/web/scripts/write-build-provenance.mjs").read_text(encoding="utf-8")

    assert "FROM node:22-bookworm AS build" in dockerfile
    assert "apk add" not in dockerfile
    assert "ARG AI_PLATFORM_BUILD_COMMIT=unknown" in dockerfile
    assert "ENV AI_PLATFORM_BUILD_COMMIT=${AI_PLATFORM_BUILD_COMMIT}" in dockerfile
    assert "org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT" in dockerfile
    assert "ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT" in dockerfile
    assert "corepack pnpm run ci:verify" in dockerfile
    assert "COPY tools ./tools" in dockerfile
    assert "COPY --from=build /workspace/frontend/web/dist" in dockerfile
    assert "nginx.conf.template" in dockerfile
    assert "package-import-method=copy" in npmrc
    assert "AI_PLATFORM_BUILD_COMMIT" in provenance_script
    assert "AI_PLATFORM_BUILD_DIRTY" in provenance_script
    assert "AI_PLATFORM_API_UPSTREAM" in nginx_template
    assert "client_max_body_size ${AI_PLATFORM_FRONTEND_MAX_BODY_SIZE}" in nginx_template
    assert "proxy_pass ${AI_PLATFORM_API_UPSTREAM}" in nginx_template
    assert "proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT}" in nginx_template
    assert "proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT}" in nginx_template
    assert "proxy_request_buffering off" in nginx_template
    assert "try_files $uri $uri/ /index.html" in nginx_template
    assert "dockerfile: frontend/web/Dockerfile" in compose_overlay
    assert "AI_PLATFORM_BUILD_COMMIT" in compose_overlay
    assert "AI_PLATFORM_API_UPSTREAM" in compose_overlay
    assert "AI_PLATFORM_FRONTEND_MAX_BODY_SIZE" in compose_overlay
    assert "  frontend:" in runtime_compose
    assert "container_name: ai-platform-frontend" in runtime_compose
    assert "dockerfile: frontend/web/Dockerfile" in runtime_compose
    assert "${AI_PLATFORM_FRONTEND_PORT:-18001}:8080" in runtime_compose
    assert "AI_PLATFORM_API_UPSTREAM: ${AI_PLATFORM_API_UPSTREAM:-http://api:8020}" in runtime_compose
    assert "AI_PLATFORM_BUILD_COMMIT" in runtime_compose
    assert "AI_PLATFORM_BUILD_DIRTY" in runtime_compose
    assert "POSTGRES_PASSWORD" not in compose_overlay
    assert "OPENAI_API_KEY" not in compose_overlay
    assert "SANDBOX_CALLBACK_TOKEN" not in compose_overlay
    assert missing_plain_dockerfile_copy_sources(dockerfile) == []


def test_frontend_release_traceability_flags_packaged_delivery_missing_required_contract(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    deploy_root = tmp_path / "deploy" / "ai-platform"
    frontend_root.mkdir(parents=True)
    deploy_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    (frontend_root / "Dockerfile").write_text(
        "FROM node:22-alpine AS build\nRUN corepack pnpm run ci:verify\nFROM nginx:1.27-alpine\n",
        encoding="utf-8",
    )
    (frontend_root / "nginx.conf.template").write_text(
        "server { location /api/ { proxy_pass ${AI_PLATFORM_API_UPSTREAM}; } }\n",
        encoding="utf-8",
    )
    (deploy_root / "docker-compose.frontend.yml").write_text(
        "services:\n  frontend:\n    build:\n      dockerfile: frontend/web/Dockerfile\n",
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["packaged_frontend_image"]["status"] == "configured_with_policy_gaps"
    assert "packaged_frontend_contract_scan_failed" in trace["packaged_frontend_image"]["blockers"]
    contract_findings = trace["packaged_frontend_image"]["contract_scan"]["contract_findings"]
    assert {"path": "frontend/web/Dockerfile", "rule_id": "dockerfile_build_commit_arg_required"} in contract_findings
    assert {"path": "frontend/web/Dockerfile", "rule_id": "dockerfile_debian_build_stage_required"} in contract_findings
    assert {"path": "frontend/web/Dockerfile", "rule_id": "dockerfile_build_dirty_arg_required"} in contract_findings
    assert {"path": "frontend/web/Dockerfile", "rule_id": "dockerfile_source_revision_label_required"} in contract_findings
    assert {"path": "frontend/web/Dockerfile", "rule_id": "dockerfile_build_dirty_env_required"} in contract_findings
    assert {"path": "frontend/web/nginx.conf.template", "rule_id": "nginx_upload_body_size_required"} in contract_findings
    assert {"path": "frontend/web/nginx.conf.template", "rule_id": "nginx_proxy_timeouts_required"} in contract_findings
    assert {"path": "frontend/web/nginx.conf.template", "rule_id": "nginx_proxy_request_buffering_off_required"} in contract_findings
    assert {"path": "deploy/ai-platform/docker-compose.frontend.yml", "rule_id": "compose_build_commit_args_required"} in contract_findings
    assert {"path": "deploy/ai-platform/docker-compose.frontend.yml", "rule_id": "compose_frontend_proxy_limits_required"} in contract_findings


def test_frontend_release_traceability_scans_packaged_delivery_files_for_forbidden_terms(tmp_path):
    frontend_root = tmp_path / "frontend" / "web"
    deploy_root = tmp_path / "deploy" / "ai-platform"
    frontend_root.mkdir(parents=True)
    deploy_root.mkdir(parents=True)
    write_frontend_package(frontend_root)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ai-platform-frontend.yml").write_text("name: frontend\n", encoding="utf-8")
    (frontend_root / "Dockerfile").write_text("FROM nginx:1.27-alpine\nENV openai_api_key=bad\n", encoding="utf-8")
    (frontend_root / "nginx.conf.template").write_text("server { location /api/ { proxy_pass http://api:8020; } }\n", encoding="utf-8")
    (deploy_root / "docker-compose.frontend.yml").write_text(
        "services:\n  frontend:\n    env_file: .env\n",
        encoding="utf-8",
    )

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["packaged_frontend_image"]["status"] == "configured_with_policy_gaps"
    assert "packaged_frontend_contract_scan_failed" in trace["packaged_frontend_image"]["blockers"]
    findings = trace["packaged_frontend_image"]["contract_scan"]["forbidden_findings"]
    assert {"path": "frontend/web/Dockerfile", "term": "OPENAI_API_KEY"} in findings
    assert {"path": "deploy/ai-platform/docker-compose.frontend.yml", "term": "env_file"} in findings


def write_frontend_package(frontend_root):
    frontend_root.mkdir(parents=True, exist_ok=True)
    (frontend_root / "package.json").write_text(
        json.dumps(
            {
                "name": "lamb-agent-frontend",
                "version": "2.3.0",
                "packageManager": "pnpm@10.32.1",
                "scripts": {
                    "lint": "eslint .",
                    "build": "tsc -b && vite build && node scripts/write-build-provenance.mjs",
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": EXPECTED_CI_VERIFY,
                },
            }
        ),
        encoding="utf-8",
    )
    (frontend_root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")


def trace_package_json_sha256(frontend_root):
    import hashlib

    return hashlib.sha256((frontend_root / "package.json").read_bytes()).hexdigest()


def trace_pnpm_lock_sha256(frontend_root):
    import hashlib

    return hashlib.sha256((frontend_root / "pnpm-lock.yaml").read_bytes()).hexdigest()


def missing_plain_dockerfile_copy_sources(dockerfile: str, repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(".")
    missing_sources: list[str] = []
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        tokens = line.split()
        for source in tokens[1:-1]:
            if source.startswith("--"):
                continue
            if not (root / source).exists():
                missing_sources.append(source)
    return missing_sources


def initialize_git_repo(repo_root):
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    add_paths = ["frontend/web/package.json", "frontend/web/pnpm-lock.yaml"]
    if (repo_root / ".gitignore").exists():
        add_paths.append(".gitignore")
    subprocess.run(["git", "add", *add_paths], cwd=repo_root, check=True)
    return commit_all(repo_root, message="test frontend release traceability")


def commit_all(repo_root, *, message):
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Traceability Test",
            "-c",
            "user.email=traceability.test@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def pytest_generate_tests(metafunc):
    if "dirty_value" in metafunc.fixturenames:
        metafunc.parametrize(
            "dirty_value",
            [
                {"dirty": None},
                {},
                {"dirty": "unknown"},
            ],
        )
