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
    audit_in_ci = projection_audit_defined and first_step in {
        "pnpm run projection:audit",
        "npm run projection:audit",
        "yarn projection:audit",
        "yarn run projection:audit",
    }
    return {
        "ci_verify_includes_projection_audit": audit_in_ci,
        "script": ci_verify,
        "projection_audit_script": projection_audit,
    }


def build_frontend_projection_audit(repo_root: Path | None = None) -> dict[str, Any]:
    """Build a static audit for frontend public/admin projection boundaries."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    files = _production_source_files(root)
    private_terms = _audit_forbidden_private_terms(root, files)
    ai_routes = _scan_routes(root, files, AI_PLATFORM_ROUTE_PREFIXES)
    compat_routes = _scan_routes(root, files, COMPAT_ROUTE_PREFIXES)
    legacy_routes = _scan_routes(root, files, LEGACY_POLICY_REQUIRED_ROUTE_PREFIXES)

    violations = private_terms["violations"]
    open_gaps: list[str] = []
    if legacy_routes:
        open_gaps.append("legacy_routes_need_route_by_route_ai_platform_policy_mapping")
    ci = _ci_integration(root)
    if not ci["ci_verify_includes_projection_audit"]:
        open_gaps.append("frontend_ci_verify_does_not_yet_run_projection_audit")

    ci_blocks_release = not ci["ci_verify_includes_projection_audit"]

    if violations or ci_blocks_release:
        status = "blocked"
    elif open_gaps:
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
        "route_inventory": {
            "ai_platform_projection_routes": ai_routes,
            "same_origin_compat_routes": compat_routes,
            "legacy_policy_required_routes": legacy_routes,
        },
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
    gaps = "\n".join(f"- {gap}" for gap in audit["open_gaps"]) or "- none"
    private_violations = audit["forbidden_private_payload_terms"]["violations"]
    violation_lines = "\n".join(
        f"- `{item['path']}:{item['line']}` `{item['term']}`"
        for item in private_violations
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
        "## Forbidden Projection Violations\n\n"
        f"{violation_lines}\n\n"
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
