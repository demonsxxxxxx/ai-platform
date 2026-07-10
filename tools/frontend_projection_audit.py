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
_IDENTIFIER_FORBIDDEN_TERMS = [
    term for term in FORBIDDEN_PROJECTION_TERMS if re.fullmatch(r"[A-Za-z0-9_]+", term)
]
_LITERAL_FORBIDDEN_TERMS = [
    term for term in FORBIDDEN_PROJECTION_TERMS if term not in _IDENTIFIER_FORBIDDEN_TERMS
]
_FORBIDDEN_TERM_SCAN = re.compile(
    "|".join(
        [rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])" for term in _IDENTIFIER_FORBIDDEN_TERMS]
        + [re.escape(term) for term in _LITERAL_FORBIDDEN_TERMS]
    )
)

REDACTION_GUARD_PATHS = {
    "frontend/web/src/components/documents/documentUrlSafety.ts",
    "frontend/web/src/hooks/useAgent/eventProcessor.ts",
    "frontend/web/src/services/api/agent.ts",
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
SAFE_PUBLIC_ROUTE_PREFIXES = [
    "/api/agent-workspace",
    "/api/agent/models/available",
    "/api/channels",
    "/api/github",
    "/api/feedback",
    "/api/marketplace",
    "/api/mcp",
    "/api/notifications/active",
    "/api/role-governance",
    "/api/skills",
    "/api/settings",
    "/api/users",
]
SAFE_ADMIN_ROUTE_PREFIXES = [
    "/api/admin/mcp",
    "/api/notifications/admin",
]
ORDINARY_USER_BASELINE_PERMISSION_TOKENS = {
    "AGENT_USE",
    "ARTIFACT_DOWNLOAD",
    "CHAT_READ",
    "CHAT_WRITE",
    "FILE_UPLOAD",
    "FILE_UPLOAD_DOCUMENT",
    "SESSION_READ",
    "SESSION_WRITE",
}
COMPAT_ROUTE_PREFIXES = [
    "/api/auth",
    "/api/chat",
    "/api/files/revealed",
    "/api/sessions",
    "/api/upload",
]
LEGACY_POLICY_REQUIRED_ROUTE_PREFIXES = [
    "/api/admin/",
    "/api/agent/config",
    "/api/agent/models",
    "/api/env-vars",
    "/api/memory",
    "/api/persona-presets",
    "/api/roles",
]
LEGACY_ROUTE_POLICY_MAP: dict[str, dict[str, str]] = {
    "/api/admin/": {
        "domain": "admin_operations",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_projection_only",
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
    "/api/env-vars": {
        "domain": "runtime_secret_policy",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed",
        "admin_exposure": "same_tenant_admin_masked_projection_only",
        "required_action": "remap_to_ai_platform_admin_projection_or_hide",
    },
    "/api/memory": {
        "domain": "memory_governance",
        "governance_gate": "G6",
        "ordinary_user_exposure": "fail_closed_until_ai_platform_memory_projection",
        "admin_exposure": "same_tenant_admin_memory_projection_only",
        "required_action": "remap_to_ai_platform_public_or_admin_projection",
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
PERMISSION_VARIABLE_ASSIGNMENT = re.compile(
    r"""\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"""
    r"""(?:hasPermission|hasAnyPermission)\s*\((?P<args>[^;\n]+)\)""",
    re.DOTALL,
)
ENABLED_OPTION_CALL = re.compile(
    r"""\b(?P<callee>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*\{[^{}]*\benabled\s*:\s*(?P<expression>[^,}\n]+)[^{}]*\}""",
    re.DOTALL,
)
PERMISSION_TOKEN = re.compile(r"""Permission\.([A-Z0-9_]+)""")
LAZY_COMPONENT_IMPORT = re.compile(
    r"""\bconst\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*lazy\s*\(\s*\(\s*\)\s*=>\s*import\s*\(\s*["'](?P<specifier>[^"']+)["']\s*\)""",
    re.DOTALL,
)
PROTECTED_ROUTE_BLOCK = re.compile(
    r"""<ProtectedRoute\b(?P<attrs>[^>]*)>(?P<body>.*?)</ProtectedRoute>""",
    re.DOTALL,
)
JSX_COMPONENT_TAG = re.compile(r"""<(?P<name>[A-Z][A-Za-z0-9_$]*)\b""")
FUNCTION_DECLARATION = re.compile(
    r"""\bfunction\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b(?P<body>.*?)(?=\nfunction\s+[A-Za-z_$]|\Z)""",
    re.DOTALL,
)
ACTIVE_TAB_LITERAL = re.compile(r"""<AppContent\b[^>]*\bactiveTab\s*=\s*["'](?P<tab>[^"']+)["']""")
PANEL_MAP_ENTRY = re.compile(r"""(?P<tab>[A-Za-z0-9_-]+)\s*:\s*(?P<component>[A-Z][A-Za-z0-9_$]*)""")
FROM_MODULE_SPECIFIER_STRING = re.compile(
    r"""(?P<prefix>\b(?:import|export)\b[^'";]*\bfrom\s*)["'][^"']+["']""",
    re.DOTALL,
)
SIDE_EFFECT_MODULE_SPECIFIER_STRING = re.compile(r"""(?P<prefix>\bimport\s*)["'][^"']+["']""")
DYNAMIC_MODULE_SPECIFIER_STRING = re.compile(
    r"""(?P<prefix>\bimport\s*\(\s*)["'][^"']+["'](?P<suffix>\s*\))"""
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


def _permission_guards_by_symbol(root: Path, files: list[Path]) -> dict[str, list[str]]:
    guards: dict[str, list[str]] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        guard_permissions: dict[str, list[str]] = {}
        for match in PERMISSION_VARIABLE_ASSIGNMENT.finditer(text):
            permissions = sorted(set(PERMISSION_TOKEN.findall(match.group("args"))))
            if permissions:
                guard_permissions[match.group("name")] = permissions
        if not guard_permissions:
            continue
        for match in ENABLED_OPTION_CALL.finditer(text):
            permissions = sorted(
                {
                    permission
                    for guard in re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", match.group("expression"))
                    for permission in guard_permissions.get(guard, [])
                }
            )
            if permissions:
                guards[match.group("callee")] = permissions
    return guards


def _exported_function_names(text: str) -> set[str]:
    return {
        match.group("name")
        for match in re.finditer(
            r"""\bexport\s+function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b""",
            text,
        )
    }


def _requested_gated_entry_files(root: Path, active_files: list[Path]) -> list[tuple[Path, list[str]]]:
    guards_by_symbol = _permission_guards_by_symbol(root, active_files)
    entries: list[tuple[Path, list[str]]] = []
    active_set = {path.resolve() for path in active_files}
    if not guards_by_symbol:
        guards_by_symbol = {}
    for path in active_files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        exported = _exported_function_names(text)
        permissions: set[str] = set()
        for symbol in exported:
            permissions.update(guards_by_symbol.get(symbol, []))
        if permissions:
            resolved = path.resolve()
            if resolved in active_set:
                entries.append((resolved, sorted(permissions)))
        lazy_imports = {
            match.group("name"): _resolve_relative_module(path, match.group("specifier"))
            for match in LAZY_COMPONENT_IMPORT.finditer(text)
        }
        if lazy_imports:
            for match in PROTECTED_ROUTE_BLOCK.finditer(text):
                permissions = sorted(set(PERMISSION_TOKEN.findall(match.group("attrs"))))
                if not permissions:
                    continue
                for component_match in JSX_COMPONENT_TAG.finditer(match.group("body")):
                    resolved = lazy_imports.get(component_match.group("name"))
                    if resolved is not None and resolved in active_set:
                        entries.append((resolved, permissions))
        function_bodies = {
            match.group("name"): match.group("body")
            for match in FUNCTION_DECLARATION.finditer(text)
        }
        gated_tabs: dict[str, list[str]] = {}
        for match in PROTECTED_ROUTE_BLOCK.finditer(text):
            permissions = sorted(set(PERMISSION_TOKEN.findall(match.group("attrs"))))
            if not permissions:
                continue
            for component_match in JSX_COMPONENT_TAG.finditer(match.group("body")):
                body = function_bodies.get(component_match.group("name"), "")
                for tab_match in ACTIVE_TAB_LITERAL.finditer(body):
                    gated_tabs[tab_match.group("tab")] = permissions
        if gated_tabs:
            tab_content_path = (root / SOURCE_ROOT / "components/layout/AppContent/TabContent.tsx").resolve()
            if tab_content_path.is_file():
                try:
                    tab_content_text = tab_content_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    tab_content_text = tab_content_path.read_text(encoding="utf-8", errors="replace")
                tab_lazy_imports = {
                    match.group("name"): _resolve_relative_module(tab_content_path, match.group("specifier"))
                    for match in LAZY_COMPONENT_IMPORT.finditer(tab_content_text)
                }
                for panel_match in PANEL_MAP_ENTRY.finditer(tab_content_text):
                    permissions = gated_tabs.get(panel_match.group("tab"))
                    resolved = tab_lazy_imports.get(panel_match.group("component"))
                    if permissions and resolved is not None and resolved in active_set:
                        entries.append((resolved, permissions))
    return entries


def _merge_permission_list(previous: list[str] | None, incoming: list[str]) -> list[str]:
    if previous is None:
        return list(incoming)
    return sorted(set(previous) | set(incoming))


def _gated_files_by_permission(root: Path, active_files: list[Path]) -> dict[str, list[str]]:
    active_set = {path.resolve() for path in active_files}
    gated: dict[Path, list[str]] = {}
    seen_requests: dict[tuple[Path, tuple[str, ...]], set[str] | None] = {}
    stack: list[tuple[Path, list[str], set[str] | None]] = [
        (path, permissions, None)
        for path, permissions in _requested_gated_entry_files(root, active_files)
    ]
    while stack:
        path, permissions, requested = stack.pop()
        if path not in active_set:
            continue
        permission_key = tuple(permissions)
        seen_key = (path, permission_key)
        previous_request = seen_requests.get(seen_key, _UNVISITED)
        if not _should_process_with_request(previous_request, requested):
            continue
        seen_requests[seen_key] = _merge_requested_symbols(previous_request, requested)
        previous = gated.get(path)
        merged = _merge_permission_list(previous, permissions)
        gated[path] = merged
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for resolved, next_requested in _dependency_edges(path, text, seen_requests[seen_key]):
            stack.append((resolved, merged, next_requested))
    return {_relative_path(root, path): permissions for path, permissions in gated.items()}


def _active_requested_symbols_by_path(root: Path, active_files: list[Path]) -> dict[str, set[str] | None]:
    source_root = root / SOURCE_ROOT
    active_set = {path.resolve() for path in active_files}
    entries = [
        (root / candidate).resolve()
        for candidate in ACTIVE_ENTRY_CANDIDATES
        if (root / candidate).is_file()
    ]
    if not entries and source_root.exists():
        return {_relative_path(root, path): None for path in active_files}

    requested_by_path: dict[Path, set[str] | None] = {}
    stack: list[tuple[Path, set[str] | None]] = [(entry, None) for entry in entries]
    while stack:
        path, requested = stack.pop()
        if path not in active_set:
            continue
        previous = requested_by_path.get(path, _UNVISITED)
        if not _should_process_with_request(previous, requested):
            continue
        requested_by_path[path] = _merge_requested_symbols(previous, requested)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for resolved, next_requested in _dependency_edges(path, text, requested_by_path[path]):
            stack.append((resolved, next_requested))
    return {_relative_path(root, path): requested for path, requested in requested_by_path.items()}


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


def _forbidden_terms_in_line(line: str) -> list[str]:
    return sorted({match.group(0) for match in _FORBIDDEN_TERM_SCAN.finditer(line)}, key=FORBIDDEN_PROJECTION_TERMS.index)


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
            for term in _forbidden_terms_in_line(line):
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


def _symbol_body_start_index(line: str, *, allow_assignment_body: bool) -> int | None:
    patterns = [
        r"\)\s*(?::[^{]+)?\s*\{",
        r"=>\s*\{",
    ]
    if allow_assignment_body:
        patterns.append(r"=\s*\{")
    starts: list[int] = []
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            starts.append(line.find("{", match.start()))
    return min(starts) if starts else None


def _requested_lines(lines: list[str], requested: set[str] | None) -> set[int] | None:
    if requested is None:
        return None
    spans: set[int] = set()
    requested_spans: list[tuple[int, int]] = []
    for index, line in enumerate(lines, start=1):
        for symbol in requested:
            if re.search(rf"""\b(?:export\s+)?(?:async\s+)?(?:function|const|let|var)\s+{re.escape(symbol)}\b""", line):
                balance = 0
                saw_body = False
                start = index
                cursor = index
                while cursor <= len(lines):
                    current_line = lines[cursor - 1]
                    spans.add(cursor)
                    if not saw_body:
                        body_start = _symbol_body_start_index(
                            current_line,
                            allow_assignment_body=cursor == start,
                        )
                        if body_start is not None:
                            saw_body = True
                            body_segment = current_line[body_start:]
                            balance += body_segment.count("{") - body_segment.count("}")
                        elif ";" in current_line:
                            break
                    else:
                        balance += current_line.count("{") - current_line.count("}")
                    if saw_body and balance <= 0:
                        break
                    cursor += 1
                requested_spans.append((start, cursor))
    if requested_spans:
        for index, line in enumerate(lines, start=1):
            if not re.search(r"""\bconst\s+[A-Z0-9_]*API[A-Z0-9_]*\s*=""", line):
                continue
            name_match = re.search(r"""\bconst\s+(?P<name>[A-Z0-9_]*API[A-Z0-9_]*)\s*=""", line)
            if not name_match:
                continue
            name = name_match.group("name")
            if any(
                name in requested_line
                for start, end in requested_spans
                for requested_line in lines[start - 1 : end]
            ):
                spans.add(index)
    return spans


def _strip_module_specifier_strings(text: str) -> str:
    text = FROM_MODULE_SPECIFIER_STRING.sub(r"\g<prefix>''", text)
    text = SIDE_EFFECT_MODULE_SPECIFIER_STRING.sub(r"\g<prefix>''", text)
    return DYNAMIC_MODULE_SPECIFIER_STRING.sub(r"\g<prefix>''\g<suffix>", text)


def _scan_routes(
    root: Path,
    files: list[Path],
    prefixes: list[str],
    *,
    excluded_prefixes: list[str] | None = None,
    requested_symbols_by_path: dict[str, set[str] | None] | None = None,
) -> list[dict[str, object]]:
    hits: dict[str, dict[str, object]] = {}
    excluded_prefixes = excluded_prefixes or []
    for path in files:
        relative = _relative_path(root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        lines = _strip_module_specifier_strings(text).splitlines()
        requested_symbols = (
            requested_symbols_by_path.get(relative)
            if requested_symbols_by_path is not None
            else None
        )
        included_lines = _requested_lines(lines, requested_symbols)
        for line_number, line in enumerate(lines, start=1):
            if included_lines is not None and line_number not in included_lines:
                continue
            for prefix in prefixes:
                if prefix in line and not any(excluded in line for excluded in excluded_prefixes):
                    _merge_route_hit(hits, root, path, line_number, prefix)
    return [hits[prefix] for prefix in prefixes if prefix in hits]


def _legacy_route_policies(
    legacy_routes: list[dict[str, object]],
    *,
    route_scope: str,
    gated_files: dict[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    policies: list[dict[str, object]] = []
    gated_files = gated_files or {}
    for route in legacy_routes:
        route_prefix = route["route_prefix"]
        if not isinstance(route_prefix, str):
            continue
        references = route.get("references", [])
        reference_paths = [
            str(reference.get("path"))
            for reference in references
            if isinstance(reference, dict) and isinstance(reference.get("path"), str)
        ]
        required_permissions = sorted(
            {
                permission
                for path in reference_paths
                for permission in gated_files.get(path, [])
            }
        )
        non_ordinary_permissions = [
            permission
            for permission in required_permissions
            if permission not in ORDINARY_USER_BASELINE_PERMISSION_TOKENS
        ]
        active_browser_access = (
            "permission_gated"
            if route_scope == "active_browser_entry" and non_ordinary_permissions
            else "ordinary_user_reachable"
            if route_scope == "active_browser_entry"
            else "not_applicable"
        )
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
                    "active_browser_access": active_browser_access,
                    "required_permissions": required_permissions,
                    "non_ordinary_required_permissions": non_ordinary_permissions,
                    "references": references,
                }
            )
            continue
        policies.append(
            {
                "route_prefix": route_prefix,
                "mapping_status": "mapped_pending_enforcement",
                **policy,
                "route_scope": route_scope,
                "active_browser_access": active_browser_access,
                "required_permissions": required_permissions,
                "non_ordinary_required_permissions": non_ordinary_permissions,
                "references": references,
            }
        )
    return policies


def _route_inventory(
    root: Path,
    files: list[Path],
    *,
    route_scope: str,
    gated_files: dict[str, list[str]] | None = None,
    requested_symbols_by_path: dict[str, set[str] | None] | None = None,
) -> dict[str, object]:
    legacy_routes = _scan_routes(
        root,
        files,
        LEGACY_POLICY_REQUIRED_ROUTE_PREFIXES,
        excluded_prefixes=SAFE_PUBLIC_ROUTE_PREFIXES + SAFE_ADMIN_ROUTE_PREFIXES,
        requested_symbols_by_path=requested_symbols_by_path,
    )
    legacy_route_policies = _legacy_route_policies(
        legacy_routes,
        route_scope=route_scope,
        gated_files=gated_files,
    )
    ordinary_user_reachable_legacy_route_policies = [
        route
        for route in legacy_route_policies
        if route.get("active_browser_access") == "ordinary_user_reachable"
    ]
    return {
        "ai_platform_projection_routes": _scan_routes(
            root,
            files,
            AI_PLATFORM_ROUTE_PREFIXES,
            requested_symbols_by_path=requested_symbols_by_path,
        ),
        "safe_public_projection_routes": _scan_routes(
            root,
            files,
            SAFE_PUBLIC_ROUTE_PREFIXES,
            requested_symbols_by_path=requested_symbols_by_path,
        ),
        "safe_admin_projection_routes": _scan_routes(
            root,
            files,
            SAFE_ADMIN_ROUTE_PREFIXES,
            requested_symbols_by_path=requested_symbols_by_path,
        ),
        "same_origin_compat_routes": _scan_routes(
            root,
            files,
            COMPAT_ROUTE_PREFIXES,
            requested_symbols_by_path=requested_symbols_by_path,
        ),
        "legacy_policy_required_routes": legacy_routes,
        "legacy_route_policies": legacy_route_policies,
        "ordinary_user_reachable_legacy_route_policies": ordinary_user_reachable_legacy_route_policies,
    }


def _governance_gate_sort_key(gate: object) -> tuple[int, str]:
    text = str(gate or "")
    match = re.fullmatch(r"G(\d+)", text)
    return (int(match.group(1)) if match else 999, text)


def _route_policy_summary(route: dict[str, object]) -> dict[str, object]:
    references = route.get("references")
    return {
        "route_prefix": route.get("route_prefix"),
        "domain": route.get("domain"),
        "governance_gate": route.get("governance_gate"),
        "route_scope": route.get("route_scope"),
        "ordinary_user_exposure": route.get("ordinary_user_exposure"),
        "admin_exposure": route.get("admin_exposure"),
        "required_action": route.get("required_action"),
        "reference_count": len(references) if isinstance(references, list) else 0,
        "sample_references": references[:8] if isinstance(references, list) else [],
    }


def _route_gap_detail(gap: str, routes: list[dict[str, object]]) -> dict[str, object]:
    governance_gates = sorted(
        {str(route.get("governance_gate")) for route in routes if route.get("governance_gate")},
        key=_governance_gate_sort_key,
    )
    return {
        "gap": gap,
        "status": "mapped_pending_enforcement",
        "governance_gates": governance_gates,
        "count": len(routes),
        "routes": [_route_policy_summary(route) for route in routes],
    }


def _quarantined_gap_detail(gap: str, violations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "gap": gap,
        "status": "quarantined_pending_projection_remap",
        "governance_gates": ["G6", "G9"],
        "count": len(violations),
        "required_action": "remap_to_ai_platform_public_or_admin_projection_or_remove_before_g9_rollout",
        "sample_violations": [
            {
                "path": item.get("path"),
                "line": item.get("line"),
                "reason": item.get("reason"),
                "required_action": "remap_or_remove_before_g9_rollout",
            }
            for item in violations[:20]
        ],
    }


def _ci_gap_detail(gap: str, ci: dict[str, object]) -> dict[str, object]:
    return {
        "gap": gap,
        "status": "missing_ci_projection_audit_gate",
        "governance_gates": ["G6", "G9"],
        "count": 1,
        "required_action": "make_ci_verify_start_with_frontend_projection_audit",
        "ci_verify_configured": bool(ci.get("script")),
        "projection_audit_configured": bool(ci.get("projection_audit_script")),
        "ci_verify_includes_projection_audit": bool(
            ci.get("ci_verify_includes_projection_audit")
        ),
    }


def _open_gap_details(
    *,
    legacy_route_policies: list[dict[str, object]],
    active_legacy_route_policies: list[dict[str, object]],
    ordinary_user_reachable_active_legacy_route_policies: list[dict[str, object]],
    quarantined_violations: list[dict[str, object]],
    ci: dict[str, object],
) -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    if any(route["mapping_status"] == "missing_policy_mapping" for route in legacy_route_policies):
        missing_policy_routes = [
            route for route in legacy_route_policies if route["mapping_status"] == "missing_policy_mapping"
        ]
        details.append(
            _route_gap_detail(
                "legacy_routes_need_route_by_route_ai_platform_policy_mapping",
                missing_policy_routes,
            )
        )
    elif legacy_route_policies:
        details.append(
            _route_gap_detail(
                "legacy_routes_need_policy_enforcement_or_ai_platform_remap",
                legacy_route_policies,
            )
        )
    if active_legacy_route_policies:
        details.append(
            _route_gap_detail(
                "active_legacy_routes_need_policy_enforcement_or_ai_platform_remap",
                active_legacy_route_policies,
            )
        )
    if ordinary_user_reachable_active_legacy_route_policies:
        details.append(
            _route_gap_detail(
                "ordinary_user_reachable_legacy_routes_need_policy_enforcement_or_ai_platform_remap",
                ordinary_user_reachable_active_legacy_route_policies,
            )
        )
    if quarantined_violations:
        details.append(
            _quarantined_gap_detail(
                "quarantined_legacy_sources_need_ai_platform_projection_remap",
                quarantined_violations,
            )
        )
    if not ci["ci_verify_includes_projection_audit"]:
        details.append(_ci_gap_detail("frontend_ci_verify_does_not_yet_run_projection_audit", ci))
    return details


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
    active_gated_files = _gated_files_by_permission(root, active_files)
    active_requested_symbols_by_path = _active_requested_symbols_by_path(root, active_files)
    quarantined_violations = [
        item
        for item in private_terms["violations"]
        if isinstance(item.get("path"), str) and item["path"] not in active_path_set
    ]
    route_inventory = _route_inventory(root, files, route_scope="production_source")
    active_route_inventory = _route_inventory(
        root,
        active_files,
        route_scope="active_browser_entry",
        gated_files=active_gated_files,
        requested_symbols_by_path=active_requested_symbols_by_path,
    )
    legacy_routes = route_inventory["legacy_policy_required_routes"]
    legacy_route_policies = route_inventory["legacy_route_policies"]
    active_legacy_route_policies = active_route_inventory["legacy_route_policies"]
    ordinary_user_reachable_active_legacy_route_policies = active_route_inventory[
        "ordinary_user_reachable_legacy_route_policies"
    ]

    violations = private_terms["violations"]
    ci = _ci_integration(root)
    open_gap_details = _open_gap_details(
        legacy_route_policies=legacy_route_policies,
        active_legacy_route_policies=active_legacy_route_policies,
        ordinary_user_reachable_active_legacy_route_policies=ordinary_user_reachable_active_legacy_route_policies,
        quarantined_violations=quarantined_violations,
        ci=ci,
    )
    open_gaps = [str(item["gap"]) for item in open_gap_details]

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
        "open_gap_details": open_gap_details,
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
    safe_public_routes = "\n".join(
        f"- `{route['route_prefix']}` ({len(route['references'])} sampled refs)"
        for route in route_inventory["safe_public_projection_routes"]
    ) or "- none"
    safe_admin_routes = "\n".join(
        f"- `{route['route_prefix']}` ({len(route['references'])} sampled refs)"
        for route in route_inventory["safe_admin_projection_routes"]
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
    detail_lines: list[str] = []
    for detail in audit.get("open_gap_details", []):
        gap = detail.get("gap")
        count = detail.get("count", 0)
        gates = ", ".join(str(gate) for gate in detail.get("governance_gates", [])) or "none"
        detail_lines.append(f"- `{gap}` count `{count}` gates `{gates}`")
        routes = detail.get("routes")
        if isinstance(routes, list):
            for route in routes[:8]:
                detail_lines.append(
                    "  - "
                    f"`{route.get('route_prefix')}` scope `{route.get('route_scope')}` "
                    f"action `{route.get('required_action')}`"
                )
        violations = detail.get("sample_violations")
        if isinstance(violations, list):
            for item in violations[:8]:
                detail_lines.append(
                    "  - "
                    f"`{item.get('path')}:{item.get('line')}` action `{item.get('required_action')}`"
                )
    gap_detail_lines = "\n".join(detail_lines) or "- none"
    return (
        "# ai-platform Frontend Projection Audit\n\n"
        f"Schema: `{audit['schema_version']}`\n\n"
        f"Frontend path: `{audit['frontend_path']}`\n\n"
        f"Status: `{audit['status']}`\n\n"
        f"Production source files scanned: `{audit['scanned']['production_source_files']}`\n\n"
        "## ai-platform Projection Routes\n\n"
        f"{ai_routes}\n\n"
        "## Safe Public Projection Routes\n\n"
        f"{safe_public_routes}\n\n"
        "## Safe Admin Projection Routes\n\n"
        f"{safe_admin_routes}\n\n"
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
        f"{gaps}\n\n"
        "## Open Gap Details\n\n"
        f"{gap_detail_lines}\n"
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
