import ast
import os
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-backend.yml"
TRUSTED_GOVERNANCE_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-trusted-governance.yml"
FRONTEND_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"
PYPROJECT = ROOT / "pyproject.toml"
AGENT_RULES = ROOT / "AGENTS.md"
ISSUE_WORKFLOW = ROOT / "docs" / "agent-rules" / "github-issue-pr-workflow.md"
TRUSTED_RUFF_REGEX = r"[0-9]+\.[0-9]+\.[0-9]+"
TRUSTED_JSONSCHEMA_REGEX = r"[0-9]+(?:\.[0-9]+)+"
TRUSTED_WORKFLOW_IMPORTS = (
    ("import", (("importlib.metadata", None),)),
    ("import", (("re", None),)),
    ("import", (("subprocess", None),)),
    ("import", (("sys", None),)),
    ("import", (("tomllib", None),)),
    ("from", "pathlib", 0, (("Path", None),)),
    ("from", "packaging.requirements", 0, (("InvalidRequirement", None), ("Requirement", None))),
    ("from", "packaging.utils", 0, (("canonicalize_name", None),)),
    ("from", "packaging.version", 0, (("InvalidVersion", None), ("Version", None))),
)
AGENT_SKILL_CONTRACT_TESTS = (
    "tests/test_agent_profile_authority.py",
    "tests/test_agent_profile_lifecycle.py",
    "tests/test_agent_profile_routes.py",
    "tests/test_agent_profiles_postgres.py",
    "tests/test_authorized_skill_catalog.py",
    "tests/test_skill_dependencies.py",
    "tests/test_skill_lifecycle.py",
    "tests/test_skill_registry.py",
    "tests/test_skill_stager.py",
    "tests/test_skill_release_policy.py",
    "tests/test_chat_routes.py",
    "tests/test_skills_marketplace_routes.py",
)
BACKEND_TEST_SHARDS = {
    "sandbox-runtime": (
        "tests/test_claude_agent_sdk_installed_contract.py",
        "tests/test_claude_agent_sdk_runner.py",
        "tests/test_claude_agent_worker_adapter.py",
        "tests/test_required_tool_contract.py",
        "tests/test_intent_router.py",
        "tests/test_public_answer_stream.py",
        "tests/test_sandbox_executor_app.py",
        "tests/test_settings.py",
        "tests/test_sandbox_container_provider.py",
        "tests/test_opensandbox_live_credential_isolation.py",
        "tests/test_sandbox_runtime.py",
        "tests/test_sandbox_runtime_cleanup.py",
        "tests/test_sandbox_runtime_evidence_script.py",
        "tests/test_b2_sandbox_readiness.py",
        "tests/test_contract.py",
    ),
    "repository-worker-streaming": (
        "tests/test_repositories.py",
        "tests/test_worker_main.py",
        "tests/test_sse_runtime_cutover.py",
        "tests/test_streaming_redis.py",
        "tests/test_streaming_control.py",
        "tests/test_streaming_postgres.py",
        "tests/test_streaming_repository.py",
        "tests/test_runtime_callbacks.py",
        "tests/test_worker.py",
        "tests/test_lambchat_sse_v21.py",
        "tests/test_app_lifespan.py",
        "tests/test_runtime_launch_script.py",
    ),
    "release-governance-policy": (
        "tests/test_architecture_governance.py",
        "tests/test_backend_ci_workflow.py",
        "tests/test_ci_image_scope.py",
        "tests/test_s72_release_contract.py",
        "tests/test_packaging_contract.py",
        "tests/test_packaging_publish_workflow.py",
        "tests/test_trivy_failure_evidence.py",
        "tests/test_release_image_manifest.py",
    ),
    "release-governance-readiness": (
        "tests/test_pre_push_readiness.py",
    ),
    "release-governance-authority": (
        "tests/test_s72_atomic_recovery_authority.py",
        "tests/test_governance_readiness.py",
        "tests/test_release_authority.py",
    ),
}


def _workflow_job_block(workflow: str, job_id: str) -> str:
    matches = list(re.finditer(rf"(?m)^  {re.escape(job_id)}:\s*$", workflow))
    assert len(matches) == 1, f"expected exactly one {job_id} job"
    start = matches[0].start()
    next_job = re.search(
        r"(?m)^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$", workflow[matches[0].end() :]
    )
    end = len(workflow) if next_job is None else matches[0].end() + next_job.start()
    return workflow[start:end]


def test_backend_required_check_is_stable_for_every_main_pull_request():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    pull_request_block = workflow.split("pull_request:", 1)[1].split("push:", 1)[0]
    assert "branches:" in pull_request_block
    assert "- main" in pull_request_block
    assert "paths:" not in pull_request_block
    assert "group: ai-platform-backend-${{ github.event.pull_request.number || github.run_id }}" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "name: backend required" in workflow
    assert (
        "needs: [backend-validation, backend-tests, agent-skill-contracts, backend-image]"
        in workflow
    )
    assert "name: Agent and Skill contracts" in workflow
    assert "name: packaged backend image build" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "python -m compileall -q app tools scripts" in workflow
    assert "tests/test_b2_sandbox_readiness.py" in workflow
    assert "tests/test_backend_ci_workflow.py" in workflow
    assert "tests/test_packaging_publish_workflow.py" in workflow
    assert "tests/test_release_image_manifest.py" in workflow
    assert "tests/test_governance_readiness.py" in workflow
    assert "tests/test_release_authority.py" in workflow
    assert "tests/test_contract.py" in workflow
    assert "tests/test_worker_main.py" in workflow


