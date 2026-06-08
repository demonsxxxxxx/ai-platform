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
GOVERNANCE_READINESS_DOC = ROOT / "docs/operations/ai-platform-governance-readiness.md"
RELEASE_EVIDENCE_INDEX = ROOT / "docs/release-evidence/README.md"
SCHEMA = ROOT / "app/schema.sql"

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


def test_schema_indexes_admin_tool_policy_history_audit_projection():
    schema_text = read(SCHEMA)

    assert "idx_audit_logs_tool_policy_history" in schema_text
    assert "on audit_logs(tenant_id, target_type, action, target_id, created_at desc, id desc)" in schema_text
    assert "idx_audit_logs_tool_policy_history_latest" in schema_text
    assert "on audit_logs(tenant_id, target_type, action, created_at desc, id desc)" in schema_text


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
        assert "tools/capacity_bounded_load_harness.py" in text
        assert "ai-platform.capacity-bounded-load-harness.v1" in text
        assert "probe_only_not_recorded" in text
        assert "not accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence" in text
        assert "tools/capacity_evidence_bundle.py" in text
        assert "ai-platform.capacity-evidence-bundle.v1" in text
        assert "draft_not_recorded" in text
        assert "assemble_evidence_bundle_draft" in text
        assert "tools/capacity_recorded_gate_snapshot.py" in text
        assert "ai-platform.capacity-recorded-gate-snapshot.v1" in text
        assert "ai-platform.capacity-recorded-gate-evidence.v1" in text
        assert "assemble_recorded_gate_snapshot" in text
        assert "ai-platform.model-gateway-backpressure-policy.v1" in text
        assert "MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT" in text
        assert "model_gateway_timeout_and_backpressure" in text
        assert "contract-only" in text
        assert "--start-runtime-evidence-json capacity-runtime-evidence-start.json" in text
        assert "--cleanup-proof-json capacity-cleanup-proof-api-read-write-burst.json" in text
        assert "capacity-cleanup-proof-api-read-write-burst.json" in text
        assert "C:\\Users" not in text


def test_capacity_docs_record_latest_211_bounded_probe_without_closing_gate():
    capacity_text = read(CAPACITY_BASELINE_DOC)

    assert "3d607c96b8d8e21f59461bd94cc4b64de1d49dd5" in capacity_text
    assert "ai-platform:3d607c9-g9-latency-acceptance" in capacity_text
    assert "probe_completed_not_gate_evidence" in capacity_text
    assert "sent_requests = 20" in capacity_text
    assert "status counts were `{\"200\": 20}`" in capacity_text
    assert "does_not_mark_gate_recorded = true" in capacity_text
    assert "not accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence" in capacity_text
    assert "still does not close #21" in capacity_text
    assert "must not be used to raise production defaults" in capacity_text
    assert "C:\\Users" not in capacity_text


