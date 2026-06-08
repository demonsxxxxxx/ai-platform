import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA_VERSION = "ai-platform.frontend-projection-audit.v1"
FRONTEND_PATH = Path("frontend/web")
SOURCE_ROOT = FRONTEND_PATH / "src"
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}

FORBIDDEN_PRIVATE_TERMS = [
    ".claude",
    "command_sha256",
    "commandSha256",
    "decision_payload",
    "decisionPayload",
    "executorPrivatePayload",
    "executor_private_payload",
    "fingerprint",
    "private_payload",
    "privatePayload",
    "raw_payload",
    "rawPayload",
    "request_payload",
    "requestPayload",
    "resource_limits",
    "resourceLimits",
    "runtime_path",
    "runtimePath",
    "sandbox_workspace_root",
    "sandbox_workdir",
    "sandboxWorkdir",
    "storage_key",
    "storageKey",
    "used_skills_source",
    "usedSkillsSource",
    "work_dir",
    "workDir",
]
FORBIDDEN_SECRET_LIKE_TERMS = [
    "API_KEY",
    "APP_SECRET",
    "BEARER_TOKEN",
    "CLIENT_SECRET",
    "ENCRYPT_KEY",
    "VERIFICATION_TOKEN",
    "api_key",
    "apiKey",
    "app_secret",
    "appSecret",
    "bearer_token",
    "bearerToken",
    "client_secret",
    "clientSecret",
    "encrypt_key",
    "encryptKey",
    "verification_token",
    "verificationToken",
]
FORBIDDEN_PROJECTION_TERMS = sorted(
    set(FORBIDDEN_PRIVATE_TERMS + FORBIDDEN_SECRET_LIKE_TERMS),
    key=lambda item: (-len(item), item),
)

REDACTION_GUARD_PATHS = {
    "frontend/web/src/components/documents/documentUrlSafety.ts",
    "frontend/web/src/hooks/useAgent/eventProcessor.ts",
    "frontend/web/src/services/api/memory.ts",
    "frontend/web/src/services/api/runPlayback.ts",
}
TYPE_ONLY_ALLOWLIST = {
    "frontend/web/src/hooks/useAgent/types.ts",
}
REDACTION_GUARD_DECLARATION = re.compile(
    r"\b(?:const|let|var)\s+"
    r"[A-Z0-9_]*(?:DANGEROUS|FRAGMENT|KEY|KEYS|MARKER|MARKERS|PATTERN|PATTERNS|"
    r"PRIVATE|REDACT|REDACTED|SENSITIVE|TOKEN|TOKENS|UNSAFE)[A-Z0-9_]*\b"
)

