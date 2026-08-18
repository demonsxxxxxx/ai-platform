import importlib.metadata
from pathlib import Path
import tomllib
from unittest.mock import patch

import pytest
import yaml

from tests.support.yaml_contracts import load_unique_yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"
BACKEND_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-backend.yml"
LOCK = ROOT / "uv.lock"
PYTEST_COMMAND = (
    "python -m pytest tests/test_deploy_frontend_static.py "
    "tests/test_frontend_release_traceability.py "
    "tests/test_frontend_packaged_runtime_smoke.py "
    "tests/test_frontend_ci_workflow.py "
    "tests/test_require_zero_junit_skips.py "
    "tests/test_source_authority_docs.py "
    "-q --basetemp .pytest-tmp"
)
LINUX_PYTEST_COMMAND = (
    "python -m pytest tests/test_frontend_linux_contracts.py -q --basetemp .pytest-tmp"
)
PYTHON_TEST_DEPENDENCIES = "python -m pip install pytest pyyaml"
JSONSCHEMA_CONTRACT_START = "          import importlib.metadata"
JSONSCHEMA_CONTRACT_END = "          '@ | python -"
BACKEND_OWNED_STATIC_SUITES = {
    "tests/test_backend_ci_workflow.py",
    "tests/test_packaging_publish_workflow.py",
    "tests/test_release_image_manifest.py",
    "tests/test_release_authority.py",
    "tests/test_runtime_launch_script.py",
}