def test_backend_required_ubuntu_jobs_execute_complete_parallel_test_shards():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    workflow_document = yaml.safe_load(workflow)
    validation_job = _workflow_job_block(workflow, "backend-validation")
    tests_job = _workflow_job_block(workflow, "backend-tests")
    required_job = _workflow_job_block(workflow, "required")

    assert "runs-on: ubuntu-latest" in validation_job
    assert "timeout-minutes: 10" in validation_job
    assert validation_job.count("- name: Enforce SSE v3 release-atomic cutover") == 1
    assert validation_job.count("run: python tools/check_sse_runtime_cutover.py") == 1
    assert "runs-on: ubuntu-latest" in tests_job
    assert "needs: backend-validation" in tests_job
    assert "timeout-minutes: 15" in tests_job
    assert "fail-fast: false" in tests_job
    assert "ref: ${{ env.BACKEND_TEST_SOURCE_COMMIT }}" in tests_job
    assert "fetch-depth: 0" in tests_job
    matrix_entries = workflow_document["jobs"]["backend-tests"]["strategy"]["matrix"][
        "include"
    ]
    actual_shards = {
        entry["shard"]: tuple(entry["test_files"].split()) for entry in matrix_entries
    }
    actual_redis = {
        entry["shard"]: (entry["redis_image"], entry["redis_url"])
        for entry in matrix_entries
    }
    assert actual_shards == BACKEND_TEST_SHARDS
    assert actual_redis == {
        "sandbox-runtime": ("redis:7.4-alpine", "redis://localhost:6379/15"),
        "repository-worker-streaming": (
            "redis:7.4-alpine",
            "redis://localhost:6379/15",
        ),
        "release-governance-policy": ("", ""),
        "release-governance-readiness": ("", ""),
        "release-governance-authority": ("", ""),
    }
    all_selectors = [selector for selectors in BACKEND_TEST_SHARDS.values() for selector in selectors]
    assert len(all_selectors) == len(set(all_selectors)) == 39
    assert "image: ${{ matrix.redis_image }}" in tests_job
    assert '"6379:6379"' in tests_job
    assert '--health-cmd "redis-cli ping"' in tests_job
    assert (
        "AI_PLATFORM_SSE_REDIS_TEST_URL: ${{ matrix.redis_url }}" in tests_job
    )
    pytest_step = tests_job.split("- name: Run backend test shard", 1)[1]
    assert pytest_step.index("mkdir -p .pytest-tmp") < pytest_step.index("timeout --signal")
    assert "timeout --signal=TERM --kill-after=30s 10m" in pytest_step
    assert "uv run --locked --extra test python -m pytest" in pytest_step
    assert "${{ matrix.test_files }}" in pytest_step
    assert "-vv" in pytest_step
    assert "--tb=short" in pytest_step
    assert "-o faulthandler_timeout=120" in pytest_step
    assert '--basetemp ".pytest-tmp/${{ matrix.shard }}"' in pytest_step
    assert "--collect-only" not in pytest_step
    assert "--ignore" not in pytest_step
    assert " -k " not in pytest_step
    assert "runs-on: ubuntu-latest" in required_job
    assert (
        "needs: [backend-validation, backend-tests, agent-skill-contracts, backend-image]"
        in required_job
    )
    assert "VALIDATION_RESULT: ${{ needs.backend-validation.result }}" in required_job
    assert "BACKEND_TESTS_RESULT: ${{ needs.backend-tests.result }}" in required_job
    assert 'test "$VALIDATION_RESULT" = "success"' in required_job
    assert 'test "$BACKEND_TESTS_RESULT" = "success"' in required_job
    assert "if: ${{ always() }}" in required_job


def test_agent_skill_contract_job_is_bounded_and_required():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    workflow_document = yaml.safe_load(workflow)
    agent_skill_job = _workflow_job_block(workflow, "agent-skill-contracts")
    agent_skill_job_document = workflow_document["jobs"]["agent-skill-contracts"]
    required_job = _workflow_job_block(workflow, "required")
    pytest_step = agent_skill_job.split("- name: Run Agent and Skill contract tests", 1)

    assert len(pytest_step) == 2
    assert "name: Agent and Skill contracts" in agent_skill_job
    assert "runs-on: ubuntu-latest" in agent_skill_job
    assert "needs: backend-validation" in agent_skill_job
    assert "timeout-minutes: 15" in agent_skill_job
    assert (
        "AGENT_SKILL_SOURCE_COMMIT: "
        "${{ github.event.pull_request.head.sha || github.sha }}"
        in agent_skill_job
    )
    assert "ref: ${{ env.AGENT_SKILL_SOURCE_COMMIT }}" in agent_skill_job
    assert "persist-credentials: false" in agent_skill_job
    assert (
        'test "$(git rev-parse HEAD)" = "$AGENT_SKILL_SOURCE_COMMIT"'
        in agent_skill_job
    )
    assert "uv lock --check" in agent_skill_job
    assert "uv sync --locked --extra test --no-install-project" in agent_skill_job
    assert re.search(r"(?m)^\s*continue-on-error\s*:", agent_skill_job) is None
    assert agent_skill_job_document["services"] == {
        "postgres": {
            "image": "postgres:16-alpine",
            "env": {
                "POSTGRES_DB": "ai_platform",
                "POSTGRES_USER": "ai_platform",
                "POSTGRES_PASSWORD": "ai_platform_ci_password",
            },
            "ports": ["54329:5432"],
            "options": (
                '--health-cmd "pg_isready -U ai_platform -d ai_platform" '
                "--health-interval 10s --health-timeout 5s --health-retries 10"
            ),
        }
    }
    assert agent_skill_job_document["env"] == {
        "AGENT_SKILL_SOURCE_COMMIT": "${{ github.event.pull_request.head.sha || github.sha }}",
        "AI_PLATFORM_AGENT_PROFILE_TEST_DSN": (
            "postgresql://ai_platform:ai_platform_ci_password@127.0.0.1:54329/ai_platform"
        ),
    }

    run_script = pytest_step[1].split("run: |", 1)[1]
    assert run_script.index("mkdir -p .pytest-tmp") < run_script.index("timeout --signal")
    timeout_script = run_script.split("mkdir -p .pytest-tmp", 1)[1]
    normalized_run = re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", timeout_script)
    tokens = shlex.split(normalized_run)
    expected_tokens = [
        "timeout",
        "--signal=TERM",
        "--kill-after=30s",
        "10m",
        "uv",
        "run",
        "--locked",
        "--extra",
        "test",
        "python",
        "-m",
        "pytest",
        *AGENT_SKILL_CONTRACT_TESTS,
        "-vv",
        "--tb=short",
        "-o",
        "faulthandler_timeout=120",
        "--basetemp",
        ".pytest-tmp/agent-skill-contracts",
    ]
    assert tokens == expected_tokens
    assert not any(token.startswith("-k") for token in tokens)
    assert not any(token.startswith("--ignore") for token in tokens)

    assert (
        "needs: [backend-validation, backend-tests, agent-skill-contracts, backend-image]"
        in required_job
    )
    assert (
        "AGENT_SKILL_RESULT: ${{ needs.agent-skill-contracts.result }}"
        in required_job
    )
    assert 'test "$AGENT_SKILL_RESULT" = "success"' in required_job
    assert re.search(r"(?m)^\s*continue-on-error\s*:", required_job) is None


