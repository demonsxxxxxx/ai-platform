import json
import subprocess
import sys

from tools.frontend_projection_audit import (
    build_frontend_projection_audit,
    render_frontend_projection_audit_markdown,
)


def test_frontend_projection_audit_reports_current_public_admin_boundary():
    audit = build_frontend_projection_audit()

    assert audit["schema_version"] == "ai-platform.frontend-projection-audit.v1"
    assert audit["frontend_path"] == "frontend/web"
    assert audit["status"] == "pass_with_policy_gaps"
    assert audit["ci_integration"]["ci_verify_includes_projection_audit"] is True
    assert audit["forbidden_private_payload_terms"]["violations"] == []
    assert "ai_platform_projection_routes" in audit["route_inventory"]
    assert "legacy_policy_required_routes" in audit["route_inventory"]
    assert any(
        route["route_prefix"] == "/api/ai/admin/"
        for route in audit["route_inventory"]["ai_platform_projection_routes"]
    )
    assert any(
        route["route_prefix"] == "/api/mcp"
        for route in audit["route_inventory"]["legacy_policy_required_routes"]
    )
    assert any(
        route["route_prefix"] == "/api/env-vars"
        for route in audit["route_inventory"]["legacy_policy_required_routes"]
    )

    serialized = json.dumps(audit, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "api_key=" not in serialized
    assert ".env" not in serialized


def test_frontend_projection_audit_detects_private_payload_consumption(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "bad.ts").write_text(
        "export function leak(payload: any) { return payload.storage_key; }\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        '{"scripts":{"ci:verify":"python ../../tools/frontend_projection_audit.py --format json"}}',
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/bad.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "production_code_references_executor_private_projection_term",
        }
    ]


def test_frontend_projection_audit_cli_outputs_json():
    result = subprocess.run(
        [sys.executable, "tools/frontend_projection_audit.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.frontend-projection-audit.v1"
    assert payload["forbidden_private_payload_terms"]["violations"] == []
    assert "c:\\users" not in result.stdout.lower()


def test_render_frontend_projection_audit_markdown_is_operator_readable():
    markdown = render_frontend_projection_audit_markdown(build_frontend_projection_audit())

    assert "# ai-platform Frontend Projection Audit" in markdown
    assert "pass_with_policy_gaps" in markdown
    assert "/api/ai/admin/" in markdown
    assert "/api/mcp" in markdown
    assert "c:\\users" not in markdown.lower()
