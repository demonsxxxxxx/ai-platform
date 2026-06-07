import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA_VERSION = "ai-platform.frontend-release-traceability.v1"
FRONTEND_PATH = Path("frontend/web")
CI_COMMANDS = [
    "corepack pnpm install --frozen-lockfile",
    "corepack pnpm run ci:verify",
]


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


def _dist_manifest(dist_root: Path, *, git_commit: str) -> dict[str, object]:
    index_html = dist_root / "index.html"
    status = "built" if index_html.exists() else "missing"
    manifest: dict[str, object] = {
        "status": status,
        "artifact_kind": "static_dist",
        "index_html_present": index_html.exists(),
        "file_count": 0,
        "total_bytes": 0,
        "manifest_sha256": None,
        "entrypoints": {},
        "release_trace": {
            "frontend_artifact": "static_dist_manifest",
            "backend_worker_commit": git_commit,
            "policy": "same_git_commit_for_api_worker_frontend_artifacts",
        },
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


def build_frontend_release_traceability(repo_root: Path | None = None) -> dict[str, Any]:
    """Build a secret-safe same-commit frontend release traceability snapshot."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    frontend_root = root / FRONTEND_PATH
    package_json_path = frontend_root / "package.json"
    pnpm_lock_path = frontend_root / "pnpm-lock.yaml"
    dist_root = frontend_root / "dist"

    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    selected_scripts = {
        name: scripts[name]
        for name in ("lint", "build", "projection:audit", "ci:verify")
        if isinstance(scripts.get(name), str)
    }
    git_commit = _git_value(root, "rev-parse", "HEAD") or "unknown"

    return {
        "schema_version": SCHEMA_VERSION,
        "frontend_path": FRONTEND_PATH.as_posix(),
        "package_name": str(package_json.get("name") or ""),
        "package_version": str(package_json.get("version") or ""),
        "package_manager": str(package_json.get("packageManager") or ""),
        "git": {
            "commit": git_commit,
            "dirty": _git_dirty(root),
        },
        "source_hashes": {
            "package_json_sha256": _sha256(package_json_path),
            "pnpm_lock_sha256": _sha256(pnpm_lock_path),
        },
        "scripts": selected_scripts,
        "commands": CI_COMMANDS,
        "dist": _dist_manifest(dist_root, git_commit=git_commit),
        "release_policy": "tie_frontend_api_worker_artifacts_to_same_git_commit",
    }


def render_frontend_release_traceability_markdown(trace: dict[str, Any]) -> str:
    """Render frontend release traceability metadata as operator-readable Markdown."""
    hashes = trace["source_hashes"]
    scripts = trace["scripts"]
    commands = "\n".join(f"- `{command}`" for command in trace["commands"])
    script_rows = "\n".join(f"| `{name}` | `{value}` |" for name, value in scripts.items())
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
        "## Scripts\n\n"
        "| Script | Command |\n"
        "| --- | --- |\n"
        f"{script_rows}\n\n"
        "## Dist Status\n\n"
        f"- status: `{trace['dist']['status']}`\n"
        f"- index_html_present: `{str(trace['dist']['index_html_present']).lower()}`\n"
        f"- artifact_kind: `{trace['dist']['artifact_kind']}`\n"
        f"- file_count: `{trace['dist']['file_count']}`\n"
        f"- total_bytes: `{trace['dist']['total_bytes']}`\n"
        f"- manifest_sha256: `{trace['dist']['manifest_sha256']}`\n"
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
