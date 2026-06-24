import json
import subprocess
import sys
import time

from tools.frontend_projection_audit import (
    LEGACY_ROUTE_POLICY_MAP,
    SAFE_PUBLIC_ROUTE_PREFIXES,
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
    assert "api_key" in violation_terms
    active_violations = audit["active_browser_entry"]["forbidden_projection_terms"]["violations"]
    assert active_violations == []
    active_paths = set(audit["active_browser_entry"]["files"])
    assert "frontend/web/src/components/panels/channel/feishu/FeishuPanel.tsx" not in active_paths
    assert "frontend/web/src/components/panels/ModelPanel/ModelPanel.tsx" not in active_paths
    assert "frontend/web/src/components/profile/tabs/ProfileEnvVarsTab.tsx" not in active_paths
    quarantined_paths = {
        item["path"] for item in audit["quarantined_legacy_sources"]["violations"]
    }
    assert "frontend/web/src/services/api/model.ts" in quarantined_paths
    assert not any(
        item["path"] == "frontend/web/src/components/documents/documentUrlSafety.ts"
        for item in violations
    )
    assert "ai_platform_projection_routes" in audit["route_inventory"]
    assert "legacy_policy_required_routes" in audit["route_inventory"]
    assert not (set(SAFE_PUBLIC_ROUTE_PREFIXES) & set(LEGACY_ROUTE_POLICY_MAP))
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
        "safe_public_projection_routes",
        "safe_admin_projection_routes",
        "same_origin_compat_routes",
        "legacy_policy_required_routes",
        "legacy_route_policies",
        "ordinary_user_reachable_legacy_route_policies",
    }
    active_policy_routes = {
        route["route_prefix"]: route
        for route in active_route_inventory["legacy_route_policies"]
    }
    active_safe_routes = {
        route["route_prefix"]
        for route in active_route_inventory["safe_public_projection_routes"]
    }
    assert active_safe_routes == {
        "/api/agent/models/available",
        "/api/channels",
        "/api/feedback",
        "/api/github",
        "/api/marketplace",
        "/api/notifications/active",
        "/api/skills",
        "/api/settings",
        "/api/users",
    }
    active_safe_admin_routes = {
        route["route_prefix"]
        for route in active_route_inventory["safe_admin_projection_routes"]
    }
    assert active_safe_admin_routes == {"/api/notifications/admin"}
    all_safe_routes = {
        route["route_prefix"]
        for route in audit["route_inventory"]["safe_public_projection_routes"]
    }
    assert "/api/channels" in all_safe_routes
    assert "/api/notifications/active" in all_safe_routes
    assert "/api/settings" in all_safe_routes
    assert "/api/users" in all_safe_routes
    all_safe_admin_routes = {
        route["route_prefix"]
        for route in audit["route_inventory"]["safe_admin_projection_routes"]
    }
    assert "/api/notifications/admin" in all_safe_admin_routes
    assert "/api/mcp" in active_policy_routes
    assert "/api/env-vars" not in active_policy_routes
    assert "/api/agent/models" not in active_policy_routes
    assert "/api/github" not in active_policy_routes
    assert "/api/marketplace" not in active_policy_routes
    assert "/api/skills" not in active_policy_routes
    assert "/api/channels" not in active_policy_routes
    assert "/api/notifications/admin" not in active_policy_routes
    assert "/api/settings" not in active_policy_routes
    assert "/api/users" not in active_policy_routes
    assert active_policy_routes["/api/mcp"]["route_scope"] == "active_browser_entry"
    ordinary_routes = {
        route["route_prefix"]: route
        for route in active_route_inventory["ordinary_user_reachable_legacy_route_policies"]
    }
    assert set(ordinary_routes) == {"/api/admin/mcp", "/api/mcp"}
    assert all(
        route["governance_gate"] == "G6" and route["domain"] == "mcp_tool_governance"
        for route in ordinary_routes.values()
    )
    assert active_policy_routes["/api/admin/"]["active_browser_access"] == "permission_gated"
    assert "CHANNEL_ADMIN" in active_policy_routes["/api/admin/"][
        "non_ordinary_required_permissions"
    ]
    permission_gated_routes = {
        route["route_prefix"]: route
        for route in active_route_inventory["legacy_route_policies"]
        if route["active_browser_access"] == "permission_gated"
    }
    assert set(permission_gated_routes).isdisjoint(ordinary_routes)
    assert "ROLE_MANAGE" in permission_gated_routes["/api/roles"][
        "non_ordinary_required_permissions"
    ]
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
    assert "ordinary_user_reachable_legacy_routes_need_policy_enforcement_or_ai_platform_remap" in audit["open_gaps"]
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
    ordinary_detail = gap_details[
        "ordinary_user_reachable_legacy_routes_need_policy_enforcement_or_ai_platform_remap"
    ]
    assert ordinary_detail["count"] == len(ordinary_routes)
    assert {route["route_prefix"] for route in ordinary_detail["routes"]} == set(ordinary_routes)
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