def test_backend_required_check_runs_on_every_main_push():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    push_block = workflow.split("push:", 1)[1].split("workflow_dispatch:", 1)[0]
    assert "branches:" in push_block
    assert "- main" in push_block
    assert "paths:" not in push_block


def test_ruff_is_pinned_in_the_test_extra_without_enabling_broad_linting():
    import tomllib

    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)

    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]
    ruff_dependencies = [
        requirement
        for dependency in test_dependencies
        if canonicalize_name((requirement := Requirement(dependency)).name) == "ruff"
    ]

    assert [str(requirement) for requirement in ruff_dependencies] == ["ruff==0.11.13"]
    assert all(
        canonicalize_name(Requirement(dependency).name) != "ruff"
        for dependency in pyproject["project"]["dependencies"]
    )


def _frontend_ruff_requirement_resolver(workflow: str | None = None):
    if workflow is None:
        workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    install_step = workflow.split("- name: Install Python test dependencies", 1)[1].split(
        "- name: Verify static frontend Python contracts", 1
    )[0]
    install_script = install_step.split("@'\n", 1)[1]
    heredoc_end = re.search(r"(?m)^\s*'@ \| python -\s*$", install_script)
    if heredoc_end is None:
        raise RuntimeError("frontend workflow Python heredoc has no terminator")
    script = textwrap.dedent(install_script[: heredoc_end.start()])
    module = ast.parse(script)
    imports = []
    for statement in module.body:
        if isinstance(statement, ast.Import):
            imports.append(("import", tuple((alias.name, alias.asname) for alias in statement.names)))
        elif isinstance(statement, ast.ImportFrom):
            imports.append(
                (
                    "from",
                    statement.module,
                    statement.level,
                    tuple((alias.name, alias.asname) for alias in statement.names),
                )
            )
    if tuple(imports) != TRUSTED_WORKFLOW_IMPORTS:
        raise RuntimeError("frontend workflow imports must match the trusted allowlist")

    def trusted_regex_constant(name: str, pattern: str) -> None:
        assignments = [
            statement
            for statement in module.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == name
        ]
        if len(assignments) != 1:
            raise RuntimeError(f"frontend workflow must define exactly one {name} constant")
        value = assignments[0].value
        literal = ast.get_source_segment(script, value.args[0]) if isinstance(value, ast.Call) and value.args else None
        if (
            not isinstance(value, ast.Call)
            or not isinstance(value.func, ast.Attribute)
            or not isinstance(value.func.value, ast.Name)
            or value.func.value.id != "re"
            or value.func.attr != "compile"
            or value.keywords
            or len(value.args) != 1
            or not isinstance(value.args[0], ast.Constant)
            or value.args[0].value != pattern
            or literal != f'r"{pattern}"'
        ):
            raise RuntimeError(f"frontend workflow {name} must be a trusted pure regex literal")

    trusted_regex_constant("CANONICAL_RUFF_VERSION", TRUSTED_RUFF_REGEX)
    trusted_regex_constant("CANONICAL_JSONSCHEMA_VERSION", TRUSTED_JSONSCHEMA_REGEX)
    functions = [
        statement
        for statement in module.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "resolve_ruff_requirement"
    ]
    if len(functions) != 1:
        raise RuntimeError("frontend workflow must define exactly one Ruff resolver")
    resolver_definition = functions[0]
    arguments = resolver_definition.args
    argument_nodes = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if (
        resolver_definition.decorator_list
        or resolver_definition.returns is not None
        or resolver_definition.type_comment is not None
        or getattr(resolver_definition, "type_params", [])
        or arguments.posonlyargs
        or len(arguments.args) != 1
        or arguments.args[0].arg != "test_dependencies"
        or arguments.vararg is not None
        or arguments.kwarg is not None
        or arguments.kwonlyargs
        or arguments.defaults
        or any(default is not None for default in arguments.kw_defaults)
        or any(argument.annotation is not None or argument.type_comment is not None for argument in argument_nodes)
    ):
        raise RuntimeError("frontend Ruff resolver has unsafe definition-time effects")

    allowed_nodes = {
        ast.FunctionDef, ast.arguments, ast.arg, ast.Assign, ast.Attribute, ast.BoolOp,
        ast.Call, ast.Compare, ast.Constant, ast.Eq, ast.ExceptHandler, ast.Expr,
        ast.For, ast.FormattedValue, ast.If, ast.IsNot, ast.JoinedStr, ast.List,
        ast.Load, ast.Name, ast.Not, ast.NotEq, ast.Or, ast.Raise, ast.Return,
        ast.Store, ast.Subscript, ast.Try, ast.UnaryOp,
    }
    unexpected = next(
        (node for node in ast.walk(resolver_definition) if type(node) not in allowed_nodes),
        None,
    )
    if unexpected is not None:
        raise RuntimeError(f"frontend Ruff resolver contains unsafe AST node {type(unexpected).__name__}")
    if sum(isinstance(node, ast.FunctionDef) for node in ast.walk(resolver_definition)) != 1:
        raise RuntimeError("frontend Ruff resolver cannot nest function definitions")

    local_names = {
        node.id
        for node in ast.walk(resolver_definition)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    local_names.add("test_dependencies")
    local_names.update(
        handler.name
        for handler in ast.walk(resolver_definition)
        if isinstance(handler, ast.ExceptHandler) and handler.name is not None
    )
    trusted_names = {
        "CANONICAL_RUFF_VERSION", "InvalidRequirement", "Requirement", "RuntimeError",
        "InvalidVersion", "Version", "canonicalize_name", "len", "list", "str",
    }
    for node in ast.walk(resolver_definition):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in local_names | trusted_names:
            raise RuntimeError(f"frontend Ruff resolver uses unsafe name {node.id}")
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name) or (node.value.id, node.attr) not in {
                ("CANONICAL_RUFF_VERSION", "fullmatch"),
                ("requirement", "name"),
                ("ruff_requirements", "append"),
                ("ruff_requirement", "url"),
                ("ruff_requirement", "extras"),
                ("ruff_requirement", "marker"),
                ("ruff_requirement", "specifier"),
                ("specifier", "operator"),
                ("specifier", "version"),
            }:
                raise RuntimeError("frontend Ruff resolver uses an unsafe dynamic attribute")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                allowed_call = node.func.id in {
                    "Requirement", "RuntimeError", "Version", "canonicalize_name", "len", "list", "str",
                }
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                allowed_call = (node.func.value.id, node.func.attr) in {
                    ("CANONICAL_RUFF_VERSION", "fullmatch"), ("ruff_requirements", "append"),
                }
            else:
                allowed_call = False
            if not allowed_call or node.keywords:
                raise RuntimeError("frontend Ruff resolver uses an unsafe call")
        if isinstance(node, ast.Subscript) and (
            not isinstance(node.value, ast.Name)
            or node.value.id not in {"ruff_requirements", "specifiers"}
            or not isinstance(node.slice, ast.Constant)
            or node.slice.value != 0
        ):
            raise RuntimeError("frontend Ruff resolver uses an unsafe subscript")

    trusted_definition = ast.FunctionDef(
        name="resolve_ruff_requirement",
        args=arguments,
        body=resolver_definition.body,
        decorator_list=[],
        returns=None,
        type_comment=None,
        type_params=[],
    )
    namespace: dict[str, object] = {
        "__name__": "frontend_workflow_contract",
        "CANONICAL_RUFF_VERSION": re.compile(TRUSTED_RUFF_REGEX),
        "InvalidRequirement": InvalidRequirement,
        "Requirement": Requirement,
        "RuntimeError": RuntimeError,
        "InvalidVersion": InvalidVersion,
        "Version": Version,
        "canonicalize_name": canonicalize_name,
    }
    exec(  # noqa: S102 -- only a statically validated, definition-time-safe function is compiled.
        compile(ast.fix_missing_locations(ast.Module(body=[trusted_definition], type_ignores=[])), "<frontend-ruff-resolver>", "exec"),
        namespace,
    )
    resolver = namespace.get("resolve_ruff_requirement")
    if not callable(resolver):
        raise RuntimeError("frontend workflow does not define a Ruff resolver")
    return resolver


