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
    violations = audit["forbidden_private_payload_terms"]["violations"]
    assert violations
    violation_terms = {item["term"] for item in violations}
    assert {"api_key", "encrypt_key", "verification_token"}.issubset(violation_terms)
    active_violations = audit["active_browser_entry"]["forbidden_projection_terms"]["violations"]
    assert active_violations == []
    active_paths = set(audit["active_browser_entry"]["files"])
    assert "frontend/web/src/components/panels/channel/feishu/FeishuPanel.tsx" not in active_paths
    assert "frontend/web/src/components/panels/ModelPanel/ModelPanel.tsx" not in active_paths
    assert "frontend/web/src/components/profile/tabs/ProfileEnvVarsTab.tsx" not in active_paths
    quarantined_paths = {
        item["path"] for item in audit["quarantined_legacy_sources"]["violations"]
    }
    assert "frontend/web/src/components/panels/channel/feishu/FeishuPanel.tsx" in quarantined_paths
    assert "frontend/web/src/services/api/model.ts" in quarantined_paths
    assert not any(
        item["path"] == "frontend/web/src/components/documents/documentUrlSafety.ts"
        for item in violations
    )
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
    assert "legacy_route_policies" in audit["route_inventory"]
    assert "route_inventory" in audit["active_browser_entry"]
    active_route_inventory = audit["active_browser_entry"]["route_inventory"]
    assert set(active_route_inventory) == {
        "ai_platform_projection_routes",
        "same_origin_compat_routes",
        "legacy_policy_required_routes",
        "legacy_route_policies",
    }
    active_policy_routes = {
        route["route_prefix"]: route
        for route in active_route_inventory["legacy_route_policies"]
    }
    assert "/api/mcp" in active_policy_routes
    assert "/api/env-vars" in active_policy_routes
    assert "/api/channels" not in active_policy_routes
    assert active_policy_routes["/api/mcp"]["route_scope"] == "active_browser_entry"
    policy_routes = {
        route["route_prefix"]: route
        for route in audit["route_inventory"]["legacy_route_policies"]
    }
    assert set(policy_routes) == {
        route["route_prefix"]
        for route in audit["route_inventory"]["legacy_policy_required_routes"]
    }
    assert policy_routes["/api/agent/models"]["ordinary_user_exposure"] == "fail_closed"
    assert policy_routes["/api/agent/models"]["required_action"] == (
        "remap_to_ai_platform_admin_projection_or_hide"
    )
    assert policy_routes["/api/mcp"]["governance_gate"] == "G6"
    assert "active_legacy_routes_need_policy_enforcement_or_ai_platform_remap" in audit["open_gaps"]
    assert "legacy_routes_need_policy_enforcement_or_ai_platform_remap" in audit["open_gaps"]
    assert "quarantined_legacy_sources_need_ai_platform_projection_remap" in audit["open_gaps"]
    gap_details = {item["gap"]: item for item in audit["open_gap_details"]}
    legacy_detail = gap_details["legacy_routes_need_policy_enforcement_or_ai_platform_remap"]
    assert legacy_detail["count"] == len(audit["route_inventory"]["legacy_route_policies"])
    assert {"G1", "G6", "G9"}.issubset(set(legacy_detail["governance_gates"]))
    assert any(
        route["route_prefix"] == "/api/mcp"
        and route["required_action"] == "remap_to_ai_platform_admin_projection_or_hide"
        for route in legacy_detail["routes"]
    )
    active_detail = gap_details["active_legacy_routes_need_policy_enforcement_or_ai_platform_remap"]
    assert active_detail["count"] == len(active_route_inventory["legacy_route_policies"])
    assert any(route["route_scope"] == "active_browser_entry" for route in active_detail["routes"])
    quarantined_detail = gap_details["quarantined_legacy_sources_need_ai_platform_projection_remap"]
    assert quarantined_detail["count"] == len(audit["quarantined_legacy_sources"]["violations"])
    assert quarantined_detail["sample_violations"]
    assert all("required_action" in item for item in quarantined_detail["sample_violations"])
    assert all("term" not in item for item in quarantined_detail["sample_violations"])
    serialized_gap_details = json.dumps(audit["open_gap_details"], ensure_ascii=False).lower()
    assert "storage_key" not in serialized_gap_details
    assert "executor_private_payload" not in serialized_gap_details
    assert "sandbox_workdir" not in serialized_gap_details

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
    assert audit["active_browser_entry"]["forbidden_projection_terms"]["violations"] == [
        {
            "path": "frontend/web/src/bad.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/bad.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_quarantines_legacy_secret_sources(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text(
        'import "./safe";\nexport const mounted = true;\n',
        encoding="utf-8",
    )
    (source_root / "safe.ts").write_text(
        'export const route = "/api/ai/runs/123/playback";\n',
        encoding="utf-8",
    )
    legacy_dir = source_root / "components" / "panels" / "ModelPanel"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "ModelPanel.tsx").write_text(
        "export function ModelPanel(model: { api_key?: string }) { return model.api_key; }\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "pass_with_policy_gaps"
    assert audit["active_browser_entry"]["forbidden_projection_terms"]["violations"] == []
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/components/panels/ModelPanel/ModelPanel.tsx",
            "line": 1,
            "term": "api_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]
    assert audit["quarantined_legacy_sources"]["violations"] == [
        {
            "path": "frontend/web/src/components/panels/ModelPanel/ModelPanel.tsx",
            "line": 1,
            "term": "api_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]
    assert "quarantined_legacy_sources_need_ai_platform_projection_remap" in audit["open_gaps"]


def test_frontend_projection_audit_follows_re_exported_active_modules(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text(
        'import { leaked } from "./api";\nexport const mounted = leaked;\n',
        encoding="utf-8",
    )
    (source_root / "api.ts").write_text(
        'export { leaked } from "./legacyModel";\n',
        encoding="utf-8",
    )
    (source_root / "legacyModel.ts").write_text(
        "export const leaked = (model: { api_key?: string }) => model.api_key;\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["active_browser_entry"]["forbidden_projection_terms"]["violations"] == [
        {
            "path": "frontend/web/src/legacyModel.ts",
            "line": 1,
            "term": "api_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_does_not_file_allowlist_redaction_paths(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src" / "hooks" / "useAgent"
    source_root.mkdir(parents=True)
    (source_root / "eventProcessor.ts").write_text(
        'const REDACTED_EVENT_KEYS = ["storage_key"];\n'
        "export function leak(payload: any) { return payload.storage_key; }\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["forbidden_private_payload_terms"]["allowed_redaction_refs"] == [
        {
            "path": "frontend/web/src/hooks/useAgent/eventProcessor.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "allowed_redaction_or_url_safety_guard",
        }
    ]
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/hooks/useAgent/eventProcessor.ts",
            "line": 2,
            "term": "storage_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_detects_executor_private_resource_and_skill_source(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "run.ts").write_text(
        "export function leak(step: any) { return step.resource_limits ?? step.used_skills_source; }\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    violation_terms = {item["term"] for item in audit["forbidden_private_payload_terms"]["violations"]}
    assert {"resource_limits", "used_skills_source"}.issubset(violation_terms)


def test_frontend_projection_audit_guard_declaration_does_not_mask_same_line_consumption(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src" / "services" / "api"
    source_root.mkdir(parents=True)
    (source_root / "memory.ts").write_text(
        "const PRIVATE_MEMORY_KEYS = { storage_key: payload.storage_key };\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/services/api/memory.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_quoted_guard_declaration_does_not_mask_same_line_consumption(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src" / "services" / "api"
    source_root.mkdir(parents=True)
    (source_root / "memory.ts").write_text(
        'const PRIVATE_MEMORY_KEYS = new Set(["storage_key", payload.storage_key]);\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/services/api/memory.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_optional_bracket_consumption_is_not_guarded(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src" / "services" / "api"
    source_root.mkdir(parents=True)
    (source_root / "memory.ts").write_text(
        'const PRIVATE_MEMORY_KEYS = new Set(["storage_key", payload?.["storage_key"]]);\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/services/api/memory.ts",
            "line": 1,
            "term": "storage_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_does_not_double_count_nested_guard_terms(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src" / "services" / "api"
    source_root.mkdir(parents=True)
    (source_root / "memory.ts").write_text(
        'const PRIVATE_MEMORY_KEYS = new Set(["executor_private_payload"]);\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "pass"
    assert audit["forbidden_private_payload_terms"]["violations"] == []
    assert audit["forbidden_private_payload_terms"]["allowed_redaction_refs"] == [
        {
            "path": "frontend/web/src/services/api/memory.ts",
            "line": 1,
            "term": "executor_private_payload",
            "reason": "allowed_redaction_or_url_safety_guard",
        }
    ]


def test_frontend_projection_audit_guard_markers_do_not_mask_consumption(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src" / "services" / "api"
    source_root.mkdir(parents=True)
    (source_root / "memory.ts").write_text(
        'const PRIVATE_MEMORY_KEYS = new Set(["private_payload", "verification_token"]);\n'
        "export function leak(payload: any) { return payload.private_payload ?? payload.verification_token; }\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    violations = audit["forbidden_private_payload_terms"]["violations"]
    assert {
        "path": "frontend/web/src/services/api/memory.ts",
        "line": 2,
        "term": "private_payload",
        "reason": "production_code_references_forbidden_projection_term",
    } in violations
    assert {
        "path": "frontend/web/src/services/api/memory.ts",
        "line": 2,
        "term": "verification_token",
        "reason": "production_code_references_forbidden_projection_term",
    } in violations


def test_frontend_projection_audit_detects_secret_like_payload_consumption(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "model.ts").write_text(
        "export function secret(model: { api_key?: string }) { return model.api_key; }\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    assert audit["forbidden_private_payload_terms"]["violations"] == [
        {
            "path": "frontend/web/src/model.ts",
            "line": 1,
            "term": "api_key",
            "reason": "production_code_references_forbidden_projection_term",
        }
    ]


def test_frontend_projection_audit_detects_upper_snake_secret_like_payload_consumption(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "env.ts").write_text(
        "export const leaked = process.env.VERIFICATION_TOKEN || process.env.APP_SECRET || process.env.ENCRYPT_KEY;\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "pnpm run projection:audit && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["status"] == "blocked"
    violation_terms = {item["term"] for item in audit["forbidden_private_payload_terms"]["violations"]}
    assert {"APP_SECRET", "ENCRYPT_KEY", "VERIFICATION_TOKEN"}.issubset(violation_terms)


def test_frontend_projection_audit_requires_ci_verify_to_start_with_projection_audit(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "ok.ts").write_text("export const ok = true;\n", encoding="utf-8")
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "TOKEN=secret eslint . && echo storage_key && pnpm run projection:audit && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["ci_integration"]["ci_verify_includes_projection_audit"] is False
    assert audit["status"] == "blocked"
    assert "frontend_ci_verify_does_not_yet_run_projection_audit" in audit["open_gaps"]
    ci_gap = {
        item["gap"]: item
        for item in audit["open_gap_details"]
    }["frontend_ci_verify_does_not_yet_run_projection_audit"]
    serialized_ci_gap = json.dumps(ci_gap, ensure_ascii=False).lower()
    assert "token=secret" not in serialized_ci_gap
    assert "storage_key" not in serialized_ci_gap
    assert "ci_verify_configured" in ci_gap
    assert "projection_audit_configured" in ci_gap


def test_frontend_projection_audit_accepts_direct_node_launcher_ci_step(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text("export const ok = true;\n", encoding="utf-8")
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["ci_integration"]["ci_verify_includes_projection_audit"] is True
    assert audit["status"] == "pass"


def test_frontend_projection_audit_rejects_ci_step_that_only_mentions_audit_tool(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    source_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text("export const ok = true;\n", encoding="utf-8")
    (tmp_path / "frontend" / "web" / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
                    "ci:verify": "echo frontend_projection_audit.py && eslint . && tsc -b && vite build",
                }
            }
        ),
        encoding="utf-8",
    )

    audit = build_frontend_projection_audit(repo_root=tmp_path)

    assert audit["ci_integration"]["ci_verify_includes_projection_audit"] is False
    assert audit["status"] == "blocked"
    assert "frontend_ci_verify_does_not_yet_run_projection_audit" in audit["open_gaps"]


def test_frontend_projection_audit_cli_outputs_json():
    result = subprocess.run(
        [sys.executable, "tools/frontend_projection_audit.py", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.frontend-projection-audit.v1"
    assert payload["status"] == "pass_with_policy_gaps"
    assert payload["forbidden_private_payload_terms"]["violations"]
    assert payload["active_browser_entry"]["forbidden_projection_terms"]["violations"] == []
    assert "c:\\users" not in result.stdout.lower()


def test_render_frontend_projection_audit_markdown_is_operator_readable():
    markdown = render_frontend_projection_audit_markdown(build_frontend_projection_audit())

    assert "# ai-platform Frontend Projection Audit" in markdown
    assert "pass_with_policy_gaps" in markdown
    assert "/api/ai/admin/" in markdown
    assert "/api/mcp" in markdown
    assert "Legacy Route Policies" in markdown
    assert "Active Browser Entry" in markdown
    assert "Active Legacy Route Policies" in markdown
    assert "Open Gap Details" in markdown
    assert "active_legacy_routes_need_policy_enforcement_or_ai_platform_remap" in markdown
    assert "legacy_routes_need_policy_enforcement_or_ai_platform_remap" in markdown
    assert "c:\\users" not in markdown.lower()
