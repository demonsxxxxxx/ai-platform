import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path
import tomllib
from unittest.mock import patch

import pytest
import yaml

from tests.support.yaml_contracts import load_unique_yaml
from tools import frontend_ci_dependencies


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"
BACKEND_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-backend.yml"
LOCK = ROOT / "uv.lock"
PACKAGE = ROOT / "frontend" / "web" / "package.json"
PYTEST_COMMAND = (
    "python -m pytest tests/test_frontend_release_traceability.py "
    "tests/test_frontend_packaged_runtime_smoke.py "
    "tests/test_frontend_ci_workflow.py tests/test_frontend_ci_shards.py "
    "-q --basetemp .pytest-tmp"
)
LINUX_PYTEST_COMMAND = (
    "python -m pytest tests/test_frontend_linux_contracts.py -q --basetemp .pytest-tmp"
)
PYTHON_TEST_DEPENDENCIES = "python tools/frontend_ci_dependencies.py --subset windows-contracts"
JSONSCHEMA_CONTRACT_START = "          import importlib.metadata"
JSONSCHEMA_CONTRACT_END = "          '@ | python -"
BACKEND_OWNED_STATIC_SUITES = {
    "tests/test_backend_ci_workflow.py",
    "tests/test_packaging_publish_workflow.py",
    "tests/test_release_image_manifest.py",
    "tests/test_release_authority.py",
    "tests/test_runtime_launch_script.py",
    "tests/test_source_authority_docs.py",
}


REQUIRED_FRONTEND_JOBS = ("frontend", "frontend-tests", "frontend-contracts", "frontend-image")


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
    assert "python -m pip install pytest" not in workflow
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


def test_frontend_ci_runs_browser_auth_owner_regressions():
    scripts = json.loads(PACKAGE.read_text(encoding="utf-8"))["scripts"]
    assert "corepack pnpm run test:auth" in scripts["ci:verify"].split(" && ")
    assert scripts["test:auth"].split() == [
        "tsx", "--import", "./scripts/lobe-ui-test-setup.mjs",
        "--test", "--test-concurrency=2",
        "src/hooks/__tests__/browserAuthCoordinator.test.ts",
        "src/hooks/__tests__/browserAuthStorage.test.ts",
        "src/hooks/__tests__/useAuth.test.ts",
        "src/hooks/__tests__/useAuthProvider.test.tsx",
        "src/services/api/__tests__/auth.test.ts",
        "src/services/api/__tests__/token.test.ts",
        "src/services/api/__tests__/authLogging.test.ts",
        "src/services/api/__tests__/download.test.ts",
        "src/services/api/__tests__/authenticatedRequest.test.ts",
        "src/components/auth/__tests__/PasswordInput.test.tsx",
    ]
    for selector in scripts["test:auth"].split()[5:]:
        assert (PACKAGE.parent / selector).is_file()