def test_frontend_ruff_requirement_resolver_extraction_does_not_run_installation(
    monkeypatch: pytest.MonkeyPatch,
):
    def reject_install(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"resolver extraction must not install dependencies: {args!r}")

    monkeypatch.setattr(subprocess, "check_call", reject_install)

    resolver = _frontend_ruff_requirement_resolver()

    assert resolver(["pytest>=8.2.0", "ruff==0.11.13"]) == "ruff==0.11.13"


def _mutate_frontend_workflow(before: str, after: str) -> str:
    workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count(before) == 1
    return workflow.replace(before, after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            'CANONICAL_RUFF_VERSION = re.compile(r"[0-9]+\\.[0-9]+\\.[0-9]+")',
            'CANONICAL_RUFF_VERSION = subprocess.check_call([sys.executable, "-m", "pip", "install", "attacker"]) or re.compile(r"[0-9]+\\.[0-9]+\\.[0-9]+")',
            id="assignment-call",
        ),
        pytest.param("          import re", "          import re as trusted_re", id="import-alias"),
        pytest.param("          import re", "          import re\n          import attacker", id="piggyback-import"),
        pytest.param(
            "          def resolve_ruff_requirement(test_dependencies):",
            '          @subprocess.check_call([sys.executable, "-m", "pip", "install", "attacker"])\n          def resolve_ruff_requirement(test_dependencies):',
            id="decorator-call",
        ),
        pytest.param(
            "          def resolve_ruff_requirement(test_dependencies):",
            '          def resolve_ruff_requirement(test_dependencies=subprocess.check_call([sys.executable, "-m", "pip", "install", "attacker"])):',
            id="default-call",
        ),
        pytest.param(
            "          def resolve_ruff_requirement(test_dependencies):",
            '          def resolve_ruff_requirement(test_dependencies, *, unused=subprocess.check_call([sys.executable, "-m", "pip", "install", "attacker"])):',
            id="kw-default-call",
        ),
        pytest.param(
            "          def resolve_ruff_requirement(test_dependencies):",
            '          def resolve_ruff_requirement(test_dependencies: subprocess.check_call([sys.executable, "-m", "pip", "install", "attacker"])):',
            id="annotation-call",
        ),
        pytest.param(
            "              ruff_requirements = []",
            '              subprocess.check_call([sys.executable, "-m", "pip", "install", "attacker"])\n              ruff_requirements = []',
            id="body-subprocess-install",
        ),
        pytest.param(
            "              ruff_requirements = []",
            '              open("attacker", "w", encoding="utf-8")\n              ruff_requirements = []',
            id="body-open",
        ),
        pytest.param(
            "              ruff_requirements.append(requirement)",
            '              getattr(ruff_requirements, "append")(requirement)',
            id="dynamic-attribute",
        ),
    ],
)
def test_frontend_ruff_requirement_resolver_rejects_untrusted_workflow_ast(
    before: str,
    after: str,
    monkeypatch: pytest.MonkeyPatch,
):
    def reject_install(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"workflow AST must not execute: {args!r}")

    monkeypatch.setattr(subprocess, "check_call", reject_install)

    with pytest.raises(RuntimeError, match="trusted|unsafe|resolver"):
        _frontend_ruff_requirement_resolver(_mutate_frontend_workflow(before, after))


