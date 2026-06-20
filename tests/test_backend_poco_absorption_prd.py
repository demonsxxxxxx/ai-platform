from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs/superpowers/specs/2026-06-20-ai-platform-backend-poco-claw-absorption-prd.md"
PLAN = ROOT / "docs/superpowers/plans/2026-06-20-ai-platform-backend-poco-claw-absorption.md"
EXPECTED_PR_FILES = {
    "docs/superpowers/plans/2026-06-20-ai-platform-backend-poco-claw-absorption.md",
    "docs/superpowers/specs/2026-06-20-ai-platform-backend-poco-claw-absorption-prd.md",
    "tests/test_backend_poco_absorption_prd.py",
}


def read_prd() -> str:
    return PRD.read_text(encoding="utf-8")


def read_plan() -> str:
    return PLAN.read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def changed_paths() -> set[str]:
    commands = (
        ("git", "diff", "--name-only", "origin/main...HEAD"),
        ("git", "diff", "--name-only", "--cached"),
        ("git", "ls-files", "--others", "--exclude-standard"),
    )
    paths: set[str] = set()
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        paths.update(line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip())
    return paths


def test_backend_poco_absorption_prd_records_scope_and_authority_boundaries():
    text = read_prd()
    compact_text = compact(text)

    for required_section in (
        "# Backend poco-claw Absorption PRD",
        "## 1. Source Authority And Evidence Basis",
        "## 2. Absorption Scope",
        "## 3. Backend Capability Mapping",
        "## 4. Acceptance Gates",
        "## 5. Non-Goals And Rejection Rules",
        "## 6. Single-PR Delivery Contract",
        "## 7. Review And Verification Requirements",
        "## 8. Implementation Plan Link",
    ):
        assert required_section in text

    for boundary in (
        "This PRD is backend-only.",
        "Frontend UI absorption remains outside this PRD.",
        "poco-claw is a reference source, not ai-platform authority.",
        "No poco-claw code is copied or vendored by this PRD.",
        "No runtime dependency is introduced by this PRD.",
        "No platform-level multi-run agent harness is introduced.",
        "Claude Agent SDK remains the execution layer.",
        "`docs/superpowers/plans/2026-06-20-ai-platform-backend-poco-claw-absorption.md`",
        "GitHub issue #160",
        "PR #159",
    ):
        assert boundary in compact_text


def test_backend_poco_absorption_prd_records_openai_codex_workflow_evidence():
    text = read_prd()
    compact_text = compact(text)

    for official_basis in (
        "OpenAI Codex manual",
        "Worktrees",
        "Custom instructions with AGENTS.md",
        "Review",
        "Subagents",
    ):
        assert official_basis in text

    for workflow_rule in (
        "Use an isolated worktree for this PR.",
        "Follow repository `AGENTS.md` and project PRD authority.",
        "Keep one branch and one pull request for this issue.",
        "Record review evidence before merge.",
        "Use subagents only for bounded review or read-only analysis.",
        "Subagent review does not replace GitHub PR review evidence.",
    ):
        assert workflow_rule in compact_text


def test_backend_poco_absorption_prd_maps_poco_updates_to_backend_stages():
    text = read_prd()

    for poco_capability in (
        "persistent runtime registry",
        "idle timeout",
        "keepalive",
        "sleep",
        "stale runtime detection",
        "runtime-to-container binding",
        "internal executor-manager authentication",
        "session share backend contract",
        "file reference backend contract",
        "skill reference backend contract",
        "group-level skill selection",
    ):
        assert poco_capability in text

    for stage_mapping in (
        "| B1 |",
        "| B2 |",
        "| B3 |",
        "| B4 |",
        "| B5 |",
        "| B6 |",
    ):
        assert stage_mapping in text

    for ai_platform_authority in (
        "sandbox_leases",
        "worker queue",
        "Admin Runtime",
        "Skill release evidence",
        "file/artifact ACL",
        "exact tool permission",
        "tenant/workspace/user",
    ):
        assert ai_platform_authority in text


def test_backend_poco_absorption_prd_preserves_gate_claim_boundaries():
    text = read_prd()
    compact_text = compact(text)

    for status_label in (
        "`local partial`",
        "`PR ready`",
        "`reviewed`",
        "`merged`",
        "`211 verified`",
        "`gate closable`",
    ):
        assert status_label in text

    for claim_boundary in (
        "A docs-only absorption PR cannot create `211 verified` status.",
        "A reference implementation cannot close B2 sandbox hardening.",
        "Persistent runtime design cannot raise worker defaults.",
        "A slash or skill reference UI pattern cannot define backend Skill release authority.",
        "Session sharing design cannot expose public artifacts without B5 ACL and redaction evidence.",
        "The 10 sessions x peak 4 SDK subagents profile remains B3 evidence work.",
    ):
        assert claim_boundary in compact_text


def test_backend_poco_absorption_prd_pr_scope_is_docs_and_tests_only():
    assert changed_paths() <= EXPECTED_PR_FILES

    for relative_path in EXPECTED_PR_FILES:
        assert (ROOT / relative_path).exists()

    for relative_path in EXPECTED_PR_FILES:
        if relative_path.startswith("docs/"):
            assert relative_path.endswith(".md")
        else:
            assert relative_path.startswith("tests/")
            assert relative_path.endswith(".py")

    forbidden_prefixes = (
        "app/",
        "frontend/",
        "frontend/web/src/",
        "skills/",
        "deploy/",
        "scripts/",
        "tools/",
    )
    forbidden_suffixes = (
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    )

    for relative_path in EXPECTED_PR_FILES:
        assert not relative_path.startswith(forbidden_prefixes)
        assert not relative_path.endswith(forbidden_suffixes)


def test_backend_poco_absorption_implementation_plan_is_executable_and_backend_only():
    text = read_plan()
    compact_text = compact(text)

    for required_header in (
        "# Backend poco-claw Absorption Implementation Plan",
        "REQUIRED SUB-SKILL",
        "**Goal:**",
        "**Architecture:**",
        "**Tech Stack:**",
        "## Global Constraints",
        "## File Structure",
        "### Task 1: Complete The PRD Closure Loop",
        "### Task 2: Open B1 Context Snapshot Issue",
        "### Task 3: Open B2 Runtime Lifecycle Issue",
        "### Task 4: Open B3 Capacity Evidence Issue",
        "### Task 5: Open B4 Skill Reference And Group Issue",
        "### Task 6: Open B5 File Share And ACL Issue",
        "### Task 7: Defer B6 Operations Beta Packaging",
        "## Self-Review",
    ):
        assert required_header in text

    for issue_or_pr_marker in (
        "GitHub issue #160",
        "PR #159",
        "issue -> branch/PR -> local verification -> review evidence -> merge",
        "one PR",
        "single PR",
    ):
        assert issue_or_pr_marker in compact_text

    for stage in ("B1", "B2", "B3", "B4", "B5", "B6"):
        assert stage in text

    for non_goal in (
        "Do not modify frontend UI.",
        "Do not copy poco-claw code.",
        "Do not add runtime dependencies.",
        "Do not claim `211 verified` from this docs/test PR.",
        "Do not claim `gate closable` from this docs/test PR.",
    ):
        assert non_goal in compact_text

    for required_command in (
        "python -m pytest tests\\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\\poco-absorption-plan-green",
        "python -m pytest tests\\test_backend_phased_prd.py tests\\test_source_authority_docs.py tests\\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\\poco-absorption-plan-docs",
        "python -m compileall -q app tools scripts",
        "git diff --check",
    ):
        assert required_command in text