def test_frontend_ci_workflow_enforces_projection_audit_build_and_traceability():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    backend_workflow = BACKEND_WORKFLOW.read_text(encoding="utf-8")
    test_sse = json.loads(PACKAGE.read_text(encoding="utf-8"))["scripts"]["test:sse"]
    assert (
        "tsx --import ./scripts/lobe-ui-test-setup.mjs --test --test-concurrency=2 "
        "src/hooks/useAgent/__tests__/useAgentRoutedSession.test.tsx "
        "src/hooks/useAgent/__tests__/conversationContinuation.test.ts"
        in test_sse.split(" && ")
    )
    assert "src/hooks/useAgent/__tests__/historyLoader.test.ts" in test_sse
    for selector in (
        "src/components/chat/__tests__/composerCommandParity.test.ts",
        "src/components/layout/AppContent/__tests__/useSessionSync.test.ts",
        "src/components/layout/AppContent/__tests__/chatViewMessageListKey.test.ts",
        "src/hooks/__tests__/useSession.test.ts",
        "src/hooks/useAgent/__tests__/runControlLifecycle.test.ts",
        "src/hooks/useAgent/runControlLifecycle.test.ts",
        "src/hooks/useAgent/__tests__/runLifecycle.test.ts",
        "src/hooks/useAgent/__tests__/stopGenerationRunCancel.test.ts",
        "src/services/api/__tests__/session.test.ts",
    ):
        assert test_sse.split().count(selector) == 1
    assert "group: ai-platform-frontend-${{ github.event.pull_request.number || github.run_id }}" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    contract = _workflow_contract()
    assert contract["on"] == {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main"]},
        "workflow_dispatch": "",
    }
    jobs = contract["jobs"]
    assert isinstance(jobs, dict)
    assert jobs["frontend"]["runs-on"] == "windows-latest"
    assert jobs["frontend"]["timeout-minutes"] == "30"
    frontend_steps = jobs["frontend"]["steps"]
    store_step = next(
        step for step in frontend_steps if step.get("name") == "Resolve pnpm store"
    )
    cache_step = next(
        step for step in frontend_steps if step.get("name") == "Cache pnpm store"
    )
    install_step = next(
        step
        for step in frontend_steps
        if step.get("name") == "Install frontend dependencies"
    )
    assert store_step["id"] == "pnpm-store"
    assert "corepack pnpm store path --silent" in store_step["run"]
    assert frontend_steps.index(store_step) < frontend_steps.index(cache_step)
    assert frontend_steps.index(cache_step) < frontend_steps.index(install_step)
    assert cache_step["uses"] == (
        "actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809"
    )
    assert cache_step["with"]["path"] == "${{ steps.pnpm-store.outputs.path }}"
    assert cache_step["with"]["key"] == (
        "frontend-pnpm-${{ runner.os }}-${{ hashFiles('frontend/web/pnpm-lock.yaml') }}"
    )
    assert cache_step["with"]["restore-keys"] == "frontend-pnpm-${{ runner.os }}-\n"
    assert jobs["frontend-image"]["runs-on"] == "ubuntu-latest"
    assert jobs["frontend-image"]["timeout-minutes"] == "30"
    assert "needs" not in jobs["frontend-image"]
    required = jobs["required"]
    assert isinstance(required, dict)
    assert required["name"] == "frontend required"
    assert required["timeout-minutes"] == "5"
    assert required["needs"] == list(REQUIRED_FRONTEND_JOBS)
    assert required["if"] == "${{ always() }}"
    image_job = workflow.split("  frontend-image:", 1)[1].split("  required:", 1)[0]
    required_job = workflow.split("  required:", 1)[1]
    assert (
        "IMAGE_BASE_COMMIT: ${{ github.event.pull_request.base.sha || github.sha }}"
        in image_job
    )
    assert "fetch-depth: 0" in image_job
    assert "- name: Determine frontend image impact" in image_job
    assert "python tools/ci_image_scope.py" in image_job
    assert '--event-name "$GITHUB_EVENT_NAME"' in image_job
    assert '--role frontend' in image_job
    assert '--base-ref "$IMAGE_BASE_COMMIT"' in image_job
    assert '--head-ref "$IMAGE_SOURCE_COMMIT"' in image_job
    assert "- name: Report frontend image build disposition" in image_job
    assert "if: steps.image-scope.outputs.build != 'true'" in image_job
    assert (
        "IMAGE_DISPOSITION: ${{ steps.image-scope.outputs.disposition }}"
        in image_job
    )
    assert 'case "$IMAGE_DISPOSITION" in' in image_job
    assert "not_affected)" in image_job
    assert "packaging_owned)" in image_job
    assert 'test "$GITHUB_EVENT_NAME" = "pull_request"' in image_job
    assert 'test "$GITHUB_EVENT_NAME" != "pull_request"' in image_job
    assert "frontend_image_validation=not_affected" in image_job
    assert "frontend_image_build=delegated owner=ai-platform-packaging-publish" in image_job
    assert "base_commit=%s head_commit=%s" in image_job
    assert "unexpected_disposition" in image_job
    for step_name in [
        "Set up Python for Linux contracts",
        "Set up uv for Linux contracts",
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
    assert "deploy_frontend_static.py" not in workflow
    assert not (ROOT / "tools/deploy_frontend_static.py").exists()
    assert not (ROOT / "tests/test_deploy_frontend_static.py").exists()
    assert "corepack pnpm run lint && corepack pnpm run check:style-rules && corepack pnpm run build" in workflow
    assert "python tools/frontend_projection_audit.py --format json" in workflow
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
    assert workflow.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}") == 4
    assert workflow.count("persist-credentials: false") == 4
    for job_name in ("frontend", "frontend-tests", "frontend-contracts"):
        job = jobs[job_name]
        assert job["runs-on"] == "windows-latest"
        assert "needs" not in job
        assert job["env"]["SOURCE_COMMIT"] == "${{ github.event.pull_request.head.sha || github.sha }}"
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

    contract_steps = jobs["frontend-contracts"]["steps"]
    contract_names = [step["name"] for step in contract_steps]
    assert contract_names.index("Set up uv for frontend contracts") < contract_names.index("Install Python test dependencies")
    assert contract_names.index("Install Python test dependencies") < contract_names.index("Verify static frontend Python contracts")
    assert contract_names.index("Verify static frontend Python contracts") < contract_names.index("Audit frontend projections")
    install_python = next(step["run"] for step in contract_steps if step["name"] == "Install Python test dependencies")
    assert install_python.index(PYTHON_TEST_DEPENDENCIES) < install_python.index("verify_installed_jsonschema_version(locked_jsonschema)")
    assert install_python.index("verify_installed_jsonschema_version(locked_jsonschema)") < install_python.index('python -c "import jsonschema"')
    frontend_names = [step["name"] for step in frontend_steps]
    assert frontend_names.index("Verify frontend lint and build") < frontend_names.index("Record frontend release traceability")
    assert frontend_names.index("Record frontend release traceability") < frontend_names.index("Record packaged frontend runtime smoke contract")
    image_names = [step["name"] for step in jobs["frontend-image"]["steps"]]
    assert image_names.index("Install Linux contract test dependencies") < image_names.index("Verify Linux frontend healthcheck contract")

    expected_split_steps = (
        "      - name: Verify static frontend Python contracts\n"
        f"        run: {PYTEST_COMMAND}\n\n"
        "      - name: Audit frontend projections\n"
        "        run: python tools/frontend_projection_audit.py --format json"
    )
    assert expected_split_steps in workflow
    expected_linux_steps = (
        "      - name: Install Linux contract test dependencies\n"
        "        if: steps.image-scope.outputs.build == 'true'\n"
        "        run: python tools/frontend_ci_dependencies.py --subset linux-contracts\n\n"
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


@pytest.mark.parametrize("job_name", REQUIRED_FRONTEND_JOBS)
@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", "", "missing"])
def test_frontend_required_contract_executes_failure_paths(job_name: str, result: str) -> None:
    namespace = _required_contract_namespace()
    require_successful_results = namespace["require_successful_results"]
    assert callable(require_successful_results)
    results = dict.fromkeys(REQUIRED_FRONTEND_JOBS, "success")
    if result == "missing":
        del results[job_name]
    else:
        results[job_name] = result

    with pytest.raises(RuntimeError, match=job_name):
        require_successful_results(results)


def test_frontend_required_contract_accepts_only_all_success() -> None:
    namespace = _required_contract_namespace()
    require_successful_results = namespace["require_successful_results"]
    assert callable(require_successful_results)

    require_successful_results(dict.fromkeys(REQUIRED_FRONTEND_JOBS, "success"))


@pytest.mark.parametrize("subset", ["windows-contracts", "linux-contracts"])
def test_frontend_dependency_subset_uses_locked_export_and_checks_installed_versions(
    subset: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LOCK.open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    versions = {package["name"]: package["version"] for package in packages}
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "uv":
            Path(command[-1]).write_text("pytest==" + versions["pytest"], encoding="utf-8")
        else:
            assert Path(command[5]).is_file()

    monkeypatch.setattr(subprocess, "run", run)
    with patch.object(importlib.metadata, "version", side_effect=versions.__getitem__) as version:
        frontend_ci_dependencies.install_dependencies(subset)
    export, install = [call[0] for call in calls]
    assert export[:-1] == [
        "uv", "export", "--locked", "--extra", "test", "--no-emit-project",
        "--no-hashes", "--output-file",
    ]
    assert install[:5] == [sys.executable, "-m", "pip", "install", "--constraint"]
    assert install[5] == export[-1]
    expected = ("pytest", "pyyaml") if subset == "windows-contracts" else ("pytest",)
    assert tuple(install[6:]) == expected
    assert [call.args[0] for call in version.call_args_list] == list(expected)
    assert all(kwargs["check"] and kwargs["cwd"] == ROOT for _, kwargs in calls)
    assert not Path(export[-1]).exists()


@pytest.mark.parametrize("failure", ["export", "install", "missing", "mismatch"])
def test_frontend_dependency_installation_fails_closed(failure: str) -> None:
    def run(command, **kwargs):
        if (command[0] == "uv") == (failure == "export") and failure in {"export", "install"}:
            raise subprocess.CalledProcessError(1, command)

    with patch.object(subprocess, "run", side_effect=run) as execute:
        with patch.object(importlib.metadata, "version") as version:
            if failure == "missing":
                version.side_effect = importlib.metadata.PackageNotFoundError("pytest")
            else:
                version.return_value = "0.0.0"
            with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
                frontend_ci_dependencies.install_dependencies("linux-contracts")
    assert execute.call_count == (1 if failure == "export" else 2)


@pytest.mark.parametrize("packages", [[], [{"name": "pytest"}], [
    {"name": "pytest", "version": "1"}, {"name": "pytest", "version": "2"},
]])
def test_frontend_dependency_installation_rejects_ambiguous_lock(packages, tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    with patch.object(tomllib, "load", return_value={"package": packages}):
        with patch.object(subprocess, "run") as execute:
            with pytest.raises(RuntimeError, match="exactly one version for pytest"):
                frontend_ci_dependencies.install_dependencies("linux-contracts", root=tmp_path)
    execute.assert_not_called()


def test_frontend_uv_bootstrap_matches_existing_authority_and_precedes_each_subset() -> None:
    workflow = _workflow_contract()
    with (ROOT / "pyproject.toml").open("rb") as handle:
        required = tomllib.load(handle)["tool"]["uv"]["required-version"]
    assert required == "==" + workflow["env"]["UV_VERSION"]
    backend = _workflow_contract(BACKEND_WORKFLOW)
    assert backend["env"]["UV_VERSION"] == workflow["env"]["UV_VERSION"]
    for job, setup_name, install_name in [
        ("frontend", "Set up uv for frontend contracts", "Install Python traceability dependencies"),
        ("frontend-contracts", "Set up uv for frontend contracts", "Install Python test dependencies"),
        ("frontend-image", "Set up uv for Linux contracts", "Install Linux contract test dependencies"),
    ]:
        steps = workflow["jobs"][job]["steps"]
        setup = next(step for step in steps if step["name"] == setup_name)
        install = next(step for step in steps if step["name"] == install_name)
        assert setup["uses"] == "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
        assert setup["with"]["version"] == "${{ env.UV_VERSION }}"
        assert steps.index(setup) < steps.index(install)
        assert setup.get("if") == install.get("if")
