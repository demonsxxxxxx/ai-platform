from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "source_quality_inventory_under_test", ROOT / "scripts/analysis/source_quality_inventory.py"
)
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def fixture_source(root: Path, sources: dict[str, str | bytes]) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "architecture-policy.json").write_text(json.dumps({
        "bounded_contexts": ["runs", "skills"],
        "layers": {name: {} for name in ("domain", "application", "infrastructure", "transport")},
        "public_cross_domain_modules": ["api", "events"],
    }), encoding="utf-8")
    for relative, content in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return list(sources)


def test_inventory_counts_public_private_edges_and_existing_test_imports(tmp_path):
    paths = fixture_source(tmp_path, {
        "app/runs/__init__.py": "",
        "app/runs/application/replay.py": "from app.skills.api import approved\nfrom app.skills.infrastructure.store import save\n",
        "app/runs/legacy.py": "flag = True\n",
        "app/skills/api.py": "def approved():\n    return True\n",
        "app/skills/infrastructure/store.py": "def save():\n    return None\n",
        "tests/test_replay_contract.py": "from app.runs.application import replay\n",
        "frontend/web/src/Preview.tsx": "export const Preview = null;\n",
        "frontend/web/src/__tests__/Preview.test.tsx": "export const expected = true;\n",
        "frontend/web/src/__tests__/support.ts": "export const fixture = {};\n",
    })
    report = inventory.build_inventory(tmp_path, paths)
    runs = next(row for row in report["contexts"] if row["context"] == "runs")
    assert runs["modules"] == 2
    assert runs["layout_percent"] == 50
    assert runs["cross_context_edges"] == 2
    assert runs["internal_cross_context_edges"] == 1
    assert runs["public_peer_percent"] == 50
    assert runs["direct_test_import_files"] == 1
    application = next(row for row in report["layer_peer_scores"] if row["layer"] == "application")
    assert application["public_peer_percent"] == 50
    assert application["internal_cross_context_edges"] == 1
    assert report["internal_cross_context_imports"][0]["target"] == "app.skills.infrastructure.store"
    assert report["totals"]["frontend_production_files"] == 1
    assert report["totals"]["frontend_test_files"] == 1
    assert report["totals"]["frontend_test_support_files"] == 1
    assert report["parse_errors"] == []


def test_relative_imports_and_static_cycles_are_reported_without_execution(tmp_path):
    paths = fixture_source(tmp_path, {
        "app/runs/one.py": "from .two import value\nraise RuntimeError('must not execute')\n",
        "app/runs/two.py": "from . import one\nvalue = 1\n",
    })
    report = inventory.build_inventory(tmp_path, paths)
    assert {(edge["source"], edge["target"]) for edge in report["static_import_edges"]} == {
        ("app.runs.one", "app.runs.two"), ("app.runs.two", "app.runs.one"),
    }
    assert set(report["static_cycle_sample"]) == {"app.runs.one", "app.runs.two"}
    assert report["static_cycle_sample"][0] == report["static_cycle_sample"][-1]
    skills = next(row for row in report["contexts"] if row["context"] == "skills")
    assert skills["layout_percent"] is None
    assert skills["public_peer_percent"] is None


def test_cycle_sample_follows_import_direction(tmp_path):
    paths = fixture_source(tmp_path, {
        "app/runs/one.py": "from .two import value\nvalue = 1\n",
        "app/runs/two.py": "from .three import value\nvalue = 1\n",
        "app/runs/three.py": "from .one import value\nvalue = 1\n",
    })
    report = inventory.build_inventory(tmp_path, paths)
    edges = {(edge["source"], edge["target"]) for edge in report["static_import_edges"]}
    cycle = report["static_cycle_sample"]
    assert len(cycle) == 4
    assert all(pair in edges for pair in zip(cycle, cycle[1:]))


def test_declared_python_encoding_is_used_and_syntax_errors_are_explicit(tmp_path):
    paths = fixture_source(tmp_path, {
        "app/runs/encoded.py": b'# coding: cp1252\nlabel = "caf\xe9"\n',
        "app/runs/broken.py": "def (: pass\n",
    })
    report = inventory.build_inventory(tmp_path, paths)
    assert report["parse_errors"] == [{"path": "app/runs/broken.py", "error": "SyntaxError", "line": 1}]
    assert report["top_app_files"][0]["path"] == "app/runs/encoded.py"
    assert report["totals"]["app_lines"] == 2


def test_workflow_inventory_reconciles_files_nodes_duplicates_and_untracked_tests(tmp_path):
    paths = fixture_source(tmp_path, {
        "tests/test_whole.py": "def test_whole():\n    pass\n",
        "tests/test_partial.py": "def test_one():\n    pass\n",
        "tests/test_orphan.py": "def test_orphan():\n    pass\n",
        ".github/workflows/backend.yml": "jobs:\n  test:\n    strategy:\n      matrix:\n        include:\n          - test_files: tests/test_whole.py tests/test_partial.py::test_one\n    steps:\n      - run: python -m pytest tests/test_whole.py tests/test_new.py\n",
        ".github/workflows/frontend.yml": "jobs:\n  test:\n    steps:\n      - run: python -m pytest tests/test_whole.py\n",
    })
    report = inventory.workflow_test_inventory(tmp_path, paths, ["tests/test_new.py", "tests/test_orphan.py"])
    assert report["tracked_test_files"] == 3
    assert report["candidate_test_files"] == 4
    assert report["literal_referenced_candidate_files"] == 3
    assert report["untracked_test_files"] == ["tests/test_new.py"]
    assert report["unreferenced_tracked_files"] == ["tests/test_orphan.py"]
    assert report["unreferenced_candidate_files"] == ["tests/test_orphan.py"]
    assert report["node_only_referenced_files"] == ["tests/test_partial.py"]
    assert report["missing_literal_targets"] == []
    assert all(len(row["sha256"]) == 64 for row in report["workflows"])


def test_workflow_inventory_ignores_env_metadata_and_marks_unresolvable_literals(tmp_path):
    paths = fixture_source(tmp_path, {
        "tests/test_unselected.py": "def test_one():\n    pass\n",
        ".github/workflows/test.yml": "env:\n  EXAMPLE: tests/test_unselected.py\njobs:\n  check:\n    steps:\n      - run: python -m pytest tests/test_missing.py\n",
    })
    report = inventory.workflow_test_inventory(tmp_path, paths)
    assert report["literal_referenced_candidate_files"] == 0
    assert report["unreferenced_tracked_files"] == ["tests/test_unselected.py"]
    assert report["missing_literal_targets"] == ["tests/test_missing.py"]


def test_workflow_inventory_distinguishes_absent_workflows_from_complete_selection(tmp_path):
    report = inventory.workflow_test_inventory(tmp_path, ["tests/test_one.py"])
    assert report["tracked_test_files"] == 1
    assert report["literal_referenced_candidate_files"] == 0
    assert report["workflows"] == []
    assert report["unreferenced_tracked_files"] == ["tests/test_one.py"]


def test_source_paths_cannot_escape_repository(tmp_path):
    root = tmp_path / "repository"
    fixture_source(root, {"app/runs/ok.py": "value = 1\n"})
    (tmp_path / "outside.py").write_text("private = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="leaves repository"):
        inventory.build_inventory(root, ["app/../../outside.py"])
