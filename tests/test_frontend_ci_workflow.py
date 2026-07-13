from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"
PYTEST_COMMAND = (
    "python -m pytest tests/test_deploy_frontend_static.py "
    "tests/test_frontend_release_traceability.py "
    "tests/test_frontend_packaged_runtime_smoke.py "
    "tests/test_frontend_ci_workflow.py "
    "tests/test_backend_ci_workflow.py "
    "tests/test_release_authority.py "
    "tests/test_runtime_launch_script.py "
    "tests/test_source_authority_docs.py "
    "tests/test_governance_readiness.py "
    "-q --basetemp .pytest-tmp"
)


def test_frontend_ci_workflow_enforces_projection_audit_build_and_traceability():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    pull_request_block = workflow.split("pull_request:", 1)[1].split("push:", 1)[0]
    push_block = workflow.split("push:", 1)[1].split("workflow_dispatch:", 1)[0]
    assert "branches:" in pull_request_block
    assert "- main" in pull_request_block
    assert "paths:" not in pull_request_block
    assert "branches:" in push_block
    assert "- main" in push_block
    assert "paths:" not in push_block
    assert "name: frontend required" in workflow
    assert "needs: [frontend, frontend-image]" in workflow
    assert "if: ${{ always() }}" in workflow

    assert "corepack pnpm install --frozen-lockfile" in workflow
    assert "python -m pip install pytest pyyaml" in workflow
    assert PYTEST_COMMAND in workflow
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
    assert "paths:" not in workflow.split("workflow_dispatch:", 1)[0]

    pytest_install_index = workflow.index("python -m pip install pytest pyyaml")
    deploy_test_index = workflow.index(PYTEST_COMMAND)
    ci_verify_index = workflow.index("corepack pnpm run ci:verify")
    traceability_index = workflow.index("python tools/frontend_release_traceability.py --format json")
    assert pytest_install_index < deploy_test_index
    assert deploy_test_index < ci_verify_index
    assert ci_verify_index < traceability_index

    expected_split_steps = (
        "      - name: Verify static frontend Python contracts\n"
        f"        run: {PYTEST_COMMAND}\n\n"
        "      - name: Verify static frontend deploy helper\n"
        "        run: python tools/deploy_frontend_static.py --help"
    )
    assert expected_split_steps in workflow

    lower = workflow.lower()
    assert "docker compose" not in lower
    assert "secret" not in lower
    assert "deploy/ai-platform/.env\"" not in lower
    assert "deploy/ai-platform/.env'" not in lower
    assert "c:\\users" not in lower