def test_frontend_static_contracts_install_only_the_pinned_test_extra_ruff():
    import tomllib

    workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    install_step = workflow.split("- name: Install Python test dependencies", 1)[1].split(
        "- name: Verify static frontend Python contracts", 1
    )[0]

    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)

    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]
    assert 'tomllib.load(handle)["project"]["optional-dependencies"]["test"]' in install_step
    assert "from packaging.requirements import InvalidRequirement, Requirement" in install_step
    assert "from packaging.utils import canonicalize_name" in install_step
    assert "for dependency in test_dependencies:" in install_step
    assert 'canonicalize_name(requirement.name) == "ruff"' in install_step
    assert "if len(ruff_requirements) != 1:" in install_step
    assert "CANONICAL_RUFF_VERSION.fullmatch(version)" in install_step
    assert 'return f"ruff=={version}"' in install_step
    assert '[sys.executable, "-m", "pip", "install", ruff_requirement]' in install_step
    assert "*test_dependencies" not in install_step
    assert '["project"]["dependencies"]' not in install_step
    assert "tests/test_backend_ci_workflow.py" not in workflow
    assert "tests/test_backend_ci_workflow.py" in WORKFLOW.read_text(encoding="utf-8")

    resolver = _frontend_ruff_requirement_resolver()
    assert resolver(test_dependencies) == "ruff==0.11.13"


def test_frontend_ruff_requirement_resolver_normalizes_a_canonical_pin():
    resolver = _frontend_ruff_requirement_resolver()

    assert resolver(["pytest>=8.2.0", "Ruff == 0.11.13"]) == "ruff==0.11.13"


@pytest.mark.parametrize(
    ("dependencies", "message"),
    [
        pytest.param(["ruff"], "exact canonical version pin", id="unpinned"),
        pytest.param(
            ["ruff==0.11.13", "ruff @ https://example.invalid/ruff.whl"],
            "expected exactly one Ruff test dependency",
            id="multiple-including-url",
        ),
        pytest.param(
            ['ruff==0.11.13; python_version >= "3.11"'],
            "exact canonical version pin",
            id="marker",
        ),
        pytest.param(["ruff[cli]==0.11.13"], "exact canonical version pin", id="extras"),
        pytest.param(["ruff===0.11.13"], "exact canonical version pin", id="arbitrary-equals"),
        pytest.param(["ruff==0.11.*"], "exact canonical version pin", id="wildcard"),
        pytest.param(["ruff>=0.11.13"], "exact canonical version pin", id="range"),
        pytest.param(["ruff==00.11.13"], "exact canonical version pin", id="noncanonical-version"),
    ],
)
def test_frontend_ruff_requirement_resolver_rejects_noncanonical_declarations(
    dependencies: list[str], message: str
):
    resolver = _frontend_ruff_requirement_resolver()

    with pytest.raises(RuntimeError, match=message):
        resolver(["pytest>=8.2.0", *dependencies])


