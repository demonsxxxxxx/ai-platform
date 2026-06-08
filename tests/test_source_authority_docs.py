from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md"
ROADMAP = ROOT / "docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md"
GUARDRAILS = ROOT / "docs/agent-rules/ai-platform-guardrails.md"
AGENTS = ROOT / "AGENTS.md"
COMPOSE = ROOT / "deploy/ai-platform/docker-compose.yml"
ENV_EXAMPLE = ROOT / "deploy/ai-platform/.env.example"
DOCKERIGNORE = ROOT / ".dockerignore"
GITIGNORE = ROOT / ".gitignore"
FRONTEND_WEB = ROOT / "frontend/web"
FRONTEND_README = FRONTEND_WEB / "README.md"
FRONTEND_MIGRATION_DOC = ROOT / "docs/frontend/ai-platform-frontend-migration.md"
CAPACITY_BASELINE_DOC = ROOT / "docs/operations/ai-platform-capacity-baseline.md"
OBSERVABILITY_READINESS_DOC = ROOT / "docs/operations/ai-platform-observability-readiness.md"

AUTHORITY_DOCS = [PRD, ROADMAP, GUARDRAILS, AGENTS]
TARGET_211_BACKEND = "/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform"
TARGET_211_DEPLOY = "/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform"
STALE_LOCAL_PATHS = [
    "webUI/services/ai-platform",
    "src/AI/agent-workbench",
    "/api/ai/workbench",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_guardrails_document_exists_and_is_named_by_authority_docs():
    assert GUARDRAILS.exists()
    guardrails_text = read(GUARDRAILS)
    assert "ai-platform Guardrails" in guardrails_text
    assert "Current Source Boundaries" in guardrails_text
    assert "P0 Gate Order" in guardrails_text

    assert "docs/agent-rules/ai-platform-guardrails.md" in read(PRD)
    assert "docs/agent-rules/ai-platform-guardrails.md" in read(ROADMAP)
    assert "docs/agent-rules/ai-platform-guardrails.md" in read(AGENTS)


def test_source_authority_docs_keep_current_repo_and_211_deploy_boundary():
    for path in AUTHORITY_DOCS:
        text = read(path)
        assert "当前 `ai-platform` 仓库根目录" in text or "current `ai-platform` repository root" in text
        assert TARGET_211_BACKEND in text
        assert TARGET_211_DEPLOY in text
        assert "http://10.56.0.211:18001/" in text
        assert "ai-platform-api" in text
        assert "ai-platform-worker" in text
        for stale_path in STALE_LOCAL_PATHS:
            assert stale_path not in text


def test_default_compose_uses_current_repo_context_and_no_docker_socket():
    compose_text = read(COMPOSE)
    assert compose_text.count("context: ../..") == 2
    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text


def test_env_template_satisfies_required_runtime_defaults_without_real_secrets():
    env_text = read(ENV_EXAMPLE)
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" in env_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.25:7263" in env_text
    assert "EXISTING_USER_INFO_BASE_URL=http://10.56.0.25:5166" in env_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.211" not in env_text
    assert "sk-" not in env_text
    assert "Bearer " not in env_text


def test_docker_build_context_excludes_real_env_files():
    dockerignore_lines = set(read(DOCKERIGNORE).splitlines())
    required_patterns = {
        ".env",
        ".env.*",
        "deploy/ai-platform/.env",
        "deploy/ai-platform/.env.*",
        ".tmp/",
        "pytest-of-*/",
        "*.egg-info/",
        "frontend/web/node_modules/",
        "frontend/web/dist/",
        "frontend/web/.env",
        "frontend/web/.env.*",
        "frontend/web/*.tsbuildinfo",
    }

    assert required_patterns.issubset(dockerignore_lines)
    assert "repo-local Docker build context" in read(GUARDRAILS)


def test_compose_build_does_not_forward_secret_capable_package_index_args():
    compose_text = read(COMPOSE)

    assert "PIP_INDEX_URL" not in compose_text
    assert "PIP_TRUSTED_HOST" not in compose_text


def test_agents_lock_211_runtime_verification_and_rebase_deploy_rules():
    agents_text = read(AGENTS)
    generator_text = read(ROOT / "scripts/generate_sandbox_runtime_evidence_211.py")

    assert "python3" in agents_text
    assert '--docker-cmd "sudo -n docker"' in agents_text
    assert "--cancel-image ai-platform:local" in agents_text
    assert "rebasing from the current/backup image" in agents_text
    assert "compose with `--no-build`" in agents_text
    assert '"ai-platform:local"' in generator_text
    assert "busybox" not in generator_text


def test_gitignore_excludes_real_env_variants_but_not_templates():
    gitignore_lines = set(read(GITIGNORE).splitlines())
    required_patterns = {
        ".env",
        ".env.*",
        "!.env.example",
        "deploy/ai-platform/.env",
        "deploy/ai-platform/.env.*",
        "!deploy/ai-platform/.env.example",
        "frontend/web/node_modules/",
        "frontend/web/dist/",
        "frontend/web/.env",
        "frontend/web/.env.*",
        "!frontend/web/.env.example",
        "frontend/web/*.tsbuildinfo",
    }

    assert required_patterns.issubset(gitignore_lines)


def test_frontend_source_import_is_documented_without_replacing_current_runtime():
    package_json = FRONTEND_WEB / "package.json"
    vite_config = read(FRONTEND_WEB / "vite.config.ts")
    api_config = read(FRONTEND_WEB / "src/services/api/config.ts")

    assert package_json.exists()
    assert FRONTEND_README.exists()
    assert FRONTEND_MIGRATION_DOC.exists()
    assert "VITE_AI_PLATFORM_API_TARGET" in vite_config
    assert "VITE_API_TARGET" not in vite_config
    assert "VITE_API_BASE" not in api_config

    combined_text = read(FRONTEND_README) + "\n" + read(FRONTEND_MIGRATION_DOC)
    assert "same-origin `/api/*`" in combined_text
    assert "public/admin projections" in combined_text
    assert "executor private payload" in combined_text
    assert "Backend scheduling, sandbox, auth/session, DB schema" in combined_text
    assert "deploy/ai-platform/docker-compose.yml` is not changed" in combined_text
    assert "ai-platform-frontend" in combined_text
    assert "current 211 thin-shell deployment remains the active runtime entry" in combined_text
    assert "G8/G10 Long Task and Multi-Agent work are not implemented" in combined_text
    assert "Docker compose one-command startup is not a current" in combined_text
    assert "tools/office_context_readiness.py" in combined_text
    assert "frontend context provenance acceptance" in combined_text
    assert "C:\\Users" not in combined_text
    assert "/api/ai/workbench" not in combined_text


def test_frontend_readme_matches_current_projection_audit_gate():
    readme_text = read(FRONTEND_README)

    assert "pass_with_policy_gaps" in readme_text
    assert "expected to fail" not in readme_text.lower()
    assert "continues to lint, type-check, and build" in readme_text
    assert "G6/G9" in readme_text


def test_capacity_docs_record_machine_readable_gate_evidence_contract():
    capacity_text = read(CAPACITY_BASELINE_DOC)
    roadmap_text = read(ROADMAP)

    for text in (capacity_text, roadmap_text):
        assert "recorded_gate_evidence_contract" in text
        assert "ai-platform.capacity-recorded-gate-evidence-contract.v1" in text
        assert "tools/capacity_profile_readiness.py" in text
        assert "ai-platform.capacity-profile-readiness.v1" in text
        assert "operator_review_required_before_default_change" in text
        assert "load_test_evidence.gate_evidence.<gate>" in text
        assert "does not raise production concurrency defaults" in text
        assert "C:\\Users" not in text


def test_observability_docs_record_quality_golden_set_contract_without_closing_g9():
    observability_text = read(OBSERVABILITY_READINESS_DOC)
    roadmap_text = read(ROADMAP)

    for text in (observability_text, roadmap_text):
        assert "ai-platform.quality-golden-set-readiness.v1" in text
        assert "ai-platform.golden-set-eval-evidence-contract.v1" in text
        assert "quality_evaluation.golden_set_runs.<eval_run_id>" in text
        assert "contract-only" in text
        assert "does not close G9" in text
        assert "golden-set evaluation runtime and 211 acceptance remain open" in text
        assert "C:\\Users" not in text


def test_frontend_docs_record_packaged_runtime_smoke_contract_and_211_blocker():
    frontend_text = read(FRONTEND_MIGRATION_DOC)
    roadmap_text = read(ROADMAP)

    for text in (frontend_text, roadmap_text):
        assert "tools/frontend_packaged_runtime_smoke.py" in text
        assert "ai-platform.frontend-packaged-runtime-smoke.v1" in text
        assert "ai-platform.frontend-packaged-runtime-smoke-evidence.v1" in text
        assert "frontend_release.packaged_runtime_smoke.<commit_sha>" in text
        assert "305bc40" in text
        assert "docker_registry_proxy_unreachable" in text
        assert "base_image_pull_failed" in text
        assert "node:22-alpine" in text
        assert "nginx:1.27-alpine" in text
        assert "not release acceptance" in text
        assert "C:\\Users" not in text