def test_observability_docs_record_quality_golden_set_contract_without_closing_g9():
    observability_text = read(OBSERVABILITY_READINESS_DOC)
    roadmap_text = read(ROADMAP)
    release_evidence_text = read(RELEASE_EVIDENCE_INDEX)

    for text in (observability_text, roadmap_text):
        assert "latency percentiles p50/p95/p99" in text
        assert "latency_percentiles_p50_p95_p99_admin_projection" in text
        assert "latency_percentile_runtime_211_acceptance" not in text
        assert "latency_percentile_per_surface_split_and_dashboard_acceptance" in text
        assert "tools/error_taxonomy_dashboard_readiness.py" in text
        assert "ai-platform.error-taxonomy-dashboard-readiness.v1" in text
        assert "ai-platform.error-taxonomy-dashboard-contract.v1" in text
        assert "error_taxonomy_dashboard_contract" in text
        assert "error_taxonomy_dashboard_runtime_acceptance" in text
        assert "error_taxonomy_dashboard_visual_acceptance" in text
        assert "error_taxonomy_dashboard_211_acceptance" in text
        assert "error_taxonomy_dashboard_acceptance" not in text
        assert "ai-platform.model-gateway-backpressure-policy.v1" in text
        assert "model_gateway_backpressure_policy_contract" in text
        assert "model_gateway_timeout_and_backpressure" in text
        assert "do_not_raise_without_recorded_load_test_evidence" in text
        assert "ai-platform.quality-golden-set-readiness.v1" in text
        assert "ai-platform.golden-set-eval-evidence-contract.v1" in text
        assert "quality_evaluation.golden_set_runs.<eval_run_id>" in text
        assert "ai-platform.alert-delivery-channel-policy.v1" in text
        assert "alert_delivery_channel_policy_contract" in text
        assert "alert_delivery_channel_runtime_acceptance" in text
        assert "`alert_delivery_channel_policy`" not in text
        assert "contract-only" in text
        assert "does not close G9" in text
        assert "golden-set evaluation runtime and 211 acceptance remain open" in text
        assert "C:\\Users" not in text

    for text in (observability_text, roadmap_text, release_evidence_text):
        assert "tools/release_evidence_readiness.py" in text
        assert "ai-platform.release-evidence-readiness.v1" in text
        assert "docs/release-evidence/" in text
        assert "ai-platform.release-evidence-entry.v1" in text
        assert "ai-platform.release-evidence-retention-policy.v1" in text
        assert "release_evidence_runtime_export_acceptance" in text
        assert "release_evidence_retention_runtime_acceptance" in text
        assert "`release_evidence_retention_policy`" not in text
        assert "does not close G9" in text
        assert "executor_private_payload" not in text
        assert "raw_storage_key" not in text
        assert "sandbox_workdir" not in text
        assert "api_key" not in text
        assert "C:\\Users" not in text

    for text in (observability_text, roadmap_text, release_evidence_text):
        assert "tools/trace_audit_export_readiness.py" in text
        assert "ai-platform.trace-audit-export-readiness.v1" in text
        assert "ai-platform.trace-audit-export-contract.v1" in text
        assert "audit.trace_exports.<export_id>" in text
        assert "trace_audit_export_contract" in text
        assert "trace_audit_export_runtime_acceptance" in text
        assert "trace_audit_export_dashboard_acceptance" in text
        assert "trace_audit_export_211_acceptance" in text
        assert "run_event_public_projection" in text
        assert "audit_event_public_projection" in text
        assert "does not close G9" in text
        assert "executor_private_payload" not in text
        assert "raw_storage_key" not in text
        assert "sandbox_workdir" not in text
        assert "api_key" not in text
        assert "C:\\Users" not in text


def test_governance_docs_record_skill_dependency_review_policy_without_closing_g6():
    governance_text = read(GOVERNANCE_READINESS_DOC)
    roadmap_text = read(ROADMAP)

    for text in (governance_text, roadmap_text):
        assert "tools/skill_release_readiness.py" in text
        assert "ai-platform.skill-release-readiness.v1" in text
        assert "ai-platform.skill-dependency-review-policy.v1" in text
        assert "ai-platform.skill-release-review.v1" in text
        assert "sbom_reviewed" in text
        assert "license_policy_reviewed" in text
        assert "vulnerability_reviewed" in text
        assert "dependency_vulnerability_or_license_policy" in text
        assert "skill_dependency_review_policy_runtime_acceptance" in text
        assert "does not close G6" in text
        assert "executor_private_payload" not in text
        assert "raw_storage_key" not in text
        assert "sandbox_workdir" not in text
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


def test_prd_records_claude_code_deerflow_boundary_without_second_runtime():
    prd_text = read(PRD)
    roadmap_text = read(ROADMAP)

    assert "Claude Code / Claude Agent SDK 是当前首选执行内核" in prd_text
    assert "DeerFlow 只吸收为平台级 long-horizon product contract" in prd_text
    assert "不是第二套 runtime 或控制面" in prd_text
    assert "executor-private logs" in prd_text
    assert "不能成为 platform source of truth" in prd_text
    assert "不要复制 DeerFlow 作为第二控制面" in prd_text
    assert "不要把 Claude Code 内部 subagents 等同于平台级 multi-run scheduling" in prd_text
    assert "Platform Product Contract Gates" in prd_text
    assert "Long Task Product Contract Gate (DeerFlow pattern)" in prd_text
    assert "Long Task Product Contract Adapter (DeerFlow pattern)" not in prd_text

    assert "Long Task Product Contract / Office Artifact Flow" in roadmap_text
    for required in (
        "parent / child run decomposition and state ledger",
        "subagent progress stream and concurrency limits",
        "artifact ledger, preview, download, versioning, and reuse",
        "context pack, long-task context compression, resume, and replay",
        "cancel / retry / timeout semantics owned by the platform",
    ):
        assert required in roadmap_text
    assert "DeerFlow is not a second runtime to clone" in roadmap_text
    assert "C:\\Users" not in prd_text
    assert "C:\\Users" not in roadmap_text
