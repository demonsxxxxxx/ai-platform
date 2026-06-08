from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"


def test_frontend_ci_workflow_enforces_projection_audit_build_and_traceability():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "corepack pnpm install --frozen-lockfile" in workflow
    assert "corepack pnpm run ci:verify" in workflow
    assert "python tools/frontend_release_traceability.py --format json" in workflow
    assert "frontend/web/**" in workflow
    assert "docs/frontend/**" in workflow
    assert "tests/test_frontend_*.py" in workflow
    assert "tools/frontend_projection_audit.py" in workflow
    assert "tools/frontend_release_traceability.py" in workflow

    ci_verify_index = workflow.index("corepack pnpm run ci:verify")
    traceability_index = workflow.index("python tools/frontend_release_traceability.py --format json")
    assert ci_verify_index < traceability_index

    lower = workflow.lower()
    assert "docker" not in lower
    assert "compose" not in lower
    assert "secret" not in lower
    assert ".env" not in lower
    assert "c:\\users" not in lower