def test_code_governance_uses_trusted_base_code_for_an_exact_pr_range():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "fetch-depth: 0" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha || github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "persist-credentials: true" not in workflow
    assert "pull_request_target:" not in workflow

    governance_step = workflow.split("- name: Run code and architecture governance", 1)[1].split(
        "- name: Checkout validated pull request head for existing checks", 1
    )[0]
    install_start = workflow.index("- name: Install trusted-base governance dependency")
    governance_start = workflow.index("- name: Run code and architecture governance")
    pre_governance = workflow[:governance_start]
    assert (
        workflow.index("ref: ${{ github.event.pull_request.base.sha || github.sha }}")
        < install_start
    )
    assert install_start < governance_start
    assert "github.event.pull_request.head" not in pre_governance
    assert "refs/pull/" not in pre_governance
    assert "if: ${{ github.event_name == 'pull_request' || github.event_name == 'push' }}" in governance_step
    assert "GOVERNANCE_PR_NUMBER: ${{ github.event.number }}" in governance_step
    assert (
        "GOVERNANCE_BASE_REF: ${{ github.event.pull_request.base.sha || github.event.before }}"
        in governance_step
    )
    assert (
        "GOVERNANCE_HEAD_REF: ${{ github.event.pull_request.head.sha || github.sha }}"
        in governance_step
    )
    assert "GOVERNANCE_FETCH_TOKEN: ${{ github.token }}" in governance_step
    assert 'PYTHONSAFEPATH: "1"' in governance_step
    assert "set -euo pipefail" in governance_step
    assert '[[ "$GOVERNANCE_PR_NUMBER" =~ ^[1-9][0-9]*$ ]]' in governance_step
    assert '[[ "$GOVERNANCE_BASE_REF" =~ ^[0-9a-f]{40}$ ]]' in governance_step
    assert '[[ "$GOVERNANCE_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in governance_step
    assert (
        'GOVERNANCE_PULL_REF="refs/remotes/origin/pull/$GOVERNANCE_PR_NUMBER/head"'
        in governance_step
    )
    assert (
        'GOVERNANCE_FETCH_BASIC="$(printf \'x-access-token:%s\' "$GOVERNANCE_FETCH_TOKEN" '
        '| base64 --wrap=0)"' in governance_step
    )
    assert 'echo "::add-mask::$GOVERNANCE_FETCH_BASIC"' in governance_step
    fetch_command = (
        'git -c http.https://github.com/.extraheader="AUTHORIZATION: basic '
        '$GOVERNANCE_FETCH_BASIC" fetch --no-tags origin '
        '"+refs/pull/$GOVERNANCE_PR_NUMBER/head:$GOVERNANCE_PULL_REF"'
    )
    assert fetch_command in governance_step
    assert "unset GOVERNANCE_FETCH_TOKEN GOVERNANCE_FETCH_BASIC" in governance_step
    assert (
        'test "$(git rev-parse "$GOVERNANCE_PULL_REF^{commit}")" = '
        '"$GOVERNANCE_HEAD_REF"' in governance_step
    )
    assert 'git cat-file -e "$GOVERNANCE_BASE_REF^{commit}"' in governance_step
    assert 'git cat-file -e "$GOVERNANCE_HEAD_REF^{commit}"' in governance_step
    assert (
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"'
        in governance_step
    )
    assert (
        'git worktree add --detach "$GOVERNANCE_BASE_WORKTREE" "$GOVERNANCE_BASE_REF"'
        in governance_step
    )
    assert (
        'git worktree add --detach "$GOVERNANCE_HEAD_WORKTREE" "$GOVERNANCE_HEAD_REF"'
        in governance_step
    )
    assert (
        'python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check'
        in governance_step
    )
    assert (
        'python -P "$GOVERNANCE_BASE_WORKTREE/tools/architecture_governance.py" check'
        in governance_step
    )
    assert "python tools/code_governance.py" not in governance_step
    assert "python tools/architecture_governance.py" not in governance_step
    assert "git checkout" not in governance_step
    assert '--base-ref "$GOVERNANCE_BASE_REF"' in governance_step
    assert '--head-ref "$GOVERNANCE_HEAD_REF"' in governance_step
    assert '--authority-ref "$GOVERNANCE_BASE_REF"' in governance_step
    assert "--format text" in governance_step

    governance_run = governance_step.split("run: |", 1)[1]
    assert "${{" not in governance_run
    assert "github.event.pull_request.head.ref" not in workflow
    assert "github.event.pull_request.base.ref" not in workflow
    assert "github.head_ref" not in workflow

    governance_lines = [
        line.strip() for line in governance_run.splitlines() if line.strip()
    ]
    fetch_index = governance_lines.index(fetch_command)
    unset_index = governance_lines.index(
        "unset GOVERNANCE_FETCH_TOKEN GOVERNANCE_FETCH_BASIC"
    )
    fetched_ref_check_index = governance_lines.index(
        'test "$(git rev-parse "$GOVERNANCE_PULL_REF^{commit}")" = "$GOVERNANCE_HEAD_REF"'
    )
    ancestry_index = governance_lines.index(
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"'
    )
    base_worktree_index = governance_lines.index(
        'git worktree add --detach "$GOVERNANCE_BASE_WORKTREE" "$GOVERNANCE_BASE_REF"'
    )
    governance_command_index = next(
        index
        for index, line in enumerate(governance_lines)
        if 'python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check'
        in line
    )
    architecture_command_index = next(
        index
        for index, line in enumerate(governance_lines)
        if 'python -P "$GOVERNANCE_BASE_WORKTREE/tools/architecture_governance.py" check'
        in line
    )
    assert (
        governance_lines.index('echo "::add-mask::$GOVERNANCE_FETCH_BASIC"')
        < fetch_index
    )
    assert unset_index == fetch_index + 1
    assert fetch_index < fetched_ref_check_index < ancestry_index < base_worktree_index
    assert base_worktree_index < governance_command_index
    assert governance_command_index < architecture_command_index


def test_main_push_governance_uses_the_previous_exact_main_as_authority():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    governance_step = workflow.split("- name: Run code and architecture governance", 1)[1].split(
        "- name: Checkout validated pull request head for existing checks", 1
    )[0]

    assert "github.event.pull_request.base.sha || github.event.before" in governance_step
    assert "github.event.pull_request.head.sha || github.sha" in governance_step
    assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then' in governance_step
    assert 'test "$GITHUB_EVENT_NAME" = "push"' in governance_step
    assert (
        'test "$GOVERNANCE_BASE_REF" != "0000000000000000000000000000000000000000"'
        in governance_step
    )
    assert 'unset GOVERNANCE_FETCH_TOKEN' in governance_step
    assert (
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"'
        in governance_step
    )
    assert '--authority-ref "$GOVERNANCE_BASE_REF"' in governance_step


