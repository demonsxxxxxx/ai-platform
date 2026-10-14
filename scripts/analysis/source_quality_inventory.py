"""Inventory tracked source structure; this is not a governance or coverage gate."""

from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tokenize

import yaml


def module_name(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def import_targets(tree: ast.AST, module: str, package: bool, known: set[str]):
    """Resolve static imports to known modules, including relative imports."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = module.split(".") if package else module.split(".")[:-1]
                if node.level > len(parent):
                    continue
                base = ".".join(parent[:len(parent) - node.level + 1] + base.split("."))
                base = base.rstrip(".")
            for alias in node.names:
                child = f"{base}.{alias.name}"
                target = child if child in known else base
                if target in known:
                    yield target, node.lineno


def peer_score(edges: list[dict]) -> dict:
    internal = sum(not edge["public_boundary"] for edge in edges)
    return {
        "cross_context_edges": len(edges), "internal_cross_context_edges": internal,
        "public_peer_percent": round(100 * (len(edges) - internal) / len(edges), 1) if edges else None,
    }


def workflow_test_inventory(root: Path, paths: list[str], untracked: list[str] | None = None) -> dict:
    """Inventory literal CI selectors; this is not collection or execution proof."""
    def tests_in(values):
        return {path for path in values if path.startswith("tests/") and Path(path).name.startswith("test_") and path.endswith(".py")}

    tracked_tests = tests_in(paths)
    untracked_tests = tests_in(untracked or []) - tracked_tests
    candidates = tracked_tests | untracked_tests
    rows = []
    selectors = set()
    whole_files = set()
    pattern = re.compile(r"\btests/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_./:-]+)?")
    for relative in sorted(path for path in paths if path.startswith(".github/workflows/") and Path(path).suffix in {".yml", ".yaml"}):
        raw = (root / relative).read_bytes()
        pending = [yaml.safe_load(raw)]
        references = set()
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"run", "test_files"} and isinstance(child, str):
                        references.update(pattern.findall(child))
                    elif isinstance(child, (dict, list)):
                        pending.append(child)
            elif isinstance(value, list):
                pending.extend(value)
        selectors.update(references)
        whole_files.update(item for item in references if "::" not in item)
        rows.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "literal_selectors": sorted(references)})
    referenced = {item.split("::", 1)[0] for item in selectors}
    return {
        "scope": "Literal Python test references in workflow run/test_files fields; not pytest collection, indirect imports, dynamic selectors, or observed CI.",
        "tracked_test_files": len(tracked_tests),
        "untracked_test_files": sorted(untracked_tests),
        "candidate_test_files": len(candidates),
        "literal_referenced_candidate_files": len(candidates & referenced),
        "unreferenced_tracked_files": sorted(tracked_tests - referenced),
        "unreferenced_candidate_files": sorted(candidates - referenced),
        "node_only_referenced_files": sorted(referenced - whole_files),
        "missing_literal_targets": sorted(referenced - candidates),
        "workflows": rows,
    }


def build_inventory(root: Path, paths: list[str]) -> dict:
    """Measure supplied tracked paths without importing product code."""
    policy_bytes = (root / "architecture-policy.json").read_bytes()
    policy = json.loads(policy_bytes)
    contexts = set(policy["bounded_contexts"])
    layers = set(policy["layers"])
    public = set(policy["public_cross_domain_modules"])
    sources = sorted(p for p in paths if p.startswith("app/") and p.endswith(".py"))
    modules = {module_name(p): p for p in sources}
    known = set(modules)
    parsed = {}
    records = []
    errors = []
    digest = hashlib.sha256()
    for module, path in sorted(modules.items()):
        source_path = root / path
        if not source_path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"source path leaves repository: {path}")
        raw = source_path.read_bytes()
        digest.update(path.encode() + b"\0" + raw + b"\0")
        try:
            with tokenize.open(source_path) as source_file:
                text = source_file.read()
            tree = ast.parse(text, filename=path)
        except (SyntaxError, UnicodeError) as error:
            errors.append({"path": path, "error": type(error).__name__, "line": getattr(error, "lineno", None)})
            continue
        parsed[module] = tree
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        records.append({
            "module": module, "path": path, "lines": len(text.splitlines()),
            "functions": len(functions),
            "max_function_span": max((node.end_lineno - node.lineno + 1 for node in functions), default=0),
        })

    edges = {}
    for module, tree in parsed.items():
        for target, line in import_targets(tree, module, modules[module].endswith("/__init__.py"), known):
            key = (module, target)
            edges[key] = min(edges.get(key, line), line)
    dependencies = {module: set() for module in parsed}
    for module, target in edges:
        dependencies[module].add(target)
    cycle_sample = []
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError as error:
        # graphlib traverses dependents; report importer -> imported instead.
        cycle_sample = list(reversed(error.args[1]))

    test_paths = sorted(p for p in paths if p.startswith("tests/") and Path(p).name.startswith("test_") and p.endswith(".py"))
    references = {context: set() for context in contexts}
    for path in test_paths:
        try:
            with tokenize.open(root / path) as test_file:
                tree = ast.parse(test_file.read(), filename=path)
        except (SyntaxError, UnicodeError) as error:
            errors.append({"path": path, "error": type(error).__name__, "line": getattr(error, "lineno", None)})
            continue
        for target, _ in import_targets(tree, module_name(path), False, known):
            parts = target.split(".")
            if len(parts) > 1 and parts[1] in contexts:
                references[parts[1]].add(path)

    cross_edges = []
    for (module, target), line in sorted(edges.items()):
        owner, peer = module.split(".")[1:2], target.split(".")[1:2]
        if owner and peer and owner[0] in contexts and peer[0] in contexts and owner != peer:
            target_parts = target.split(".")
            cross_edges.append({
                "source": module, "target": target, "path": modules[module], "line": line,
                "public_boundary": len(target_parts) == 3 and target_parts[2] in public,
            })

    scores = []
    layer_counts = Counter()
    for context in sorted(contexts):
        owned = [record for record in records if record["path"].startswith(f"app/{context}/") and not record["path"].endswith("/__init__.py")]
        canonical = 0
        for record in owned:
            role = Path(record["path"]).parts[2]
            is_canonical = role in layers or role in {"api.py", "events.py", "registry.py"}
            canonical += is_canonical
            layer_counts[role if role in layers else "public" if is_canonical else "legacy"] += 1
        peers = [edge for edge in cross_edges if edge["source"].startswith(f"app.{context}.")]
        scores.append({
            "context": context, "modules": len(owned), "canonical_modules": canonical,
            "layout_percent": round(100 * canonical / len(owned), 1) if owned else None,
            **peer_score(peers),
            "direct_test_import_files": len(references[context]),
        })
    layer_peers = {role: [] for role in (*sorted(layers), "public", "legacy")}
    for edge in cross_edges:
        role = Path(modules[edge["source"]]).parts[2]
        role = role if role in layers else "public" if role in {"api.py", "events.py", "registry.py"} else "legacy"
        layer_peers[role].append(edge)

    frontend = sorted(p for p in paths if p.startswith("frontend/web/src/") and Path(p).suffix in {".ts", ".tsx", ".js", ".jsx"})
    frontend_tests = [p for p in frontend if ".test." in p or ".spec." in p]
    frontend_test_code = {p for p in frontend if "/__tests__/" in p} | set(frontend_tests)
    frontend_production = [p for p in frontend if p not in frontend_test_code]
    frontend_sizes = [{"path": p, "lines": len((root / p).read_text(encoding="utf-8").splitlines())} for p in frontend_production]
    return {
        "schema_version": "ai-platform.source-quality-inventory.v1",
        "scope": "Tracked app Python and frontend/web/src sources. Static imports include conditional/type-only edges; direct test imports are not coverage.",
        "source_digest": digest.hexdigest(),
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "totals": {"app_python_files": len(sources), "app_lines": sum(r["lines"] for r in records), "app_functions": sum(r["functions"] for r in records), "backend_test_files": len(test_paths), "frontend_production_files": len(frontend_production), "frontend_test_files": len(frontend_tests), "frontend_test_support_files": len(frontend_test_code) - len(frontend_tests), "frontend_production_lines": sum(r["lines"] for r in frontend_sizes)},
        "contexts": scores, "layers": dict(sorted(layer_counts.items())),
        "layer_peer_scores": [{"layer": role, **peer_score(edges)} for role, edges in layer_peers.items()],
        "internal_cross_context_imports": [edge for edge in cross_edges if not edge["public_boundary"]],
        "static_import_edges": [{"source": source, "target": target, "line": line} for (source, target), line in sorted(edges.items())],
        "static_cycle_sample": cycle_sample,
        "top_app_files": sorted(records, key=lambda r: (-r["lines"], r["path"]))[:10],
        "top_frontend_files": sorted(frontend_sizes, key=lambda r: (-r["lines"], r["path"]))[:10],
        "parse_errors": errors,
    }


def main() -> int:
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    report = build_inventory(root, paths)
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "tests"], cwd=root).decode("utf-8").split("\0")
    report["workflow_test_inventory"] = workflow_test_inventory(root, paths, untracked)
    report["source_ref"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    report["tracked_app_changes"] = subprocess.check_output(["git", "diff", "HEAD", "--name-only", "--", "app"], cwd=root, text=True).splitlines()
    report["generator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["generated_at_beijing"] = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
