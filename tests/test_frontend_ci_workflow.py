from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"


def test_frontend_ci_workflow_enforces_projection_audit_build_and_traceability():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "corepack pnpm install --frozen-lockfile" in workflow
    assert "python -m pip install pytest" in workflow
    assert "python -m pytest tests/test_deploy_frontend_static.py -q --basetemp .pytest-tmp" in workflow
    assert "python tools/deploy_frontend_static.py --help" in workflow
    assert "corepack pnpm run ci:verify" in workflow
    assert "python tools/frontend_release_traceability.py --format json" in workflow
    assert "python tools/frontend_packaged_runtime_smoke.py --format json" in workflow
    assert "docker build" in workflow
    assert "--build-arg AI_PLATFORM_BUILD_COMMIT=${{ github.sha }}" in workflow
    assert "--build-arg AI_PLATFORM_BUILD_DIRTY=false" in workflow
    assert "-f frontend/web/Dockerfile" in workflow
    assert "docker run --rm --entrypoint cat" in workflow
    assert "ai-platform-build-provenance.json" in workflow
    assert "frontend/web/**" in workflow
    assert "docs/frontend/**" in workflow
    assert "docs/operations/frontend-static-release-deploy.md" in workflow
    assert "deploy/ai-platform/docker-compose.frontend.yml" in workflow
    assert "tests/test_deploy_frontend_static.py" in workflow
    assert "tests/test_frontend_*.py" in workflow
    assert "tools/deploy_frontend_static.py" in workflow
    assert "tools/frontend_projection_audit.py" in workflow
    assert "tools/frontend_release_traceability.py" in workflow
    assert "tools/frontend_packaged_runtime_smoke.py" in workflow

    pytest_install_index = workflow.index("python -m pip install pytest")
    deploy_test_index = workflow.index("python -m pytest tests/test_deploy_frontend_static.py")
    ci_verify_index = workflow.index("corepack pnpm run ci:verify")
    traceability_index = workflow.index("python tools/frontend_release_traceability.py --format json")
    assert pytest_install_index < deploy_test_index
    assert deploy_test_index < ci_verify_index
    assert ci_verify_index < traceability_index

    lower = workflow.lower()
    assert "docker compose" not in lower
    assert "secret" not in lower
    assert ".env" not in lower
    assert "c:\\users" not in lower
