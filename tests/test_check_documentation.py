from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("documentation_checker_under_test", ROOT / "tools/check_documentation.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def documents(root: Path, sources: dict[str, str]) -> None:
    for relative, source in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(("link", "code"), [
    ("missing.md", "link_target_missing"),
    ("target.md#removed-heading", "link_anchor_missing"),
    ("../../outside.md", "link_outside_repository"),
])
def test_actual_local_link_violation_is_actionable(tmp_path, link, code):
    documents(tmp_path, {
        "docs/README.md": "[owner](owner.md)\n",
        "docs/owner.md": f"# Owner\n\nStatus: active contract\n\n[broken]({link})\n",
        "docs/target.md": "# Valid heading\n",
    })
    findings, excluded = checker.check_documents(tmp_path, ["docs/owner.md"])
    assert len(findings) == 1 and code in findings[0]
    assert "docs/owner.md:" in findings[0]
    assert excluded == []


def test_reference_links_unicode_duplicates_and_inline_code_headings(tmp_path):
    documents(tmp_path, {
        "docs/README.md": "[owner](owner.md)\n",
        "docs/owner.md": "# Owner\n\nStatus: active\n\n[one][target]\n\n[target]: target.md#部门-run\n\n[two](target.md#部门-run-1)\n\n[code](target.md#api)\n\n[html](target.md#named)\n",
        "docs/target.md": '# 部门 Run\n\n# 部门 Run\n\n## `api`\n\n<a id="named"></a>\n',
    })
    assert checker.check_documents(tmp_path, ["docs/owner.md"]) == ([], [])


def test_code_examples_are_not_links_and_fixed_history_is_preserved(tmp_path):
    documents(tmp_path, {
        "docs/README.md": "[owner](owner.md)\n",
        "docs/owner.md": "# Owner\n\nStatus: dated observation\n\n`[example](missing.md)`\n\n```md\n[example](missing.md)\n```\n\n[fixed history](https://github.com/example/repo/blob/0123456789/deleted.py)\n",
        "docs/HARNESS-RULES.md": "[upstream template](not-local.md)\n",
        "docs/release-evidence/old.md": "[fixed observation](deleted.py)\n",
    })
    findings, excluded = checker.check_documents(tmp_path, ["docs/owner.md", "docs/HARNESS-RULES.md", "docs/release-evidence/old.md"])
    assert findings == []
    assert excluded == ["docs/HARNESS-RULES.md", "docs/release-evidence/old.md"]


def test_missing_status_and_navigation_fail_independently(tmp_path):
    documents(tmp_path, {"docs/README.md": "# Index\n", "docs/orphan.md": "# Orphan\n"})
    findings, _ = checker.check_documents(tmp_path, ["docs/orphan.md"])
    assert len(findings) == 2
    assert any("document_status_missing" in finding for finding in findings)
    assert any("document_unindexed" in finding for finding in findings)
    documents(tmp_path, {"docs/orphan.md": "# Orphan\n\nStatus: proposed\n"})
    findings, _ = checker.check_documents(tmp_path, ["docs/orphan.md"])
    assert len(findings) == 1 and "document_unindexed" in findings[0]
    documents(tmp_path, {"docs/README.md": "[orphan](orphan.md)\n"})
    assert checker.check_documents(tmp_path, ["docs/orphan.md"]) == ([], [])


def test_navigation_can_reuse_an_existing_owner_and_file_line_links(tmp_path):
    documents(tmp_path, {
        "docs/README.md": "[owner](owner.md)\n",
        "docs/owner.md": "[plan](plans/new.md)\n",
        "docs/plans/new.md": "# New\n\n状态：提案\n\n[source](../../source.py:5)\n",
        "source.py": "value = 1\n",
    })
    assert checker.check_documents(tmp_path, ["docs/plans/new.md"]) == ([], [])


@pytest.mark.parametrize("path", ["../external.md", "app/product.md", "docs/missing.md"])
def test_invalid_scope_or_missing_document_fails(tmp_path, path):
    findings, _ = checker.check_documents(tmp_path, [path])
    assert len(findings) == 1
    assert "document_path_invalid" in findings[0] or "document_missing" in findings[0]


def test_status_inside_a_code_example_does_not_satisfy_metadata(tmp_path):
    documents(tmp_path, {"docs/README.md": "[owner](owner.md)\n", "docs/owner.md": "# Owner\n\n```md\nStatus: example only\n```\n"})
    findings, _ = checker.check_documents(tmp_path, ["docs/owner.md"])
    assert len(findings) == 1 and "document_status_missing" in findings[0]


def test_commit_comparison_finds_documents_in_a_clean_checkout(tmp_path):
    documents(tmp_path, {"docs/README.md": "# Index\n"})
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init", "-b", "main")
    git("config", "user.name", "Documentation Test")
    git("config", "user.email", "documentation@example.test")
    git("add", "docs/README.md")
    git("commit", "-m", "fixture")
    base = git("rev-parse", "HEAD")
    documents(tmp_path, {"docs/new.md": "# New\n\nStatus: proposed\n", "docs/README.md": "[new](new.md)\n"})
    git("add", "docs")
    git("commit", "-m", "candidate fixture")
    assert checker.changed_documents(tmp_path) == []
    assert checker.changed_documents(tmp_path, base) == ["docs/README.md", "docs/new.md"]
    with pytest.raises(ValueError):
        checker.changed_documents(tmp_path, "main")


def test_behind_main_comparison_uses_the_fork_point_not_incoming_base_changes(tmp_path):
    documents(tmp_path, {"docs/shared.md": "# Shared\n"})
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init", "-b", "main")
    git("config", "user.name", "Documentation Test")
    git("config", "user.email", "documentation@example.test")
    git("add", "docs")
    git("commit", "-m", "common fixture")
    git("switch", "-c", "candidate")
    documents(tmp_path, {"docs/candidate.md": "# Candidate\n\nStatus: proposed\n"})
    git("add", "docs")
    git("commit", "-m", "candidate fixture")
    git("switch", "main")
    documents(tmp_path, {"docs/shared.md": "# Shared\n\nChanged only on incoming main.\n"})
    git("add", "docs")
    git("commit", "-m", "incoming base fixture")
    new_base = git("rev-parse", "HEAD")
    git("switch", "candidate")
    assert checker.changed_documents(tmp_path, new_base) == ["docs/candidate.md"]


def test_cli_fails_for_real_violations_without_network(tmp_path):
    documents(tmp_path, {"docs/README.md": "# Index\n", "docs/new.md": "# New\n"})
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    completed = subprocess.run([sys.executable, str(ROOT / "tools/check_documentation.py"), "docs/new.md"], cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 1
    assert "document_status_missing" in completed.stdout
    assert "document_unindexed" in completed.stdout
