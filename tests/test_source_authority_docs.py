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
    dockerignore_text = read(DOCKERIGNORE)
    required_patterns = {
        ".env",
        ".env.*",
        "deploy/ai-platform/.env",
        "deploy/ai-platform/.env.*",
        ".tmp/",
        "pytest-of-*/",
        "*.egg-info/",
    }

    assert required_patterns.issubset(set(dockerignore_text.splitlines()))
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
    }

    assert required_patterns.issubset(gitignore_lines)
