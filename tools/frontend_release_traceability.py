import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA_VERSION = "ai-platform.frontend-release-traceability.v1"
FRONTEND_PATH = Path("frontend/web")
WORKFLOW_PATH = Path(".github/workflows/ai-platform-frontend.yml")
FRONTEND_DOCKERFILE_PATH = Path("frontend/web/Dockerfile")
FRONTEND_NGINX_TEMPLATE_PATH = Path("frontend/web/nginx.conf.template")
FRONTEND_COMPOSE_OVERLAY_PATH = Path("deploy/ai-platform/docker-compose.frontend.yml")
DIST_BUILD_PROVENANCE_FILENAME = "ai-platform-build-provenance.json"
CI_COMMANDS = [
    "corepack pnpm install --frozen-lockfile",
    "corepack pnpm run ci:verify",
]
DIST_REMEDIATION_COMMANDS = [
    "cd frontend/web",
    "corepack pnpm run ci:verify",
    "python ../../tools/frontend_release_traceability.py --format json",
]
WORKFLOW_COMMANDS = [
    "corepack pnpm install --frozen-lockfile",
    "python -m pip install pytest",
    "python -m pytest tests/test_deploy_frontend_static.py -q --basetemp .pytest-tmp",
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
WORKFLOW_PATH_FILTERS = [
    "frontend/web/**",
    "docs/frontend/**",
    "docs/operations/frontend-static-release-deploy.md",
    "deploy/ai-platform/docker-compose.frontend.yml",
    "tests/test_deploy_frontend_static.py",
    "tests/test_frontend_*.py",
    "tools/deploy_frontend_static.py",
    "tools/frontend_projection_audit.py",
    "tools/frontend_release_traceability.py",
    "tools/frontend_packaged_runtime_smoke.py",
    ".github/workflows/ai-platform-frontend.yml",
]
PACKAGED_DELIVERY_PATHS = [
    FRONTEND_DOCKERFILE_PATH,
    FRONTEND_NGINX_TEMPLATE_PATH,
    FRONTEND_COMPOSE_OVERLAY_PATH,
]
PACKAGED_DELIVERY_FORBIDDEN_TERMS = [
    ".env",
    "ANTHROPIC_AUTH_TOKEN",
    "API_KEY",
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "POSTGRES_PASSWORD",
    "RAGFLOW_API_KEY",
    "S3_SECRET_ACCESS_KEY",
    "SANDBOX_CALLBACK_TOKEN",
    "SANDBOX_WORKSPACE_ROOT",
    "TRUSTED_PRINCIPAL_SECRET",
    "env_file",
    "executor_private_payload",
    "raw_payload",
    "sandbox_workdir",
    "sandbox_workspace_root",
    "storage_key",
]
PACKAGED_DELIVERY_REQUIRED_TERMS = {
    FRONTEND_DOCKERFILE_PATH: {
        "dockerfile_build_commit_arg_required": "ARG AI_PLATFORM_BUILD_COMMIT=unknown",
        "dockerfile_build_dirty_arg_required": "ARG AI_PLATFORM_BUILD_DIRTY=unknown",
        "dockerfile_build_commit_env_required": "ENV AI_PLATFORM_BUILD_COMMIT=${AI_PLATFORM_BUILD_COMMIT}",
        "dockerfile_build_dirty_env_required": "ENV AI_PLATFORM_BUILD_DIRTY=${AI_PLATFORM_BUILD_DIRTY}",
        "dockerfile_revision_label_required": "org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT",
        "dockerfile_source_revision_label_required": "ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT",
        "dockerfile_ci_verify_required": "corepack pnpm run ci:verify",
        "dockerfile_tools_context_required": "COPY tools ./tools",
        "dockerfile_dist_copy_required": "COPY --from=build /workspace/frontend/web/dist",
    },
    FRONTEND_NGINX_TEMPLATE_PATH: {
        "nginx_api_upstream_required": "proxy_pass ${AI_PLATFORM_API_UPSTREAM}",
        "nginx_upload_body_size_required": "client_max_body_size ${AI_PLATFORM_FRONTEND_MAX_BODY_SIZE}",
        "nginx_proxy_read_timeout_required": "proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT}",
        "nginx_proxy_send_timeout_required": "proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT}",
        "nginx_proxy_request_buffering_off_required": "proxy_request_buffering off",
        "nginx_spa_fallback_required": 'try_files $uri $uri/ /index.html',
    },
    FRONTEND_COMPOSE_OVERLAY_PATH: {
        "compose_frontend_dockerfile_required": "dockerfile: frontend/web/Dockerfile",
        "compose_build_commit_args_required": "AI_PLATFORM_BUILD_COMMIT",
        "compose_build_dirty_args_required": "AI_PLATFORM_BUILD_DIRTY",
        "compose_api_upstream_required": "AI_PLATFORM_API_UPSTREAM",
        "compose_frontend_proxy_limits_required": "AI_PLATFORM_FRONTEND_MAX_BODY_SIZE",
        "compose_frontend_proxy_read_timeout_required": "AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT",
        "compose_frontend_proxy_send_timeout_required": "AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _git_dirty(repo_root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def _source_snapshot_identity(repo_root: Path) -> dict[str, object] | None:
    marker_path = repo_root / ".ai-platform-source-snapshot.json"
    marker = _load_json(marker_path) if marker_path.exists() else None
    if not marker:
        revision_path = repo_root / ".ai-platform-source-revision"
        try:
            revision = revision_path.read_text(encoding="utf-8").strip()
        except OSError:
            revision = ""
        if not revision:
            return None
        return {"commit": revision, "dirty": None, "source": "source_revision_marker"}

    commit = marker.get("source_tree_commit_sha")
    dirty = marker.get("source_tree_dirty")
    if not isinstance(commit, str) or not commit.strip():
        return None
    return {
        "commit": commit.strip(),
        "dirty": dirty if isinstance(dirty, bool) else None,
        "source": "source_snapshot_marker",
    }


def _git_identity(repo_root: Path) -> dict[str, object]:
    git_root = _git_value(repo_root, "rev-parse", "--show-toplevel")
    if git_root:
        try:
            if Path(git_root).resolve() == repo_root.resolve():
                commit = _git_value(repo_root, "rev-parse", "HEAD") or "unknown"
                return {"commit": commit, "dirty": _git_dirty(repo_root), "source": "git"}
        except OSError:
            pass

    return _source_snapshot_identity(repo_root) or {
        "commit": "unknown",
        "dirty": None,
        "source": "unknown",
    }


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _dist_provenance_status(
    provenance: dict[str, object] | None,
    *,
    git_commit: str,
    source_dirty: bool | None,
    package_json_sha256: str,
    pnpm_lock_sha256: str,
) -> dict[str, object]:
    blockers: list[str] = []
    if provenance is None:
        return {
            "status": "missing",
            "verified_same_commit": False,
            "build_commit": None,
            "blockers": ["dist_build_provenance_missing"],
        }

    git = provenance.get("git") if isinstance(provenance.get("git"), dict) else {}
    source_hashes = (
        provenance.get("source_hashes") if isinstance(provenance.get("source_hashes"), dict) else {}
    )
    build_commit = git.get("commit")
    if not isinstance(build_commit, str) or build_commit == "unknown" or git_commit == "unknown":
        blockers.append("dist_build_commit_unknown")
    elif build_commit != git_commit:
        blockers.append("dist_build_commit_mismatch")
    if source_dirty is True:
        blockers.append("source_tree_dirty")
    elif source_dirty is None:
        blockers.append("source_tree_dirty_state_unknown")
    dirty = git.get("dirty")
    if dirty is True:
        blockers.append("dist_built_from_dirty_worktree")
    elif dirty is not False:
        blockers.append("dist_build_dirty_state_unknown")
    if source_hashes.get("package_json_sha256") != package_json_sha256:
        blockers.append("dist_package_json_hash_mismatch")
    if source_hashes.get("pnpm_lock_sha256") != pnpm_lock_sha256:
        blockers.append("dist_pnpm_lock_hash_mismatch")

    return {
        "status": "verified" if not blockers else "mismatch",
        "verified_same_commit": not blockers,
        "build_commit": build_commit if isinstance(build_commit, str) else None,
        "blockers": blockers,
    }


def _dist_manifest(
    dist_root: Path,
    *,
    git_commit: str,
    source_dirty: bool | None,
    package_json_sha256: str,
    pnpm_lock_sha256: str,
) -> dict[str, object]:
    index_html = dist_root / "index.html"
    provenance_path = dist_root / DIST_BUILD_PROVENANCE_FILENAME
    provenance = _load_json(provenance_path) if provenance_path.exists() else None
    provenance_status = _dist_provenance_status(
        provenance,
        git_commit=git_commit,
        source_dirty=source_dirty,
        package_json_sha256=package_json_sha256,
        pnpm_lock_sha256=pnpm_lock_sha256,
    )
    status = "missing"
    if index_html.exists():
        status = "built" if provenance_status["verified_same_commit"] else "built_unverified"
    manifest: dict[str, object] = {
        "status": status,
        "artifact_kind": "static_dist",
        "artifact_scope": "ignored_local_build_output",
        "index_html_present": index_html.exists(),
        "file_count": 0,
        "total_bytes": 0,
        "manifest_sha256": None,
        "entrypoints": {},
        "build_provenance": {
            "path": f"dist/{DIST_BUILD_PROVENANCE_FILENAME}",
            **provenance_status,
        },
        "release_trace": {
            "frontend_artifact": "static_dist_manifest",
            "backend_worker_commit": git_commit,
            "policy": "same_git_commit_for_api_worker_frontend_artifacts",
            "verified_same_commit": bool(provenance_status["verified_same_commit"]),
        },
        "blockers": list(provenance_status["blockers"]),
        "remediation_commands": DIST_REMEDIATION_COMMANDS if provenance_status["blockers"] else [],
    }
    if not dist_root.exists():
        return manifest

    file_records: list[dict[str, object]] = []
    total_bytes = 0
    for item in sorted(path for path in dist_root.rglob("*") if path.is_file()):
        relative_path = item.relative_to(dist_root).as_posix()
        size = item.stat().st_size
        total_bytes += size
        file_records.append(
            {
                "path": relative_path,
                "size": size,
                "sha256": _sha256(item),
            }
        )

    digest = hashlib.sha256()
    for record in file_records:
        digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")

    entrypoints: dict[str, str] = {}
    if index_html.exists():
        entrypoints["index_html_sha256"] = _sha256(index_html)
    service_worker = dist_root / "sw.js"
    if service_worker.exists():
        entrypoints["service_worker_sha256"] = _sha256(service_worker)

    manifest.update(
        {
            "file_count": len(file_records),
            "total_bytes": total_bytes,
            "manifest_sha256": digest.hexdigest(),
            "entrypoints": entrypoints,
        }
    )
    return manifest


def _workflow_manifest(workflow_path: Path) -> dict[str, object]:
    missing_commands: list[str] = []
    missing_path_filters: list[str] = []
    if workflow_path.exists():
        workflow_content = workflow_path.read_text(encoding="utf-8")
        missing_commands = [command for command in WORKFLOW_COMMANDS if command not in workflow_content]
        missing_path_filters = [path_filter for path_filter in WORKFLOW_PATH_FILTERS if path_filter not in workflow_content]
    blockers = []
    if missing_commands:
        blockers.append("frontend_workflow_enforced_commands_missing")
    if missing_path_filters:
        blockers.append("frontend_workflow_path_filters_missing")
    status = "missing"
    if workflow_path.exists():
        status = "present_with_policy_gaps" if blockers else "present"
    manifest: dict[str, object] = {
        "path": WORKFLOW_PATH.as_posix(),
        "status": status,
        "sha256": _sha256(workflow_path) if workflow_path.exists() else None,
        "enforced_commands": WORKFLOW_COMMANDS,
        "missing_commands": missing_commands,
        "required_path_filters": WORKFLOW_PATH_FILTERS,
        "missing_path_filters": missing_path_filters,
        "blockers": blockers,
    }
    return manifest


def _packaged_delivery_contract_scan(root: Path) -> dict[str, object]:
    findings: list[dict[str, str]] = []
    contract_findings: list[dict[str, str]] = []
    scanned_files: list[str] = []
    for relative_path in PACKAGED_DELIVERY_PATHS:
        path = root / relative_path
        if not path.exists():
            continue
        scanned_files.append(relative_path.as_posix())
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            findings.append({"path": relative_path.as_posix(), "term": "unreadable"})
            continue
        normalized_content = content.lower()
        for term in PACKAGED_DELIVERY_FORBIDDEN_TERMS:
            if term.lower() in normalized_content:
                findings.append({"path": relative_path.as_posix(), "term": term})
        for rule_id, required_term in PACKAGED_DELIVERY_REQUIRED_TERMS.get(relative_path, {}).items():
            if required_term not in content:
                contract_findings.append({"path": relative_path.as_posix(), "rule_id": rule_id})
        if relative_path == FRONTEND_NGINX_TEMPLATE_PATH and (
            "proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT}" not in content
            or "proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT}" not in content
        ):
            contract_findings.append(
                {"path": relative_path.as_posix(), "rule_id": "nginx_proxy_timeouts_required"}
            )
    return {
        "status": "fail" if findings or contract_findings else "pass",
        "scanned_files": scanned_files,
        "forbidden_findings": findings,
        "contract_findings": contract_findings,
    }


def _packaged_frontend_image_manifest(root: Path, *, git_commit: str) -> dict[str, object]:
    dockerfile_path = root / FRONTEND_DOCKERFILE_PATH
    compose_overlay_path = root / FRONTEND_COMPOSE_OVERLAY_PATH
    dockerfile_present = dockerfile_path.exists()
    compose_overlay_present = compose_overlay_path.exists()
    contract_scan = _packaged_delivery_contract_scan(root)
    blockers: list[str] = []
    if not dockerfile_present:
        blockers.append("packaged_frontend_dockerfile_missing")
    if not compose_overlay_present:
        blockers.append("packaged_frontend_compose_overlay_missing")
    if contract_scan["status"] != "pass":
        blockers.append("packaged_frontend_contract_scan_failed")
    if blockers:
        blockers.append("packaged_frontend_image_trace_missing")
    status = "not_configured"
    if dockerfile_present and compose_overlay_present:
        status = "configured_with_policy_gaps" if contract_scan["status"] != "pass" else "configured"

    return {
        "artifact_kind": "frontend_static_image",
        "status": status,
        "dockerfile": {
            "path": FRONTEND_DOCKERFILE_PATH.as_posix(),
            "status": "present" if dockerfile_present else "missing",
            "sha256": _sha256(dockerfile_path) if dockerfile_present else None,
        },
        "compose_overlay": {
            "path": FRONTEND_COMPOSE_OVERLAY_PATH.as_posix(),
            "status": "present" if compose_overlay_present else "missing",
            "sha256": _sha256(compose_overlay_path) if compose_overlay_present else None,
        },
        "release_trace": {
            "frontend_artifact": "frontend_static_image",
            "backend_worker_commit": git_commit,
            "policy": "same_git_commit_for_api_worker_frontend_artifacts",
        },
        "contract_scan": contract_scan,
        "blockers": blockers,
    }


def build_frontend_release_traceability(repo_root: Path | None = None) -> dict[str, Any]:
    """Build a secret-safe same-commit frontend release traceability snapshot."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    frontend_root = root / FRONTEND_PATH
    package_json_path = frontend_root / "package.json"
    pnpm_lock_path = frontend_root / "pnpm-lock.yaml"
    dist_root = frontend_root / "dist"
    workflow_path = root / WORKFLOW_PATH

    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    package_json_sha256 = _sha256(package_json_path)
    pnpm_lock_sha256 = _sha256(pnpm_lock_path)
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    selected_scripts = {
        name: scripts[name]
        for name in ("lint", "build", "projection:audit", "ci:verify")
        if isinstance(scripts.get(name), str)
    }
    git_identity = _git_identity(root)
    git_commit = str(git_identity.get("commit") or "unknown")
    source_dirty = git_identity.get("dirty") if isinstance(git_identity.get("dirty"), bool) else None

    return {
        "schema_version": SCHEMA_VERSION,
        "frontend_path": FRONTEND_PATH.as_posix(),
        "package_name": str(package_json.get("name") or ""),
        "package_version": str(package_json.get("version") or ""),
        "package_manager": str(package_json.get("packageManager") or ""),
        "git": {
            **git_identity,
        },
        "source_hashes": {
            "package_json_sha256": package_json_sha256,
            "pnpm_lock_sha256": pnpm_lock_sha256,
        },
        "scripts": selected_scripts,
        "commands": CI_COMMANDS,
        "workflow": _workflow_manifest(workflow_path),
        "dist": _dist_manifest(
            dist_root,
            git_commit=git_commit,
            source_dirty=source_dirty,
            package_json_sha256=package_json_sha256,
            pnpm_lock_sha256=pnpm_lock_sha256,
        ),
        "packaged_frontend_image": _packaged_frontend_image_manifest(root, git_commit=git_commit),
        "release_policy": "tie_frontend_api_worker_artifacts_to_same_git_commit",
    }


def render_frontend_release_traceability_markdown(trace: dict[str, Any]) -> str:
    """Render frontend release traceability metadata as operator-readable Markdown."""
    hashes = trace["source_hashes"]
    scripts = trace["scripts"]
    commands = "\n".join(f"- `{command}`" for command in trace["commands"])
    workflow_commands = "\n".join(f"- `{command}`" for command in trace["workflow"]["enforced_commands"])
    script_rows = "\n".join(f"| `{name}` | `{value}` |" for name, value in scripts.items())
    packaged_image = trace["packaged_frontend_image"]
    blockers = "\n".join(f"- `{blocker}`" for blocker in packaged_image["blockers"]) or "- none"
    return (
        "# ai-platform Frontend Release Traceability\n\n"
        f"Schema: `{trace['schema_version']}`\n\n"
        f"Frontend path: `{trace['frontend_path']}`\n\n"
        f"Package: `{trace['package_name']}@{trace['package_version']}`\n\n"
        f"Package manager: `{trace['package_manager']}`\n\n"
        f"Git commit: `{trace['git']['commit']}`\n\n"
        f"Git dirty: `{str(trace['git']['dirty']).lower()}`\n\n"
        "## Source Hashes\n\n"
        f"- `package_json_sha256`: `{hashes['package_json_sha256']}`\n"
        f"- `pnpm_lock_sha256`: `{hashes['pnpm_lock_sha256']}`\n\n"
        "## CI Commands\n\n"
        f"{commands}\n\n"
        "## Workflow\n\n"
        f"- path: `{trace['workflow']['path']}`\n"
        f"- status: `{trace['workflow']['status']}`\n"
        f"- sha256: `{trace['workflow']['sha256']}`\n\n"
        "Workflow enforced commands:\n\n"
        f"{workflow_commands}\n\n"
        "## Scripts\n\n"
        "| Script | Command |\n"
        "| --- | --- |\n"
        f"{script_rows}\n\n"
        "## Dist Status\n\n"
        f"- status: `{trace['dist']['status']}`\n"
        f"- artifact_scope: `{trace['dist']['artifact_scope']}`\n"
        f"- index_html_present: `{str(trace['dist']['index_html_present']).lower()}`\n"
        f"- artifact_kind: `{trace['dist']['artifact_kind']}`\n"
        f"- file_count: `{trace['dist']['file_count']}`\n"
        f"- total_bytes: `{trace['dist']['total_bytes']}`\n"
        f"- manifest_sha256: `{trace['dist']['manifest_sha256']}`\n\n"
        "Build provenance:\n\n"
        f"- path: `{trace['dist']['build_provenance']['path']}`\n"
        f"- status: `{trace['dist']['build_provenance']['status']}`\n"
        f"- build_commit: `{trace['dist']['build_provenance']['build_commit']}`\n"
        f"- verified_same_commit: "
        f"`{str(trace['dist']['build_provenance']['verified_same_commit']).lower()}`\n\n"
        "Remediation commands:\n\n"
        + (
            "\n".join(f"- `{command}`" for command in trace["dist"].get("remediation_commands", []))
            or "- none"
        )
        + "\n\n"
        "## Packaged Frontend Image\n\n"
        f"- status: `{packaged_image['status']}`\n"
        f"- artifact_kind: `{packaged_image['artifact_kind']}`\n"
        f"- dockerfile: `{packaged_image['dockerfile']['path']}` "
        f"(`{packaged_image['dockerfile']['status']}`)\n"
        f"- compose_overlay: `{packaged_image['compose_overlay']['path']}` "
        f"(`{packaged_image['compose_overlay']['status']}`)\n"
        f"- backend_worker_commit: `{packaged_image['release_trace']['backend_worker_commit']}`\n\n"
        "Blockers:\n\n"
        f"{blockers}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print frontend release traceability metadata.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    trace = build_frontend_release_traceability()
    if args.format == "json":
        print(json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_frontend_release_traceability_markdown(trace))


if __name__ == "__main__":
    main()