AI_PLATFORM_ROUTE_PREFIXES = [
    "/api/ai/admin/",
    "/api/ai/artifacts/",
    "/api/ai/memory/",
    "/api/ai/runs/",
]
COMPAT_ROUTE_PREFIXES = [
    "/api/auth",
    "/api/chat",
    "/api/files/revealed",
    "/api/sessions",
    "/api/upload",
]
LEGACY_POLICY_REQUIRED_ROUTE_PREFIXES = [
    "/api/admin/",
    "/api/admin/mcp",
    "/api/agent/config",
    "/api/agent/models",
    "/api/channels",
    "/api/env-vars",
    "/api/github",
    "/api/marketplace",
    "/api/mcp",
    "/api/memory",
    "/api/notifications/admin",
    "/api/persona-presets",
    "/api/roles",
    "/api/settings",
    "/api/skills",
    "/api/users",
]
LEGACY_ROUTE_POLICY_MAP: dict[str, dict[str, str]] = {
    "/api/admin/": {
        "domain": "admin_operations",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/admin/mcp": {
        "domain": "mcp_tool_governance",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_policy_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/agent/config": {
        "domain": "agent_configuration",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/agent/models": {
        "domain": "model_gateway_secret_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_masked_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/channels": {
        "domain": "channel_secret_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_masked_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/env-vars": {
        "domain": "runtime_secret_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_masked_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/github": {
        "domain": "skill_package_source_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/marketplace": {
        "domain": "skill_catalog_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed_until_public_projection_exists",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_public_or_admin_projection",
    },
    "/api/mcp": {
        "domain": "mcp_tool_governance",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_policy_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/memory": {
        "domain": "memory_governance",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed_until_ai_platform_memory_projection",
        "admin_exposure": "same_tenant_admin_memory_projection_only",
        "required_action": "remap_to_ai_platform_public_or_admin_projection",
    },
    "/api/notifications/admin": {
        "domain": "admin_notifications",
        "governance_gate": "G9",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/persona-presets": {
        "domain": "agent_frontend_profile_policy",
        "governance_gate": "G9",
        "ordinary_user_exposure": "fail_closed_until_public_projection_exists",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_public_or_admin_projection",
    },
    "/api/roles": {
        "domain": "rbac_policy",
        "governance_gate": "G1",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/settings": {
        "domain": "runtime_settings_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed_until_public_projection_exists",
        "admin_exposure": "same_tenant_admin_masked_projection_only",
        "required_action": "remap_to_ai_platform_public_or_admin_projection",
    },
    "/api/skills": {
        "domain": "skill_governance",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed_until_public_catalog_projection_exists",
        "admin_exposure": "same_tenant_admin_skill_projection_only",
        "required_action": "remap_to_ai_platform_public_or_admin_projection",
    },
    "/api/users": {
        "domain": "rbac_user_directory_policy",
        "governance_gate": "G1",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
}

IMPORT_FROM_SPECIFIER = re.compile(
    r"""\bimport\s+(?P<type_only>type\s+)?(?P<clause>[^'";]+?)\s+from\s+["'](?P<specifier>[^"']+)["']""",
    re.DOTALL,
)
SIDE_EFFECT_IMPORT_SPECIFIER = re.compile(r"""\bimport\s+["'](?P<specifier>[^"']+)["']""")
DYNAMIC_IMPORT_SPECIFIER = re.compile(r"""\bimport\s*\(\s*["'](?P<specifier>[^"']+)["']""")
EXPORT_FROM_SPECIFIER = re.compile(
    r"""\bexport\s+(?P<type_only>type\s+)?(?P<clause>[^'";]+?)\s+from\s+["'](?P<specifier>[^"']+)["']""",
    re.DOTALL,
)
ACTIVE_ENTRY_CANDIDATES = [
    SOURCE_ROOT / "main.tsx",
    SOURCE_ROOT / "main.ts",
    SOURCE_ROOT / "App.tsx",
    SOURCE_ROOT / "App.ts",
]


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _production_source_files(root: Path) -> list[Path]:
    source_root = root / SOURCE_ROOT
    if not source_root.exists():
        return []
    files: list[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        parts = set(path.parts)
        if "__tests__" in parts:
            continue
        name = path.name
        if ".test." in name or ".spec." in name:
            continue
        files.append(path)
    return sorted(files)


def _resolve_relative_module(path: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    base = (path.parent / specifier).resolve()
    if base.is_file() and base.suffix in SOURCE_SUFFIXES:
        return base
    for suffix in SOURCE_SUFFIXES:
        candidate = base.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    if base.is_dir():
        for name in ("index.tsx", "index.ts", "index.jsx", "index.js"):
            candidate = base / name
            if candidate.is_file():
                return candidate
    return None


def _exported_names_from_named_clause(clause: str) -> set[str]:
    names: set[str] = set()
    for raw_part in clause.strip("{} \n\t").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("type "):
            part = part.removeprefix("type ").strip()
        if " as " in part:
            names.add(part.rsplit(" as ", 1)[1].strip())
        else:
            names.add(part)
    return {name for name in names if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name)}


def _requested_symbols_from_import_clause(clause: str) -> set[str] | None:
    stripped = clause.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return _exported_names_from_named_clause(stripped)
    return None


def _exported_symbols_from_export_clause(clause: str) -> set[str] | None:
    stripped = clause.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return _exported_names_from_named_clause(stripped)
    namespace_export = re.match(r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)$", stripped)
    if namespace_export:
        return {namespace_export.group(1)}
    if stripped.startswith("*"):
        return None
    return None


def _merge_requested_symbols(
    previous: set[str] | None | object,
    incoming: set[str] | None,
) -> set[str] | None:
    if previous is _UNVISITED:
        return incoming
    if previous is None or incoming is None:
        return None
    return set(previous) | set(incoming)


def _should_process_with_request(
    previous: set[str] | None | object,
    incoming: set[str] | None,
) -> bool:
    if previous is _UNVISITED:
        return True
    if previous is None:
        return False
    if incoming is None:
        return True
    return not incoming.issubset(previous)


def _export_satisfies_request(exported: set[str] | None, requested: set[str] | None) -> bool:
    if requested is None or exported is None:
        return True
    return bool(exported & requested)


def _dependency_edges(path: Path, text: str, requested: set[str] | None) -> list[tuple[Path, set[str] | None]]:
    edges: list[tuple[Path, set[str] | None]] = []
    for match in SIDE_EFFECT_IMPORT_SPECIFIER.finditer(text):
        resolved = _resolve_relative_module(path, match.group("specifier"))
        if resolved is not None:
            edges.append((resolved, None))
    for match in DYNAMIC_IMPORT_SPECIFIER.finditer(text):
        resolved = _resolve_relative_module(path, match.group("specifier"))
        if resolved is not None:
            edges.append((resolved, None))
    for match in IMPORT_FROM_SPECIFIER.finditer(text):
        if match.group("type_only"):
            continue
        resolved = _resolve_relative_module(path, match.group("specifier"))
        if resolved is None:
            continue
        edges.append((resolved, _requested_symbols_from_import_clause(match.group("clause"))))
    for match in EXPORT_FROM_SPECIFIER.finditer(text):
        if match.group("type_only"):
            continue
        exported = _exported_symbols_from_export_clause(match.group("clause"))
        if not _export_satisfies_request(exported, requested):
            continue
        resolved = _resolve_relative_module(path, match.group("specifier"))
        if resolved is not None:
            edges.append((resolved, requested if exported is None else exported & requested if requested else None))
    return edges


_UNVISITED = object()


def _active_browser_source_files(root: Path, production_files: list[Path]) -> list[Path]:
    source_root = root / SOURCE_ROOT
    production_set = {path.resolve() for path in production_files}
    entries = [
        (root / candidate).resolve()
        for candidate in ACTIVE_ENTRY_CANDIDATES
        if (root / candidate).is_file()
    ]
    if not entries and source_root.exists():
        return sorted(production_set)

    active: set[Path] = set()
    requested_by_path: dict[Path, set[str] | None] = {}
    stack: list[tuple[Path, set[str] | None]] = [(entry, None) for entry in entries]
    while stack:
        path, requested = stack.pop()
        if path not in production_set:
            continue
        previous = requested_by_path.get(path, _UNVISITED)
        if not _should_process_with_request(previous, requested):
            continue
        requested_by_path[path] = _merge_requested_symbols(previous, requested)
        active.add(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for resolved, next_requested in _dependency_edges(path, text, requested_by_path[path]):
            stack.append((resolved, next_requested))
    return sorted(active)


def _line_refs(root: Path, path: Path, line_number: int, term: str, reason: str) -> dict[str, object]:
    return {
        "path": _relative_path(root, path),
        "line": line_number,
        "term": term,
        "reason": reason,
    }


def _quoted_term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    return re.compile(rf"""["']{escaped}["']""")


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if re.fullmatch(r"[A-Za-z0-9_]+", term):
        return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])")
    return re.compile(escaped)


def _line_has_term(line: str, term: str) -> bool:
    return bool(_term_pattern(term).search(line))


def _line_has_forbidden_consumption(line: str, term: str) -> bool:
    escaped = re.escape(term)
    dot_access = re.compile(rf"\.\s*{escaped}(?![A-Za-z0-9_])")
    bracket_access = re.compile(
        rf"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\s*"
        rf"(?:\?\.)?\[\s*['\"]{escaped}['\"]\s*\]"
    )
    return bool(dot_access.search(line) or bracket_access.search(line))


def _is_redaction_guard_line(relative: str, line: str, term: str) -> bool:
    if relative not in REDACTION_GUARD_PATHS:
        return False
    stripped = line.strip()
    if re.fullmatch(rf"""["']{re.escape(term)}["'],?""", stripped):
        return True
    if _line_has_forbidden_consumption(line, term):
        return False
    quoted_term = _quoted_term_pattern(term).search(line)
    if not quoted_term and not _line_has_term(line, term):
        return False
    if quoted_term and REDACTION_GUARD_DECLARATION.search(line) and ("[" in line or "new Set" in line or "{" in line):
        return True
    if relative == "frontend/web/src/components/documents/documentUrlSafety.ts" and ".test(" in line:
        return True
    return stripped.startswith("/") and stripped.rstrip(",").endswith(("/", "/i"))


def _is_type_only_line(relative: str, line: str, term: str) -> bool:
    if relative not in TYPE_ONLY_ALLOWLIST:
        return False
    stripped = line.strip()
    return bool(re.match(rf"""^{re.escape(term)}\??\s*:""", stripped))


def _audit_forbidden_private_terms(root: Path, files: list[Path]) -> dict[str, object]:
    violations: list[dict[str, object]] = []
    allowed_redaction_refs: list[dict[str, object]] = []
    type_only_refs: list[dict[str, object]] = []
    for path in files:
        relative = _relative_path(root, path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_number, line in enumerate(lines, start=1):
            for term in FORBIDDEN_PROJECTION_TERMS:
                if not _line_has_term(line, term):
                    continue
                if _is_redaction_guard_line(relative, line, term):
                    allowed_redaction_refs.append(
                        _line_refs(
                            root,
                            path,
                            line_number,
                            term,
                            "allowed_redaction_or_url_safety_guard",
                        )
                    )
                    continue
                if _is_type_only_line(relative, line, term):
                    type_only_refs.append(
                        _line_refs(root, path, line_number, term, "type_only_executor_payload_shape")
                    )
                    continue
                violations.append(
                    _line_refs(
                        root,
                        path,
                        line_number,
                        term,
                        "production_code_references_forbidden_projection_term",
                    )
                )
    return {
        "violations": violations,
        "allowed_redaction_refs": allowed_redaction_refs,
        "type_only_refs": type_only_refs,
    }


def _route_hit(root: Path, path: Path, line_number: int, route_prefix: str) -> dict[str, object]:
    return {
        "route_prefix": route_prefix,
        "references": [{"path": _relative_path(root, path), "line": line_number}],
    }


def _merge_route_hit(existing: dict[str, dict[str, object]], root: Path, path: Path, line_number: int, route_prefix: str) -> None:
    hit = existing.get(route_prefix)
    if hit is None:
        existing[route_prefix] = _route_hit(root, path, line_number, route_prefix)
        return
    references = hit["references"]
    if isinstance(references, list) and len(references) < 8:
        references.append({"path": _relative_path(root, path), "line": line_number})


def _scan_routes(root: Path, files: list[Path], prefixes: list[str]) -> list[dict[str, object]]:
    hits: dict[str, dict[str, object]] = {}
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_number, line in enumerate(lines, start=1):
            for prefix in prefixes:
                if prefix in line:
                    _merge_route_hit(hits, root, path, line_number, prefix)
    return [hits[prefix] for prefix in prefixes if prefix in hits]


def _legacy_route_policies(
    legacy_routes: list[dict[str, object]],
    *,
    route_scope: str,
) -> list[dict[str, object]]:
    policies: list[dict[str, object]] = []
    for route in legacy_routes:
        route_prefix = route["route_prefix"]
        if not isinstance(route_prefix, str):
            continue
        policy = LEGACY_ROUTE_POLICY_MAP.get(route_prefix)
        if policy is None:
            policies.append(
                {
                    "route_prefix": route_prefix,
                    "mapping_status": "missing_policy_mapping",
                    "ordinary_user_exposure": "fail_closed",
                    "admin_exposure": "fail_closed",
                    "governance_gate": "G6",
                    "required_action": "define_ai_platform_projection_policy",
                    "route_scope": route_scope,
                    "references": route.get("references", []),
                }
            )
            continue
        policies.append(
            {
                "route_prefix": route_prefix,
                "mapping_status": "mapped_pending_enforcement",
                **policy,
                "route_scope": route_scope,
                "references": route.get("references", []),
            }
        )
    return policies


def _route_inventory(root: Path, files: list[Path], *, route_scope: str) -> dict[str, object]:
    legacy_routes = _scan_routes(root, files, LEGACY_POLICY_REQUIRED_ROUTE_PREFIXES)
    return {
        "ai_platform_projection_routes": _scan_routes(root, files, AI_PLATFORM_ROUTE_PREFIXES),
        "same_origin_compat_routes": _scan_routes(root, files, COMPAT_ROUTE_PREFIXES),
        "legacy_policy_required_routes": legacy_routes,
        "legacy_route_policies": _legacy_route_policies(legacy_routes, route_scope=route_scope),
    }


def _ci_integration(root: Path) -> dict[str, object]:
    package_json_path = root / FRONTEND_PATH / "package.json"
    if not package_json_path.exists():
        return {"ci_verify_includes_projection_audit": False, "script": ""}
    try:
        package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"ci_verify_includes_projection_audit": False, "script": ""}
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    ci_verify = scripts.get("ci:verify") if isinstance(scripts.get("ci:verify"), str) else ""
    projection_audit = scripts.get("projection:audit") if isinstance(scripts.get("projection:audit"), str) else ""
    projection_audit_defined = "frontend_projection_audit.py" in projection_audit
    ci_verify_steps = [step.strip() for step in ci_verify.split("&&") if step.strip()]
    first_step = ci_verify_steps[0] if ci_verify_steps else ""
    audit_in_ci = projection_audit_defined and (
        first_step in _PROJECTION_AUDIT_SCRIPT_INVOCATIONS
        or _first_step_directly_launches_projection_audit(first_step)
    )
    return {
        "ci_verify_includes_projection_audit": audit_in_ci,
        "script": ci_verify,
        "projection_audit_script": projection_audit,
    }


_PROJECTION_AUDIT_SCRIPT_INVOCATIONS = {
    "corepack pnpm run projection:audit",
    "pnpm run projection:audit",
    "npm run projection:audit",
    "yarn projection:audit",
    "yarn run projection:audit",
}


def _first_step_directly_launches_projection_audit(first_step: str) -> bool:
    tokens = [token.strip("\"'").replace("\\", "/") for token in first_step.split()]
    if len(tokens) >= 3 and tokens[0] in {"node", "node.exe"}:
        return tokens[1] == "scripts/run-python-tool.mjs" and any(
            token.endswith("tools/frontend_projection_audit.py") for token in tokens[2:]
        )
    if len(tokens) >= 2 and tokens[0] in {"python", "python3", "py", "py.exe"}:
        return tokens[1].endswith("tools/frontend_projection_audit.py")
    return False


def build_frontend_projection_audit(repo_root: Path | None = None) -> dict[str, Any]:
    """Build a static audit for frontend public/admin projection boundaries."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    files = _production_source_files(root)
    active_files = _active_browser_source_files(root, files)
    private_terms = _audit_forbidden_private_terms(root, files)
    active_private_terms = _audit_forbidden_private_terms(root, active_files)
    active_path_set = {_relative_path(root, path) for path in active_files}
    quarantined_violations = [
        item
        for item in private_terms["violations"]
        if isinstance(item.get("path"), str) and item["path"] not in active_path_set
    ]
    route_inventory = _route_inventory(root, files, route_scope="production_source")
    active_route_inventory = _route_inventory(root, active_files, route_scope="active_browser_entry")
    legacy_routes = route_inventory["legacy_policy_required_routes"]
    legacy_route_policies = route_inventory["legacy_route_policies"]
    active_legacy_route_policies = active_route_inventory["legacy_route_policies"]

    violations = private_terms["violations"]
    open_gaps: list[str] = []
    if any(route["mapping_status"] == "missing_policy_mapping" for route in legacy_route_policies):
        open_gaps.append("legacy_routes_need_route_by_route_ai_platform_policy_mapping")
    elif legacy_routes:
        open_gaps.append("legacy_routes_need_policy_enforcement_or_ai_platform_remap")
    if active_legacy_route_policies:
        open_gaps.append("active_legacy_routes_need_policy_enforcement_or_ai_platform_remap")
    if quarantined_violations:
        open_gaps.append("quarantined_legacy_sources_need_ai_platform_projection_remap")
    ci = _ci_integration(root)
    if not ci["ci_verify_includes_projection_audit"]:
        open_gaps.append("frontend_ci_verify_does_not_yet_run_projection_audit")

    ci_blocks_release = not ci["ci_verify_includes_projection_audit"]
    active_violations = active_private_terms["violations"]

    if active_violations or ci_blocks_release:
        status = "blocked"
    elif open_gaps or violations:
        status = "pass_with_policy_gaps"
    else:
        status = "pass"

    return {
        "schema_version": SCHEMA_VERSION,
        "frontend_path": FRONTEND_PATH.as_posix(),
        "status": status,
        "scanned": {
            "source_root": SOURCE_ROOT.as_posix(),
            "production_source_files": len(files),
        },
        "forbidden_private_payload_terms": private_terms,
        "forbidden_projection_terms": private_terms,
        "active_browser_entry": {
            "entry_candidates": [candidate.as_posix() for candidate in ACTIVE_ENTRY_CANDIDATES],
            "files": [_relative_path(root, path) for path in active_files],
            "forbidden_projection_terms": active_private_terms,
            "route_inventory": active_route_inventory,
        },
        "quarantined_legacy_sources": {
            "violations": quarantined_violations,
            "policy": "not_in_active_browser_entry_graph_but_must_be_remapped_or_removed_before_g9_rollout",
        },
        "route_inventory": route_inventory,
        "ci_integration": ci,
        "open_gaps": open_gaps,
        "policy": {
            "ordinary_user": "fail_closed_until_legacy_routes_have_ai_platform_projection_mapping_and_acceptance",
            "admin": "same_tenant_operational_projection_only",
        },
    }


def render_frontend_projection_audit_markdown(audit: dict[str, Any]) -> str:
    """Render the frontend projection audit as operator-readable Markdown."""
    route_inventory = audit["route_inventory"]
    ai_routes = "\n".join(
        f"- `{route['route_prefix']}` ({len(route['references'])} sampled refs)"
        for route in route_inventory["ai_platform_projection_routes"]
    ) or "- none"
    legacy_routes = "\n".join(
        f"- `{route['route_prefix']}` ({len(route['references'])} sampled refs)"
        for route in route_inventory["legacy_policy_required_routes"]
    ) or "- none"
    legacy_policies = "\n".join(
        "- "
        f"`{route['route_prefix']}` gate `{route['governance_gate']}`, "
        f"ordinary `{route['ordinary_user_exposure']}`, "
        f"action `{route['required_action']}`"
        for route in route_inventory["legacy_route_policies"]
    ) or "- none"
    active_route_inventory = audit["active_browser_entry"]["route_inventory"]
    active_legacy_policies = "\n".join(
        "- "
        f"`{route['route_prefix']}` gate `{route['governance_gate']}`, "
        f"ordinary `{route['ordinary_user_exposure']}`, "
        f"action `{route['required_action']}`"
        for route in active_route_inventory["legacy_route_policies"]
    ) or "- none"
    gaps = "\n".join(f"- {gap}" for gap in audit["open_gaps"]) or "- none"
    private_violations = audit["forbidden_private_payload_terms"]["violations"]
    active_violations = audit["active_browser_entry"]["forbidden_projection_terms"]["violations"]
    quarantined_violations = audit["quarantined_legacy_sources"]["violations"]
    violation_lines = "\n".join(
        f"- `{item['path']}:{item['line']}` `{item['term']}`"
        for item in private_violations
    ) or "- none"
    active_lines = "\n".join(
        f"- `{item['path']}:{item['line']}` `{item['term']}`"
        for item in active_violations
    ) or "- none"
    quarantined_lines = "\n".join(
        f"- `{item['path']}:{item['line']}` `{item['term']}`"
        for item in quarantined_violations[:20]
    ) or "- none"
    return (
        "# ai-platform Frontend Projection Audit\n\n"
        f"Schema: `{audit['schema_version']}`\n\n"
        f"Frontend path: `{audit['frontend_path']}`\n\n"
        f"Status: `{audit['status']}`\n\n"
        f"Production source files scanned: `{audit['scanned']['production_source_files']}`\n\n"
        "## ai-platform Projection Routes\n\n"
        f"{ai_routes}\n\n"
        "## Legacy Routes Requiring Policy Mapping\n\n"
        f"{legacy_routes}\n\n"
        "## Legacy Route Policies\n\n"
        f"{legacy_policies}\n\n"
        "## Forbidden Projection Violations\n\n"
        f"{violation_lines}\n\n"
        "## Active Browser Entry\n\n"
        f"Active source files: `{len(audit['active_browser_entry']['files'])}`\n\n"
        f"{active_lines}\n\n"
        "## Active Legacy Route Policies\n\n"
        f"{active_legacy_policies}\n\n"
        "## Quarantined Legacy Sources\n\n"
        f"{quarantined_lines}\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit frontend public/admin projection boundaries.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    audit = build_frontend_projection_audit()
    if args.format == "json":
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_frontend_projection_audit_markdown(audit))
    if audit["status"] == "blocked":
        sys.exit(1)


if __name__ == "__main__":
    main()
