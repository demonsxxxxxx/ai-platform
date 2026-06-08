import json
import subprocess
import sys

from tools.frontend_release_traceability import (
    build_frontend_release_traceability,
    render_frontend_release_traceability_markdown,
)


EXPECTED_CI_VERIFY = (
    "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json "
    "&& eslint . && tsc -b && vite build"
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
    assert "python tools/frontend_release_traceability.py --format json" in trace["workflow"]["enforced_commands"]
    assert len(trace["source_hashes"]["package_json_sha256"]) == 64
    assert len(trace["source_hashes"]["pnpm_lock_sha256"]) == 64
    assert trace["dist"]["status"] in {"built", "missing"}
    assert trace["packaged_frontend_image"]["artifact_kind"] == "frontend_static_image"
    assert trace["packaged_frontend_image"]["status"] == "not_configured"
    assert trace["packaged_frontend_image"]["dockerfile"]["path"] == "frontend/web/Dockerfile"
    assert trace["packaged_frontend_image"]["dockerfile"]["status"] == "missing"
    assert trace["packaged_frontend_image"]["compose_overlay"]["path"] == "deploy/ai-platform/docker-compose.frontend.yml"
    assert trace["packaged_frontend_image"]["compose_overlay"]["status"] == "missing"
    assert trace["packaged_frontend_image"]["release_trace"]["backend_worker_commit"] == trace["git"]["commit"]
    assert "packaged_frontend_dockerfile_missing" in trace["packaged_frontend_image"]["blockers"]

    serialized = json.dumps(trace, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "database_url" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert ".env" not in serialized


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
                    "build": "tsc -b && vite build",
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

    trace = build_frontend_release_traceability(repo_root=tmp_path)

    assert trace["dist"]["status"] == "built"
    assert trace["dist"]["artifact_kind"] == "static_dist"
    assert trace["dist"]["file_count"] == 2
    assert trace["dist"]["total_bytes"] > 0
    assert len(trace["dist"]["manifest_sha256"]) == 64
    assert len(trace["dist"]["entrypoints"]["index_html_sha256"]) == 64
    assert trace["dist"]["release_trace"] == {
        "frontend_artifact": "static_dist_manifest",
        "backend_worker_commit": trace["git"]["commit"],
        "policy": "same_git_commit_for_api_worker_frontend_artifacts",
    }
    assert trace["packaged_frontend_image"]["status"] == "not_configured"
    assert "packaged_frontend_image_trace_missing" in trace["packaged_frontend_image"]["blockers"]
    serialized = json.dumps(trace, ensure_ascii=False).lower()
    assert str(tmp_path).lower() not in serialized
    assert "secret" not in serialized


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
    assert payload["packaged_frontend_image"]["status"] == "not_configured"
    assert "c:\\users" not in result.stdout.lower()


def test_render_frontend_release_traceability_markdown_is_operator_readable():
    markdown = render_frontend_release_traceability_markdown(build_frontend_release_traceability())

    assert "# ai-platform Frontend Release Traceability" in markdown
    assert "frontend/web" in markdown
    assert "`corepack pnpm run ci:verify`" in markdown
    assert "frontend_projection_audit.py" in markdown
    assert "package_json_sha256" in markdown
    assert "manifest_sha256" in markdown
    assert "artifact_kind" in markdown
    assert "## Packaged Frontend Image" in markdown
    assert "not_configured" in markdown
    assert "packaged_frontend_dockerfile_missing" in markdown
    assert "ai-platform-frontend.yml" in markdown
    assert "c:\\users" not in markdown.lower()