def _workflow_contract(path: Path = WORKFLOW) -> dict[str, object]:
    payload = load_unique_yaml(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _required_contract_namespace() -> dict[str, object]:
    workflow = _workflow_contract()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    required = jobs["required"]
    assert isinstance(required, dict)
    steps = required["steps"]
    assert isinstance(steps, list)
    contract_steps = [
        step
        for step in steps
        if step.get("name") == "Require frontend verification and image provenance"
    ]
    assert len(contract_steps) == 1
    run = contract_steps[0]["run"]
    assert isinstance(run, str)
    marker = "python - <<'PY'\n"
    assert run.startswith(marker)
    source, terminator = run.removeprefix(marker).rsplit("\nPY", 1)
    assert terminator.strip() == ""
    namespace: dict[str, object] = {"__name__": "workflow_contract"}
    exec(source, namespace)
    return namespace


def _jsonschema_contract_namespace() -> dict[str, object]:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index(JSONSCHEMA_CONTRACT_START)
    end = workflow.index(JSONSCHEMA_CONTRACT_END, start)
    source = "\n".join(
        line.removeprefix("          ") for line in workflow[start:end].splitlines()
    )
    namespace: dict[str, object] = {"__name__": "workflow_contract"}
    with patch("subprocess.check_call"):
        exec(source, namespace)
    return namespace


def _locked_jsonschema_version(lock_path: Path) -> str:
    with lock_path.open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    jsonschema_packages = [package for package in packages if package["name"] == "jsonschema"]
    assert len(jsonschema_packages) == 1
    return jsonschema_packages[0]["version"]


def test_frontend_ci_workflow_derives_and_verifies_jsonschema_from_lock_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m pip install pytest pyyaml jsonschema" not in workflow
    assert 'python -c "import jsonschema"' in workflow
    assert 'f"jsonschema=={locked_jsonschema}"' in workflow

    namespace = _jsonschema_contract_namespace()
    locked_version = namespace["locked_jsonschema_version"]
    verify_installed = namespace["verify_installed_jsonschema_version"]
    assert callable(locked_version)
    assert callable(verify_installed)
    expected_locked_version = _locked_jsonschema_version(LOCK)
    assert locked_version(LOCK) == expected_locked_version

    missing_lock = tmp_path / "missing.lock"
    missing_lock.write_text('version = 1\n[[package]]\nname = "pytest"\nversion = "9.0.2"\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="exactly one jsonschema"):
        locked_version(missing_lock)

    invalid_lock = tmp_path / "invalid.lock"
    invalid_lock.write_text(
        'version = 1\n[[package]]\nname = "jsonschema"\nversion = "untrusted"\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="canonical numeric version"):
        locked_version(invalid_lock)

    duplicate_lock = tmp_path / "duplicate.lock"
    duplicate_lock.write_text(
        f'version = 1\n[[package]]\nname = "jsonschema"\nversion = "{expected_locked_version}"\n'
        f'[[package]]\nname = "jsonschema"\nversion = "{expected_locked_version}"\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="exactly one jsonschema"):
        locked_version(duplicate_lock)

    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.0.0")
    with pytest.raises(RuntimeError, match="installed jsonschema version mismatch"):
        verify_installed(expected_locked_version)


def test_frontend_ci_workflow_enforces_projection_audit_build_and_traceability():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    backend_workflow = BACKEND_WORKFLOW.read_text(encoding="utf-8")
    contract = _workflow_contract()
    assert contract["on"] == {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main"]},
        "workflow_dispatch": "",
    }
    jobs = contract["jobs"]
    assert isinstance(jobs, dict)
    assert jobs["frontend"]["runs-on"] == "windows-latest"
    assert jobs["frontend-image"]["runs-on"] == "ubuntu-latest"
    required = jobs["required"]
    assert isinstance(required, dict)
    assert required["name"] == "frontend required"
    assert required["needs"] == ["frontend", "frontend-image"]
    assert required["if"] == "${{ always() }}"
    image_job = workflow.split("  frontend-image:", 1)[1].split("  required:", 1)[0]
    required_job = workflow.split("  required:", 1)[1]
    assert "IMAGE_BASE_COMMIT: ${{ github.event.pull_request.base.sha }}" in image_job
    assert "fetch-depth: 0" in image_job
    assert "- name: Determine frontend image impact" in image_job
    assert "python tools/ci_image_scope.py" in image_job
    assert '--event-name "$GITHUB_EVENT_NAME"' in image_job
    assert '--role frontend' in image_job
    assert '--base-ref "$IMAGE_BASE_COMMIT"' in image_job
    assert '--head-ref "$IMAGE_SOURCE_COMMIT"' in image_job
    assert "- name: Report frontend image validation not affected" in image_job
    assert "if: steps.image-scope.outputs.build != 'true'" in image_job
    assert "reason=packaged_inputs_unchanged" in image_job
    assert "base_commit=%s head_commit=%s" in image_job
    for step_name in [
        "Set up Python for Linux contracts",
        "Install Linux contract test dependencies",
        "Verify Linux frontend healthcheck contract",
        "Resolve image source repository",
        "Build frontend image",
        "Block fixable frontend image vulnerabilities",
        "Verify image build provenance",
        "Verify frontend image startup",
    ]:
        step = image_job.split(f"- name: {step_name}", 1)[1].split("\n      - name:", 1)[0]
        assert "if: steps.image-scope.outputs.build == 'true'" in step
    assert "IMAGE_RESULT: ${{ needs.frontend-image.result }}" in required_job
    assert "IMAGE_DISPOSITION" not in required_job
    for job_name in [*required["needs"], "required"]:
        job = jobs[job_name]
        assert isinstance(job, dict)
        assert "continue-on-error" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step

    assert "corepack pnpm install --frozen-lockfile" in workflow
    assert PYTHON_TEST_DEPENDENCIES in workflow
    assert "locked_jsonschema_version(Path(\"uv.lock\"))" in workflow
    assert "verify_installed_jsonschema_version(locked_jsonschema)" in workflow
    assert PYTEST_COMMAND in workflow
    assert LINUX_PYTEST_COMMAND in workflow
    for suite in BACKEND_OWNED_STATIC_SUITES:
        assert suite not in PYTEST_COMMAND
        assert suite not in workflow
        assert suite in backend_workflow
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
    assert "- name: Block fixable frontend image vulnerabilities" in workflow
    assert (
        "uses: aquasecurity/trivy-action@"
        "ed142fd0673e97e23eac54620cfb913e5ce36c25"
    ) in workflow
    assert "image-ref: ai-platform-frontend:${{ env.IMAGE_SOURCE_COMMIT }}" in workflow
    assert "severity: HIGH,CRITICAL" in workflow
    assert "ignore-unfixed: true" in workflow
    assert "exit-code: '1'" in workflow
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

    pytest_install_index = workflow.index(PYTHON_TEST_DEPENDENCIES)
    collection_dependency_import_index = workflow.index(
        "verify_installed_jsonschema_version(locked_jsonschema)"
    )
    jsonschema_import_index = workflow.index('python -c "import jsonschema"')
    deploy_test_index = workflow.index(PYTEST_COMMAND)
    linux_test_index = workflow.index(LINUX_PYTEST_COMMAND)
    ci_verify_index = workflow.index("corepack pnpm run ci:verify")
    traceability_index = workflow.index("python tools/frontend_release_traceability.py --format json")
    assert pytest_install_index < deploy_test_index
    assert collection_dependency_import_index < deploy_test_index
    assert pytest_install_index < collection_dependency_import_index
    assert collection_dependency_import_index < jsonschema_import_index < deploy_test_index
    assert deploy_test_index < ci_verify_index
    assert ci_verify_index < traceability_index
    assert traceability_index < linux_test_index

    expected_split_steps = (
        "      - name: Verify static frontend Python contracts\n"
        f"        run: {PYTEST_COMMAND}\n\n"
        "      - name: Verify static frontend deploy helper\n"
        "        run: python tools/deploy_frontend_static.py --help"
    )
    assert expected_split_steps in workflow
    expected_linux_steps = (
        "      - name: Install Linux contract test dependencies\n"
        "        if: steps.image-scope.outputs.build == 'true'\n"
        "        run: python -m pip install pytest\n\n"
        "      - name: Verify Linux frontend healthcheck contract\n"
        "        if: steps.image-scope.outputs.build == 'true'\n"
        f"        run: {LINUX_PYTEST_COMMAND}"
    )
    assert expected_linux_steps in workflow

    lower = workflow.lower()
    assert "docker compose" not in lower
    assert "secret" not in lower
    assert "deploy/ai-platform/.env\"" not in lower
    assert "deploy/ai-platform/.env'" not in lower
    assert "c:\\users" not in lower


def test_frontend_ci_workflow_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yml"
    duplicate.write_text(
        "name: frontend\non:\n  push:\n    branches: [main]\non:\n  pull_request:\n    branches: [main]\n",
        encoding="utf-8",
    )

    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key 'on'"):
        _workflow_contract(duplicate)


@pytest.mark.parametrize("job_name", ["frontend", "frontend-image"])
@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", ""])
def test_frontend_required_contract_executes_failure_paths(job_name: str, result: str) -> None:
    namespace = _required_contract_namespace()
    require_successful_results = namespace["require_successful_results"]
    assert callable(require_successful_results)
    results = {"frontend": "success", "frontend-image": "success"}
    results[job_name] = result

    with pytest.raises(RuntimeError, match=job_name):
        require_successful_results(results)


def test_frontend_required_contract_accepts_only_all_success() -> None:
    namespace = _required_contract_namespace()
    require_successful_results = namespace["require_successful_results"]
    assert callable(require_successful_results)

    require_successful_results({"frontend": "success", "frontend-image": "success"})
