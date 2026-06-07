import json
import subprocess
import sys

from tools.frontend_release_traceability import (
    build_frontend_release_traceability,
    render_frontend_release_traceability_markdown,
)


def test_frontend_release_traceability_records_ci_contract_without_local_paths():
    trace = build_frontend_release_traceability()

    assert trace["schema_version"] == "ai-platform.frontend-release-traceability.v1"
    assert trace["frontend_path"] == "frontend/web"
    assert trace["package_manager"] == "pnpm@10.32.1"
    assert trace["scripts"]["ci:verify"] == (
        "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json "
        "&& eslint . && tsc -b && vite build"
    )
    assert trace["scripts"]["projection:audit"] == (
        "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json"
    )
    assert trace["commands"] == [
        "corepack pnpm install --frozen-lockfile",
        "corepack pnpm run ci:verify",
    ]
    assert len(trace["source_hashes"]["package_json_sha256"]) == 64
    assert len(trace["source_hashes"]["pnpm_lock_sha256"]) == 64
    assert trace["dist"]["status"] in {"built", "missing"}

    serialized = json.dumps(trace, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "database_url" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert ".env" not in serialized


def test_frontend_release_traceability_cli_outputs_json():
    result = subprocess.run(
        [sys.executable, "tools/frontend_release_traceability.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.frontend-release-traceability.v1"
    assert payload["scripts"]["ci:verify"] == (
        "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json "
        "&& eslint . && tsc -b && vite build"
    )
    assert "corepack pnpm run ci:verify" in payload["commands"]
    assert "c:\\users" not in result.stdout.lower()


def test_render_frontend_release_traceability_markdown_is_operator_readable():
    markdown = render_frontend_release_traceability_markdown(build_frontend_release_traceability())

    assert "# ai-platform Frontend Release Traceability" in markdown
    assert "frontend/web" in markdown
    assert "`corepack pnpm run ci:verify`" in markdown
    assert "frontend_projection_audit.py" in markdown
    assert "package_json_sha256" in markdown
    assert "c:\\users" not in markdown.lower()