def test_pull_request_target_governance_is_immutable_and_never_executes_candidate_code():
    workflow = TRUSTED_GOVERNANCE_WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)

    assert parsed["on"] == {"pull_request_target": {"branches": ["main"]}}
    assert parsed["permissions"] == {"contents": "read"}
    assert set(parsed["jobs"]) == {"trusted-governance"}
    trusted_job = parsed["jobs"]["trusted-governance"]
    assert trusted_job["name"] == "trusted architecture and code governance"
    assert trusted_job["timeout-minutes"] == "10"
    assert "continue-on-error" not in trusted_job
    assert all("continue-on-error" not in step for step in trusted_job["steps"])
    assert "pull_request:\n" not in workflow
    assert "push:\n" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "persist-credentials: false" in workflow
    assert "persist-credentials: true" not in workflow
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in workflow
    assert "python -m pip install ruff==0.11.13 PyYAML==6.0.3" in workflow
    assert 'PYTHONSAFEPATH: "1"' in workflow
    assert "uv sync" not in workflow
    assert "pytest" not in workflow
    assert "docker " not in workflow

    immutable_step = workflow.split("- name: Run immutable exact-range governance", 1)[1]
    assert '[[ "$GOVERNANCE_BASE_REF" =~ ^[0-9a-f]{40}$ ]]' in immutable_step
    assert '[[ "$GOVERNANCE_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in immutable_step
    assert (
        'GOVERNANCE_PULL_REF="refs/remotes/origin/pull/$GOVERNANCE_PR_NUMBER/head"'
        in immutable_step
    )
    assert (
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"'
        in immutable_step
    )
    assert "class UniqueKeyLoader(yaml.BaseLoader):" in immutable_step
    assert 'for key in ("permissions", "concurrency", "env"):' in immutable_step
    assert "if head_events != base_events:" in immutable_step
    assert "if head_jobs != base_jobs:" in immutable_step
    assert 'head_backend_events.get("push") != base_backend_events.get("push")' in immutable_step
    assert 'protected_step_name = "Run code and architecture governance"' in immutable_step
    assert "if head_validation_contract != base_validation_contract:" in immutable_step
    assert "if len(base_protected) != 1 or head_protected != base_protected:" in immutable_step
    assert "if not isinstance(base_required, dict) or head_required != base_required:" in immutable_step
    assert (
        'python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check'
        in immutable_step
    )
    assert (
        'python -P "$GOVERNANCE_BASE_WORKTREE/tools/architecture_governance.py" check'
        in immutable_step
    )
    assert '--authority-ref "$GOVERNANCE_BASE_REF"' in immutable_step
    assert "python tools/code_governance.py" not in immutable_step
    assert "python tools/architecture_governance.py" not in immutable_step


def test_code_governance_rejects_credential_and_untrusted_ref_fallbacks():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    governance_step = workflow.split("- name: Run code and architecture governance", 1)[1].split(
        "- name: Checkout validated pull request head for existing checks", 1
    )[0]
    normalized = governance_step.lower()

    assert "authorization: bearer" not in normalized
    assert 'fetch --no-tags origin "$governance_head_ref"' not in normalized
    assert "refs/heads/" not in normalized
    assert "git config" not in normalized
    assert "--local" not in normalized
    assert "--global" not in normalized
    assert "http.extraheader" not in normalized
    assert "continue-on-error:" not in governance_step
    assert "|| true" not in governance_step
    assert "set +e" not in governance_step


