from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"
PYTEST_COMMAND = (
    "python -m pytest tests/test_deploy_frontend_static.py "
    "tests/test_frontend_release_traceability.py "
    "tests/test_frontend_packaged_runtime_smoke.py "
    "tests/test_frontend_ci_workflow.py "
    "tests/test_backend_ci_workflow.py "
    "tests/test_packaging_publish_workflow.py "
    "tests/test_release_image_manifest.py "
    "tests/test_release_authority.py "
    "tests/test_runtime_launch_script.py "
    "tests/test_source_authority_docs.py "
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
    assert "tests/test_governance_readiness.py" not in workflow
    assert "python tools/deploy_frontend_static.py --help" in workflow
    assert "corepack pnpm run ci:verify" in workflow
    assert "python tools/frontend_release_traceability.py --format json" in workflow
    assert "python tools/frontend_packaged_runtime_smoke.py --format json" in workflow
    assert "docker build" in workflow
    assert '--build-arg AI_PLATFORM_BUILD_COMMIT="$IMAGE_SOURCE_COMMIT"' in workflow
    assert "--build-arg AI_PLATFORM_BUILD_DIRTY=false" in workflow
    assert '--build-arg AI_PLATFORM_BUILD_REPOSITORY="$IMAGE_SOURCE_REPOSITORY"' in workflow
    assert "-f frontend/web/Dockerfile" in workflow
    assert "docker run --rm --entrypoint cat" in workflow
    assert "ai-platform-build-provenance.json" in workflow
    assert "paths:" not in workflow.split("workflow_dispatch:", 1)[0]
    assert workflow.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}") == 2
    assert workflow.count("persist-credentials: false") == 2
    assert "IMAGE_SOURCE_HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}" in workflow
    assert "- name: Resolve image source repository" in workflow
    assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then' in workflow
    assert '[[ "$IMAGE_SOURCE_HEAD_REPOSITORY" =~ ^[A-Za-z0-9]' in workflow
    assert 'image_source_repository="https://github.com/${IMAGE_SOURCE_HEAD_REPOSITORY}.git"' in workflow
    assert 'printf \'IMAGE_SOURCE_REPOSITORY=%s\\n\' "$image_source_repository" >> "$GITHUB_ENV"' in workflow
    assert "IMAGE_SOURCE_REPOSITORY: https://github.com/${{ github.repository }}.git" not in workflow
    assert "if ((git rev-parse HEAD) -ne $env:SOURCE_COMMIT) { exit 1 }" in workflow
    assert 'labels["org.opencontainers.image.revision"]' in workflow
    assert 'labels["ai-platform.source-commit"] == os.environ["IMAGE_SOURCE_COMMIT"]' in workflow
    assert 'labels["ai-platform.source-repository"]' in workflow
    assert "packaged_frontend_image_id=%s" in workflow
    assert "docker run --detach --name" in workflow
    assert "--env AI_PLATFORM_API_UPSTREAM=http://127.0.0.1:8020" in workflow
    assert "frontend_container_state=" in workflow
    assert "docker logs --tail 80" in workflow
    assert "frontend_redacted_container_log_tail_lines=" in workflow
    assert '"nginx_upstream_resolution"' in workflow
    assert 'print(f"frontend_container_log_signal={signal}")' in workflow
    assert "| sed -E" not in workflow
    assert "http://127.0.0.1:18080/healthz" in workflow

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