def test_frontend_projection_audit_returns_without_scan_timeout():
    started = time.perf_counter()

    audit = build_frontend_projection_audit()

    assert audit["schema_version"] == "ai-platform.frontend-projection-audit.v1"
    assert time.perf_counter() - started < 25


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


def test_frontend_projection_audit_treats_model_public_routes_as_safe_projection(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    api_root = source_root / "services" / "api"
    api_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text(
        'import { modelPublicApi } from "./services/api/modelPublic";\n'
        "export const models = modelPublicApi.listAvailable;\n",
        encoding="utf-8",
    )
    (api_root / "modelPublic.ts").write_text(
        "export const modelPublicApi = {\n"
        "  listAvailable: () => fetch('/api/agent/models/available'),\n"
        "};\n",
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
    assert audit["route_inventory"]["legacy_policy_required_routes"] == []
    assert audit["active_browser_entry"]["route_inventory"]["legacy_route_policies"] == []


def test_frontend_projection_audit_keeps_requested_api_route_constants(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    api_root = source_root / "services" / "api"
    hooks_root = source_root / "hooks"
    api_root.mkdir(parents=True)
    hooks_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text(
        'import { useMarketplace } from "./hooks/useMarketplace";\n'
        "export const market = useMarketplace;\n",
        encoding="utf-8",
    )
    (hooks_root / "useMarketplace.ts").write_text(
        'import { marketplaceApi } from "../services/api/marketplace";\n'
        "export function useMarketplace() { return marketplaceApi.list; }\n",
        encoding="utf-8",
    )
    (api_root / "marketplace.ts").write_text(
        "const MARKETPLACE_API = `/api/marketplace`;\n"
        "export const marketplaceApi = {\n"
        "  list: () => fetch(`${MARKETPLACE_API}/`),\n"
        "};\n",
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
    active_safe_routes = {
        route["route_prefix"]
        for route in audit["active_browser_entry"]["route_inventory"]["safe_public_projection_routes"]
    }

    assert "/api/marketplace" in active_safe_routes
    assert audit["status"] == "pass"


def test_frontend_projection_audit_tracks_permission_gated_active_legacy_routes(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    services_root = source_root / "services" / "api"
    hooks_root = source_root / "hooks"
    services_root.mkdir(parents=True)
    hooks_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text('import "./App";\n', encoding="utf-8")
    (source_root / "App.tsx").write_text(
        'import { useSkills } from "./hooks/useSkills";\n'
        'import { legacySettingsApi } from "./services/api/settings";\n'
        "function App() {\n"
        "  const canReadSkills = hasPermission(Permission.SKILL_READ);\n"
        "  useSkills({ enabled: canReadSkills });\n"
        "  legacySettingsApi.load();\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    (hooks_root / "useSkills.ts").write_text(
        'import { skillApi } from "../services/api/skill";\n'
        "export function useSkills(options: { enabled?: boolean }) {\n"
        "  if (options.enabled) void skillApi.list();\n"
        "}\n",
        encoding="utf-8",
    )
    (services_root / "skill.ts").write_text(
        "export const skillApi = { list: () => fetch('/api/skills') };\n",
        encoding="utf-8",
    )
    (services_root / "settings.ts").write_text(
        "export const legacySettingsApi = { load: () => fetch('/api/settings') };\n",
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
    active_inventory = audit["active_browser_entry"]["route_inventory"]
    active_routes = {
        route["route_prefix"]: route
        for route in active_inventory["legacy_route_policies"]
    }
    ordinary_routes = {
        route["route_prefix"]: route
        for route in active_inventory["ordinary_user_reachable_legacy_route_policies"]
    }

    active_safe_routes = {
        route["route_prefix"]: route
        for route in active_inventory["safe_public_projection_routes"]
    }
    assert "/api/skills" in active_safe_routes
    assert "/api/skills" not in active_routes
    assert "/api/settings" in active_safe_routes
    assert "/api/settings" not in active_routes
    assert ordinary_routes == {}
    assert "ordinary_user_reachable_legacy_routes_need_policy_enforcement_or_ai_platform_remap" not in audit["open_gaps"]


def test_frontend_projection_audit_treats_skills_as_safe_public_when_permission_gated(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    services_root = source_root / "services" / "api"
    hooks_root = source_root / "hooks"
    services_root.mkdir(parents=True)
    hooks_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text('import "./App";\n', encoding="utf-8")
    (source_root / "App.tsx").write_text(
        'import { useSkills } from "./hooks/useSkills";\n'
        "function App() {\n"
        "  const canReadSkills = hasPermission(Permission.SKILL_READ);\n"
        "  useSkills({ enabled: enableSkills && canReadSkills });\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    (hooks_root / "useSkills.ts").write_text(
        'import { skillApi } from "../services/api/skill";\n'
        "export function useSkills(options: { enabled?: boolean }) {\n"
        "  if (options.enabled) void skillApi.list();\n"
        "}\n",
        encoding="utf-8",
    )
    (services_root / "skill.ts").write_text(
        "export const skillApi = { list: () => fetch('/api/skills') };\n",
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
    active_inventory = audit["active_browser_entry"]["route_inventory"]
    active_routes = {
        route["route_prefix"]: route
        for route in active_inventory["legacy_route_policies"]
    }
    active_safe_routes = {
        route["route_prefix"]: route
        for route in active_inventory["safe_public_projection_routes"]
    }

    assert "/api/skills" not in active_routes
    assert "/api/skills" in active_safe_routes


def test_frontend_projection_audit_treats_baseline_user_permissions_as_ordinary_reachable(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    services_root = source_root / "services" / "api"
    services_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text('import "./App";\n', encoding="utf-8")
    (source_root / "App.tsx").write_text(
        'const MemoryPanel = lazy(() => import("./MemoryPanel"));\n'
        "function App() {\n"
        "  return (\n"
        "    <ProtectedRoute permissions={[Permission.CHAT_READ, Permission.SESSION_READ]}>\n"
        "      <MemoryPanel />\n"
        "    </ProtectedRoute>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )
    (source_root / "MemoryPanel.tsx").write_text(
        'import { memoryApi } from "./services/api/memory";\n'
        "export default function MemoryPanel() { memoryApi.list(); return null; }\n",
        encoding="utf-8",
    )
    (services_root / "memory.ts").write_text(
        "export const memoryApi = { list: () => fetch('/api/memory') };\n",
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
    active_routes = {
        route["route_prefix"]: route
        for route in audit["active_browser_entry"]["route_inventory"]["legacy_route_policies"]
    }
    ordinary_routes = {
        route["route_prefix"]: route
        for route in audit["active_browser_entry"]["route_inventory"][
            "ordinary_user_reachable_legacy_route_policies"
        ]
    }

    assert active_routes["/api/memory"]["required_permissions"] == ["CHAT_READ", "SESSION_READ"]
    assert active_routes["/api/memory"]["active_browser_access"] == "ordinary_user_reachable"
    assert set(ordinary_routes) == {"/api/memory"}


def test_frontend_projection_audit_ignores_route_like_relative_import_specifiers(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    services_root = source_root / "services" / "api"
    services_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text('import "./App";\n', encoding="utf-8")
    (source_root / "App.tsx").write_text(
        'import {\n'
        "  fetchMemoryPolicy,\n"
        '} from "./services/api/memory";\n'
        "function App() {\n"
        "  void fetchMemoryPolicy();\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    (services_root / "memory.ts").write_text(
        "export function fetchMemoryPolicy() {\n"
        "  return fetch('/api/ai/memory/policy');\n"
        "}\n",
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

    assert audit["active_browser_entry"]["route_inventory"]["legacy_route_policies"] == []


def test_frontend_projection_audit_tracks_protected_route_lazy_panel_permissions(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    services_root = source_root / "services" / "api"
    panels_root = source_root / "components" / "panels"
    services_root.mkdir(parents=True)
    panels_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text('import "./App";\n', encoding="utf-8")
    (source_root / "App.tsx").write_text(
        'const SettingsPanel = lazy(() => import("./components/panels/SettingsPanel"));\n'
        "function App() {\n"
        "  return <ProtectedRoute permissions={[Permission.SETTINGS_MANAGE]}><SettingsPanel /></ProtectedRoute>;\n"
        "}\n",
        encoding="utf-8",
    )
    (panels_root / "SettingsPanel.tsx").write_text(
        'import { settingsApi } from "../../services/api/settings";\n'
        "export default function SettingsPanel() { settingsApi.list(); return null; }\n",
        encoding="utf-8",
    )
    (services_root / "settings.ts").write_text(
        "export const settingsApi = { list: () => fetch('/api/settings') };\n",
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
    active_routes = {
        route["route_prefix"]: route
        for route in audit["active_browser_entry"]["route_inventory"]["legacy_route_policies"]
    }

    active_safe_routes = {
        route["route_prefix"]
        for route in audit["active_browser_entry"]["route_inventory"]["safe_public_projection_routes"]
    }
    assert "/api/settings" in active_safe_routes
    assert "/api/settings" not in active_routes
    assert audit["active_browser_entry"]["route_inventory"]["ordinary_user_reachable_legacy_route_policies"] == []


def test_frontend_projection_audit_tracks_protected_active_tab_panel_permissions(tmp_path):
    source_root = tmp_path / "frontend" / "web" / "src"
    app_content_root = source_root / "components" / "layout" / "AppContent"
    panels_root = source_root / "components" / "panels"
    services_root = source_root / "services" / "api"
    app_content_root.mkdir(parents=True)
    panels_root.mkdir(parents=True)
    services_root.mkdir(parents=True)
    (source_root / "main.tsx").write_text('import "./App";\n', encoding="utf-8")
    (source_root / "App.tsx").write_text(
        'import { AppContent } from "./components/layout/AppContent";\n'
        "function SettingsPage() { return <AppContent activeTab=\"settings\" />; }\n"
        "function App() {\n"
        "  return <ProtectedRoute permissions={[Permission.SETTINGS_MANAGE]}><SettingsPage /></ProtectedRoute>;\n"
        "}\n",
        encoding="utf-8",
    )
    (app_content_root / "index.tsx").write_text(
        'import { TabContent } from "./TabContent";\n'
        "export function AppContent({ activeTab }: { activeTab: string }) { return <TabContent activeTab={activeTab} />; }\n",
        encoding="utf-8",
    )
    (app_content_root / "TabContent.tsx").write_text(
        'const SettingsPanel = lazy(() => import("../../panels/SettingsPanel"));\n'
        "const panelMap = { settings: SettingsPanel };\n"
        "export function TabContent({ activeTab }: { activeTab: string }) {\n"
        "  const Panel = panelMap[activeTab];\n"
        "  return <Panel />;\n"
        "}\n",
        encoding="utf-8",
    )
    (panels_root / "SettingsPanel.tsx").write_text(
        'import { settingsApi } from "../../services/api/settings";\n'
        "export default function SettingsPanel() { settingsApi.list(); return null; }\n",
        encoding="utf-8",
    )
    (services_root / "settings.ts").write_text(
        "export const settingsApi = { list: () => fetch('/api/settings') };\n",
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
    active_routes = {
        route["route_prefix"]: route
        for route in audit["active_browser_entry"]["route_inventory"]["legacy_route_policies"]
    }

    active_safe_routes = {
        route["route_prefix"]
        for route in audit["active_browser_entry"]["route_inventory"]["safe_public_projection_routes"]
    }
    assert "/api/settings" in active_safe_routes
    assert "/api/settings" not in active_routes
    assert audit["active_browser_entry"]["route_inventory"]["ordinary_user_reachable_legacy_route_policies"] == []


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
