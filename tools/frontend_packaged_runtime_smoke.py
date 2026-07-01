import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.frontend-packaged-runtime-smoke.v1"
EVIDENCE_SCHEMA_VERSION = "ai-platform.frontend-packaged-runtime-smoke-evidence.v1"
GATE_NAME = "#17 Packaged Frontend Runtime Smoke"

REQUIRED_EVIDENCE_FIELDS = [
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

OPERATOR_COMMANDS = [
    "sudo -n docker build --build-arg AI_PLATFORM_BUILD_COMMIT=<commit_sha> --build-arg AI_PLATFORM_BUILD_DIRTY=false -f frontend/web/Dockerfile -t ai-platform-frontend:<commit_short>-smoke .",
    "sudo -n docker image inspect ai-platform-frontend:<commit_short>-smoke",
    "sudo -n docker run --rm -d --name ai-platform-frontend-smoke-<commit_short> --network ai-platform-phaseb_default -e AI_PLATFORM_API_UPSTREAM=http://ai-platform-api:8020 -p <smoke_port>:8080 ai-platform-frontend:<commit_short>-smoke",
    "curl -fsS http://127.0.0.1:<smoke_port>/healthz",
    "curl -fsS http://127.0.0.1:<smoke_port>/",
    "curl -fsS http://127.0.0.1:<smoke_port>/ai-platform-build-provenance.json",
    "curl -fsS http://127.0.0.1:<smoke_port>/api/ai/health",
    "grep -E -i -q -f <forbidden_marker_patterns> <runtime_smoke_artifacts>; status=$?; if [ \"$status\" -eq 0 ]; then printf '{\"status\":\"failed\",\"forbidden_markers\":[\"redacted_match\"]}' > <leak_scan_json>; exit 1; elif [ \"$status\" -eq 1 ]; then printf '{\"status\":\"passed\",\"forbidden_markers\":[]}' > <leak_scan_json>; else exit \"$status\"; fi",
    "sudo -n docker rm -f ai-platform-frontend-smoke-<commit_short>",
    "remaining=\"$(sudo -n docker ps -a --filter name=ai-platform-frontend-smoke-<commit_short> --format '{{.Names}}')\" && test -z \"$remaining\"",
    "sudo -n env AI_PLATFORM_FRONTEND_IMAGE=ai-platform-frontend:<commit_short>-smoke AI_PLATFORM_FRONTEND_PORT=18001 AI_PLATFORM_BUILD_COMMIT=<commit_sha> AI_PLATFORM_BUILD_DIRTY=false docker compose up -d --no-build frontend",
    "sudo -n docker compose ps frontend",
    "curl -fsS http://127.0.0.1:18001/healthz",
    "curl -fsS http://127.0.0.1:18001/auth/login",
    "curl -fsS http://127.0.0.1:18001/chat",
    "grep -E -q '<composer_ready_selector>' <logged_in_chat_html_or_browser_probe_json>",
    "curl -fsS http://127.0.0.1:18001/api/ai/health",
]


def _status_code_ok(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return int(value.get("status_code", 0)) == 200
    except (TypeError, ValueError):
        return False


def _body_status_ok(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    body = value.get("body")
    if isinstance(body, dict):
        return body.get("status") == "ok"
    if isinstance(body, str):
        return body.strip().lower() == "ok"
    return False


def _nested_dict(value: object, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    child = value.get(key)
    return child if isinstance(child, dict) else {}


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classify_build_environment_blockers(evidence: dict[str, Any]) -> list[str]:
    docker_build = _nested_dict(evidence, "docker_build")
    exit_code = _int_or_none(docker_build.get("exit_code", 0))
    if exit_code is None:
        return []
    if exit_code == 0:
        return []
    log_tail = str(docker_build.get("log_tail") or "").lower()
    blockers: list[str] = []
    if "proxyconnect" in log_tail:
        blockers.append("docker_registry_proxy_unreachable")
    base_image_patterns = [
        "failed to resolve source metadata",
        "resolve source metadata",
        "failed to copy",
        "failed to fetch anonymous token",
        "load metadata for docker.io",
        "pull access denied",
    ]
    if any(pattern in log_tail for pattern in base_image_patterns) or (
        ("from node:" in log_tail or "from nginx:" in log_tail)
        and ("proxyconnect" in log_tail or "failed" in log_tail)
    ):
        blockers.append("base_image_pull_failed")
    if not blockers:
        blockers.append("docker_build_failed")
    return list(dict.fromkeys(blockers))


def _missing_fields(evidence: dict[str, Any]) -> list[str]:
    missing = []
    for field in REQUIRED_EVIDENCE_FIELDS:
        if field not in evidence:
            missing.append(field)
            continue
        value = evidence.get(field)
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and not value.strip():
            missing.append(field)
    return missing


def _image_tag_matches_commit(image_tag: object, commit_sha: str) -> bool:
    if not _commit_sha_format_valid(commit_sha) or not isinstance(image_tag, str) or not image_tag.strip():
        return False
    commit_short = commit_sha[:7]
    return image_tag.strip() == f"ai-platform-frontend:{commit_short}-smoke"


def _commit_sha_format_valid(commit_sha: object) -> bool:
    return isinstance(commit_sha, str) and re.fullmatch(r"[0-9a-f]{40}", commit_sha) is not None


def _runtime_host_slug(runtime_host: object) -> str | None:
    if not isinstance(runtime_host, str) or not runtime_host.strip():
        return None
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", runtime_host.strip().lower()).strip("_")
    return slug or None


def _closed_evidence_items(evidence: dict[str, Any], status: str) -> list[str]:
    if status != "ready_for_operator_review":
        return []
    items = ["packaged_frontend_runtime_smoke"]
    host_slug = _runtime_host_slug(evidence.get("runtime_host"))
    if host_slug:
        items.append(f"{host_slug}_packaged_frontend_runtime_smoke")
    return list(dict.fromkeys(items))


def _build_checks(evidence: dict[str, Any]) -> dict[str, bool]:
    commit_sha = str(evidence.get("commit_sha") or "")
    image_inspect = _nested_dict(evidence, "image_inspect")
    provenance = _nested_dict(evidence, "build_provenance")
    provenance_git = _nested_dict(provenance, "git")
    runtime_smoke = _nested_dict(evidence, "runtime_smoke")
    compose_service = _nested_dict(evidence, "compose_service")
    api_health = _nested_dict(runtime_smoke, "api_health")
    logged_in_chat = _nested_dict(runtime_smoke, "logged_in_chat")
    composer = _nested_dict(runtime_smoke, "composer")
    leak_scan = _nested_dict(evidence, "leak_scan")
    cleanup = _nested_dict(evidence, "cleanup")
    docker_build_exit_code = _int_or_none(_nested_dict(evidence, "docker_build").get("exit_code", 1))
    return {
        "docker_build_succeeded": docker_build_exit_code == 0,
        "commit_sha_format_valid": _commit_sha_format_valid(commit_sha),
        "image_tag_matches_commit": _image_tag_matches_commit(evidence.get("image_tag"), commit_sha),
        "image_revision_matches_commit": (
            _commit_sha_format_valid(commit_sha) and image_inspect.get("revision") == commit_sha
        ),
        "build_provenance_matches_commit": _commit_sha_format_valid(commit_sha)
        and provenance.get("schema_version") == "ai-platform.frontend-build-provenance.v1"
        and provenance_git.get("commit") == commit_sha
        and provenance_git.get("dirty") is False,
        "compose_service_named_frontend": compose_service.get("service") == "frontend",
        "compose_container_named_ai_platform_frontend": (
            compose_service.get("container_name") == "ai-platform-frontend"
        ),
        "compose_port_18001_bound": compose_service.get("host_port") == 18001
        and compose_service.get("container_port") == 8080,
        "compose_service_running": compose_service.get("state") in {"running", "Up", "healthy"},
        "healthz_ok": _status_code_ok(runtime_smoke.get("healthz")) and _body_status_ok(runtime_smoke.get("healthz")),
        "index_ok": _status_code_ok(runtime_smoke.get("index")),
        "auth_login_ok": _status_code_ok(runtime_smoke.get("auth_login")),
        "logged_in_chat_ok": _status_code_ok(logged_in_chat)
        and logged_in_chat.get("authenticated") is True
        and logged_in_chat.get("redirected_to_login") is False,
        "composer_visible": composer.get("visible") is True,
        "composer_usable": composer.get("usable") is True,
        "api_proxy_ok": _status_code_ok(api_health) and _body_status_ok(api_health),
        "build_provenance_endpoint_ok": _status_code_ok(runtime_smoke.get("build_provenance_endpoint")),
        "leak_scan_passed": leak_scan.get("status") == "passed" and leak_scan.get("forbidden_markers") == [],
        "cleanup_complete": cleanup.get("container_removed") is True,
    }


def build_frontend_packaged_runtime_smoke_readiness(
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fail-closed #17 packaged frontend runtime smoke readiness verdict."""
    evidence = evidence or {}
    missing_fields = _missing_fields(evidence)
    environment_blockers = _classify_build_environment_blockers(evidence) if evidence else []
    checks = _build_checks(evidence) if evidence else {
        "docker_build_succeeded": False,
        "commit_sha_format_valid": False,
        "image_tag_matches_commit": False,
        "image_revision_matches_commit": False,
        "build_provenance_matches_commit": False,
        "compose_service_named_frontend": False,
        "compose_container_named_ai_platform_frontend": False,
        "compose_port_18001_bound": False,
        "compose_service_running": False,
        "healthz_ok": False,
        "index_ok": False,
        "auth_login_ok": False,
        "logged_in_chat_ok": False,
        "composer_visible": False,
        "composer_usable": False,
        "api_proxy_ok": False,
        "build_provenance_endpoint_ok": False,
        "leak_scan_passed": False,
        "cleanup_complete": False,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    blockers: list[str] = []
    status = "ready_for_operator_review"
    if not evidence:
        status = "blocked_missing_runtime_evidence"
        blockers.append("packaged_frontend_runtime_smoke_evidence_missing")
    elif environment_blockers:
        status = "blocked_environment"
        blockers.extend(environment_blockers)
    elif missing_fields or failed_checks:
        status = "blocked_incomplete_runtime_evidence"
        blockers.extend(f"missing_{field}" for field in missing_fields)
        blockers.extend(f"failed_{check}" for check in failed_checks)

    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": status,
        "evidence_contract": {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "required_fields": list(REQUIRED_EVIDENCE_FIELDS),
            "accepted_leak_scan_statuses": ["passed"],
            "accepted_cleanup_status": "container_removed_true",
            "write_path": "frontend_release.packaged_runtime_smoke.<commit_sha>",
        },
        "operator_commands": list(OPERATOR_COMMANDS),
        "runtime_policy": "docker_capable_host_only_no_local_windows_docker",
        "does_not_close_g6_g9_or_21": True,
        "formal_frontend_compose_runtime_required": True,
        "missing_evidence_fields": missing_fields,
        "checks": checks,
        "blockers": list(dict.fromkeys(blockers)),
        "closed_evidence_items": _closed_evidence_items(evidence, status),
    }


def render_frontend_packaged_runtime_smoke_markdown(readiness: dict[str, Any]) -> str:
    """Render packaged frontend runtime smoke readiness as operator-readable Markdown."""
    blockers = "\n".join(f"- `{blocker}`" for blocker in readiness["blockers"]) or "- none"
    missing = "\n".join(f"- `{field}`" for field in readiness["missing_evidence_fields"]) or "- none"
    commands = "\n".join(f"- `{command}`" for command in readiness["operator_commands"])
    closed_items = "\n".join(f"- `{item}`" for item in readiness["closed_evidence_items"]) or "- none"
    checks = "\n".join(
        f"- `{name}`: `{str(value).lower()}`" for name, value in readiness["checks"].items()
    )
    return (
        "# ai-platform Packaged Frontend Runtime Smoke\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Evidence contract: `{readiness['evidence_contract']['schema_version']}`\n\n"
        "## Blockers\n\n"
        f"{blockers}\n\n"
        "## Missing Evidence\n\n"
        f"{missing}\n\n"
        "## Checks\n\n"
        f"{checks}\n\n"
        "## Closed Evidence Items\n\n"
        f"{closed_items}\n\n"
        "## Operator Commands\n\n"
        f"{commands}\n"
    )


def _load_evidence(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence_json_must_be_object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify #17 packaged frontend runtime smoke evidence.")
    parser.add_argument("--evidence-json", help="Optional JSON evidence file captured from a Docker-capable host.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    try:
        evidence = _load_evidence(args.evidence_json)
    except OSError:
        print("frontend_packaged_runtime_smoke_error: evidence_json_unreadable", file=sys.stderr)
        raise SystemExit(2)
    except (json.JSONDecodeError, ValueError):
        print("frontend_packaged_runtime_smoke_error: invalid_evidence_json", file=sys.stderr)
        raise SystemExit(2)

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_frontend_packaged_runtime_smoke_markdown(readiness))


if __name__ == "__main__":
    main()