def test_python_safe_path_blocks_a_head_root_ruff_module(tmp_path: Path):
    (tmp_path / "ruff.py").write_text('raise RuntimeError("head ruff.py was imported")\n', encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONSAFEPATH"] = "1"
    environment.pop("PYTHONPATH", None)

    child = "import subprocess, sys; raise SystemExit(subprocess.run([sys.executable, '-m', 'ruff', '--version']).returncode)"
    completed = subprocess.run(
        [sys.executable, "-P", "-c", child],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "head ruff.py was imported" not in completed.stderr


def test_code_governance_uses_exact_trusted_base_bootstrap_and_propagates_pr_failures():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    install_start = workflow.index("- name: Install trusted-base governance dependency")
    governance_start = workflow.index("- name: Run code and architecture governance")
    governance_step = workflow[governance_start : workflow.index("  backend-tests:")]

    assert install_start < governance_start
    assert "python -m pip install ruff==0.11.13" in workflow
    assert "python -m pip install --upgrade pip" not in workflow
    assert "uv lock --check" in workflow
    assert "uv sync --locked --extra test --no-install-project" in workflow
    assert "continue-on-error:" not in governance_step
    assert "|| true" not in governance_step
    assert "set +e" not in governance_step
    assert "ruff check ." not in workflow
    assert "ruff format" not in workflow


def test_existing_pr_checks_switch_to_the_validated_head_after_governance():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    governance_start = workflow.index("- name: Run code and architecture governance")
    head_checkout_start = workflow.index(
        "- name: Checkout validated pull request head for existing checks"
    )
    compile_start = workflow.index("- name: Compile backend sources")
    locked_install_start = workflow.index(
        "- name: Install candidate dependencies from the lock authority"
    )
    pytest_start = workflow.index("  backend-tests:")
    head_checkout = workflow[head_checkout_start:pytest_start]

    assert (
        governance_start
        < head_checkout_start
        < locked_install_start
        < compile_start
        < pytest_start
    )
    assert "if: github.event_name == 'pull_request'" in head_checkout
    assert (
        "VALIDATED_PR_HEAD_REF: ${{ github.event.pull_request.head.sha }}"
        in head_checkout
    )
    assert '[[ "$VALIDATED_PR_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in head_checkout
    assert (
        'test "$(git rev-parse "$VALIDATED_PR_HEAD_REF^{commit}")" = "$VALIDATED_PR_HEAD_REF"'
        in head_checkout
    )
    assert 'git checkout --detach "$VALIDATED_PR_HEAD_REF"' in head_checkout
    assert 'test "$(git rev-parse HEAD)" = "$VALIDATED_PR_HEAD_REF"' in head_checkout


def test_backend_image_job_builds_only_affected_pull_request_candidates_and_checks_runtime():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    image_job = workflow.split("  backend-image:", 1)[1].split("  required:", 1)[0]
    startup_step = image_job.split("- name: Verify backend image startup", 1)[1]

    assert "paths:" not in workflow
    assert "needs: backend-validation" in image_job
    assert "timeout-minutes: 30" in image_job
    assert "IMAGE_BASE_COMMIT: ${{ github.event.pull_request.base.sha }}" in image_job
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in image_job
    assert "fetch-depth: 0" in image_job
    assert "persist-credentials: false" in image_job
    assert "- name: Determine backend image impact" in image_job
    assert "id: image-scope" in image_job
    assert "python tools/ci_image_scope.py" in image_job
    assert '--event-name "$GITHUB_EVENT_NAME"' in image_job
    assert '--role backend' in image_job
    assert '--base-ref "$IMAGE_BASE_COMMIT"' in image_job
    assert '--head-ref "$IMAGE_SOURCE_COMMIT"' in image_job
    assert "- name: Report backend image validation not affected" in image_job
    assert "if: steps.image-scope.outputs.build != 'true'" in image_job
    assert "reason=packaged_inputs_unchanged" in image_job
    assert "base_commit=%s head_commit=%s" in image_job
    for step_name in [
        "Resolve image source repository",
        "Build backend image",
        "Block fixable backend image vulnerabilities",
        "Verify backend image runtime contract",
        "Verify backend image startup",
    ]:
        step = image_job.split(f"- name: {step_name}", 1)[1].split("\n      - name:", 1)[0]
        assert "if: steps.image-scope.outputs.build == 'true'" in step
    assert "IMAGE_SOURCE_HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}" in image_job
    assert "- name: Resolve image source repository" in image_job
    assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then' in image_job
    assert '[[ "$IMAGE_SOURCE_HEAD_REPOSITORY" =~ ^[A-Za-z0-9]' in image_job
    assert 'image_source_repository="https://github.com/${IMAGE_SOURCE_HEAD_REPOSITORY}.git"' in image_job
    assert 'printf \'IMAGE_SOURCE_REPOSITORY=%s\\n\' "$image_source_repository" >> "$GITHUB_ENV"' in image_job
    assert "IMAGE_SOURCE_REPOSITORY: https://github.com/${{ github.repository }}.git" not in image_job
    assert "docker build" in image_job
    assert "-f Dockerfile" in image_job
    assert "- name: Block fixable backend image vulnerabilities" in image_job
    assert (
        "uses: aquasecurity/trivy-action@"
        "ed142fd0673e97e23eac54620cfb913e5ce36c25"
    ) in image_job
    assert "image-ref: ai-platform-backend:${{ env.IMAGE_SOURCE_COMMIT }}" in image_job
    assert "severity: HIGH,CRITICAL" in image_job
    assert "ignore-unfixed: true" in image_job
    assert "exit-code: '1'" in image_job
    assert "uv.lock" not in image_job  # The real Docker build proves lock consumption.
    assert 'config["User"] == "10001:10001"' in image_job
    assert 'config["Entrypoint"] == ["/app/docker-entrypoint.sh"]' in image_job
    assert 'labels["org.opencontainers.image.revision"]' in image_job
    assert 'labels["ai-platform.source-repository"]' in image_job
    assert '--env IMAGE_SOURCE_COMMIT="$IMAGE_SOURCE_COMMIT"' in image_job
    assert "import app.main, claude_agent_sdk" in image_job
    assert "http://127.0.0.1:18020/api/ai/health" in image_job
    assert "python - <<'PY'" not in startup_step
    assert 'BACKEND_HEALTH_FILE="$RUNNER_TEMP/backend-health.json" python -c' in startup_step
    assert "backend_container_state=" in startup_step
    assert "docker logs --tail 80" in startup_step
    assert "backend_redacted_container_log_tail_lines=" in startup_step
    assert "backend_container_log_signal=redacted" in startup_step
    assert "| sed -E" not in startup_step
    assert "exit 1" in startup_step
    assert "docker push" not in image_job
    assert "docker compose" not in image_job.lower()
    required_job = workflow.split("  required:", 1)[1]
    assert "IMAGE_RESULT: ${{ needs.backend-image.result }}" in required_job
    assert "IMAGE_DISPOSITION" not in required_job


def test_backend_required_contract_preserves_high_risk_design_triggers():
    guidance = "\n".join(
        [
            AGENT_RULES.read_text(encoding="utf-8"),
            ISSUE_WORKFLOW.read_text(encoding="utf-8"),
        ]
    )

    for trigger in [
        "security",
        "auth",
        "tenant isolation",
        "release",
        "deployment",
        "runtime",
    ]:
        assert re.search(
            r"Create a separate design for.{0,160}" + re.escape(trigger),
            guidance,
            re.IGNORECASE | re.DOTALL,
        ), trigger
