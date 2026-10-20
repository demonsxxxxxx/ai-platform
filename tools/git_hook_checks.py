"""Run local feedback on the exact staged tree or pushed commit, never on drafts."""
from __future__ import annotations

import ast
from contextlib import contextmanager
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.analysis.source_quality_inventory import import_targets, module_name  # noqa: E402
from tools.check_documentation import (  # noqa: E402
    ENTRYPOINTS, check_documents, historical, local_target, markdown_facts,
)

from tools.run_test_stage import _filesystem_path  # noqa: E402

SHA = re.compile(r"[0-9a-f]{40}\Z")
ZERO = "0" * 40


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, input=data, timeout=30)


def changed_paths(root: Path, *revision_args: str) -> list[str]:
    raw = git(root, "diff", "--no-renames", "--name-only", "--diff-filter=ACMRD", "-z", *revision_args, "--")
    return sorted(value.decode("utf-8") for value in raw.split(b"\0") if value)


@contextmanager
def snapshot(root: Path, tree: str, head: str):
    # An independent repository keeps test-created refs/indexes away from the user's Git state.
    with tempfile.TemporaryDirectory(prefix="ap-hook-", dir=_filesystem_path(Path(tempfile.gettempdir()))) as directory:
        target = Path(directory) / "source"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(root), "source"],
                       cwd=directory, check=True, timeout=30)
        git(target, "update-ref", "HEAD", head)
        git(target, "read-tree", tree)
        with tarfile.open(fileobj=io.BytesIO(git(root, "archive", "--format=tar", tree))) as archive:
            archive.extractall(target, filter="data")
        yield target


def affected_tests(root: Path, changed: list[str]) -> list[str]:
    """Trace static imports and literal path consumers; CI still owns complete validation."""
    paths = [p.relative_to(root).as_posix() for directory in ("app", "tools", "scripts", "tests")
             for p in (root / directory).rglob("*.py") if "__pycache__" not in p.parts]
    modules = {module_name(path): path for path in paths}
    changed_modules = {module_name(path) for path in changed if path.endswith(".py")}
    known = set(modules) | changed_modules
    affected = set(changed_modules)
    edges = {}
    text = {}
    for module, path in modules.items():
        text[path] = (root / path).read_text(encoding="utf-8-sig")
        tree = ast.parse(text[path], filename=path)
        edges[module] = {target for target, _ in import_targets(tree, module, path.endswith("/__init__.py"), known)}
    while True:
        expanded = affected | {module for module, targets in edges.items() if targets & affected}
        if expanded == affected:
            break
        affected = expanded
    all_tests = sorted(path for path in paths if path.startswith("tests/") and Path(path).name.startswith("test_"))
    if any(path in changed for path in ("tests/conftest.py", "pyproject.toml", "uv.lock")):
        return all_tests
    return [path for path in all_tests if module_name(path) in affected
            or any(value in text[path] for value in changed)]


def link_frontend_dependencies(original: Path, tree: Path) -> None:
    source = original / "frontend/web/node_modules"
    target = tree / "frontend/web/node_modules"
    if not source.is_dir():
        raise RuntimeError("prepare frontend/web dependencies with pnpm install --frozen-lockfile")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(source), str(target))
    else:
        target.symlink_to(source, target_is_directory=True)


def checks(original: Path, tree: Path, changed: list[str], *, push: bool) -> None:
    python_files = [path for path in changed if path.endswith(".py") and (tree / path).is_file()]
    if python_files:
        subprocess.run([sys.executable, "-m", "ruff", "check", "--isolated", "--", *python_files],
                       cwd=tree, check=True, timeout=60)
    documents = [path for path in changed if path.endswith(".md")
                 and (path in ENTRYPOINTS or path.startswith("docs/")) and (tree / path).is_file()]
    deleted = {(tree / path).resolve() for path in changed if not (tree / path).exists()}
    if deleted:
        candidates = {*(tree / "docs").rglob("*.md"), *(tree / path for path in ENTRYPOINTS)}
        for document in candidates:
            relative = document.relative_to(tree).as_posix()
            if not document.is_file() or historical(relative):
                continue
            links, _ = markdown_facts(document.read_text(encoding="utf-8"))
            if any((target := local_target(tree, document, href)) and target[0] in deleted for _, href in links):
                documents.append(relative)
    findings, _ = check_documents(tree, documents)
    if findings:
        raise RuntimeError("\n".join(findings))
    frontend = [path.removeprefix("frontend/web/") for path in changed
                if path.startswith("frontend/web/") and path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs"))
                and (tree / path).is_file()]
    frontend_changed = any(path.startswith("frontend/web/")
                           and path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", "package.json", "pnpm-lock.yaml"))
                           for path in changed)
    if frontend or (push and frontend_changed):
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node is required for changed frontend code")
        link_frontend_dependencies(original, tree)
        # Node cannot resolve a relative entry point from a native-prefixed Windows cwd.
        web = str(tree / "frontend/web")
        if web.startswith("\\\\?\\"):
            web = "\\\\" + web[8:] if web.startswith("\\\\?\\UNC\\") else web[4:]
        if frontend:
            subprocess.run([node, "node_modules/eslint/bin/eslint.js", "--", *frontend],
                           cwd=web, check=True, timeout=120)
        if push:
            subprocess.run([node, "node_modules/typescript/bin/tsc", "-b"], cwd=web, check=True, timeout=180)
    if push:
        selectors = affected_tests(tree, changed)
        if selectors:
            print("pre-push affected tests: " + ", ".join(selectors), flush=True)
            subprocess.run([sys.executable, "tools/run_test_stage.py", "--stage", "pre-push",
                            "--timeout-seconds", "1800", "--", *selectors], cwd=tree, check=True)


def push_ranges(root: Path, records: str) -> list[tuple[str, str]]:
    ranges = set()
    for record in records.splitlines():
        fields = record.split()
        if len(fields) != 4 or not SHA.fullmatch(fields[1]) or not SHA.fullmatch(fields[3]):
            raise ValueError("invalid Git pre-push input")
        _, local, _, remote = fields
        if local == ZERO:
            continue
        if remote == ZERO:
            try:
                remote = git(root, "merge-base", local, "refs/remotes/origin/main").decode().strip()
            except subprocess.CalledProcessError:
                remote = git(root, "hash-object", "-t", "tree", "--stdin", data=b"").decode().strip()
        ranges.add((remote, local))
    return sorted(ranges)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if not args or args[0] not in {"pre-commit", "pre-push"}:
            raise ValueError("choose pre-commit or pre-push")
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        if args[0] == "pre-commit":
            subprocess.run(["git", "diff", "--cached", "--check"], cwd=root, check=True, timeout=30)
            changed = changed_paths(root, "--cached")
            if changed:
                tree = git(root, "write-tree").decode().strip()
                head = git(root, "rev-parse", "HEAD").decode().strip()
                with snapshot(root, tree, head) as selected:
                    checks(root, selected, changed, push=False)
        else:
            for base, head in push_ranges(root, sys.stdin.read()):
                changed = changed_paths(root, base, head)
                with snapshot(root, head, head) as selected:
                    checks(root, selected, changed, push=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"git_hook_check_failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
