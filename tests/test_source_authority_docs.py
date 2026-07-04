import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md"
OLD_PRD = ROOT / "docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md"
TECH_ACCEPTANCE = ROOT / "docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md"
BACKEND_PRD = ROOT / "docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md"
ROADMAP = ROOT / "docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md"
G7_B3_EVIDENCE_CLOSURE_PLAN = ROOT / "docs/superpowers/plans/2026-07-02-g7-b3-evidence-closure.md"
GUARDRAILS = ROOT / "docs/agent-rules/ai-platform-guardrails.md"
MULTI_AGENT_CONTEXT_WORKFLOW = ROOT / "docs/agent-rules/multi-agent-context-workflow.md"
GITHUB_WORKFLOW = ROOT / "docs/agent-rules/github-issue-pr-workflow.md"
AGENTS = ROOT / "AGENTS.md"
BACKEND_DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "deploy/ai-platform/docker-compose.yml"
ENV_EXAMPLE = ROOT / "deploy/ai-platform/.env.example"
DOCKERIGNORE = ROOT / ".dockerignore"
GITIGNORE = ROOT / ".gitignore"
FRONTEND_WEB = ROOT / "frontend/web"
FRONTEND_README = FRONTEND_WEB / "README.md"
FRONTEND_MIGRATION_DOC = ROOT / "docs/frontend/ai-platform-frontend-migration.md"
FRONTEND_PRD_CLOSURE_MATRIX = ROOT / "docs/frontend/prd-frontend-closure-matrix.md"
SKILLS_MARKETPLACE_PUBLIC_API = ROOT / "docs/frontend/skills-marketplace-public-api.md"
CAPACITY_BASELINE_DOC = ROOT / "docs/operations/ai-platform-capacity-baseline.md"
OBSERVABILITY_READINESS_DOC = ROOT / "docs/operations/ai-platform-observability-readiness.md"
GOVERNANCE_READINESS_DOC = ROOT / "docs/operations/ai-platform-governance-readiness.md"
GATE_STATUS_DOC = ROOT / "docs/operations/ai-platform-gate-status.md"
FOUNDATION_ALPHA_CLOSURE_DOC = ROOT / "docs/operations/ai-platform-foundation-alpha-closure.md"
RELEASE_EVIDENCE_INDEX = ROOT / "docs/release-evidence/README.md"
CURRENT_G7_B3_SANDBOX_DIAGNOSTIC = (
    ROOT
    / "docs/release-evidence/diagnostics/2026-07-04-211-b3-sandbox-observation-61073b1.json"
)
POST_PR317_B3_SANDBOX_DIAGNOSTIC = (
    ROOT
    / "docs/release-evidence/diagnostics/2026-07-04-211-b3-host-sandbox-observation-bbe23d5.json"
)
POST_PR319_B3_HOST_SANDBOX_OBSERVATION = (
    ROOT
    / "docs/release-evidence/diagnostics/2026-07-04-211-b3-host-sandbox-observation-a294727.json"
)
POST_PR321_B3_HOST_SANDBOX_OBSERVATION = (
    ROOT
    / "docs/release-evidence/diagnostics/2026-07-05-211-b3-host-sandbox-observation-945db2b.json"
)
SOURCE_RUNTIME_RELATION_MANIFEST = (
    ROOT / "docs/release-evidence/foundation-alpha-poc/source-runtime-relation-manifest.json"
)
ACTIVE_RUNTIME_SUBJECT_SHA = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"
ACTIVE_SOURCE_TREE_SHA = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"
CURRENT_SOURCE_RUNTIME_RELATION_SHA = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"
LATEST_VERIFIED_FRC_RUNTIME_SUBJECT_SHA = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"
CURRENT_MAIN_SOURCE_SHA = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"
AE6B7E5_CURRENT_MAIN_SHA = "ae6b7e52c656fd8296cf039834ce8d8559b01228"
PR297_G7_B3_SHA = "4805031fc3333ccbf38224172e4e85e21c0630bb"
PR304_G7_B3_SHA = "decf33a017e0b97e2a2992f80e3ccdc19152c1f4"
PR305_G7_B3_SHA = "28676df4abcbb7063211fceb4cc1701648c43d49"
PR306_G7_B3_SHA = "9c669761bbb4bd719af64a341d361b7c3b3e380e"
PR308_G7_B3_SHA = "15903fdfe96ffcfba9daa1252741111017dcf832"
PR311_G7_B3_SHA = "40691c01d64d6cd604dd94e6fc24ee6babdf0cad"
PR312_G7_B3_SHA = "881493d042a522b343c9df2044bd3830fd02e62f"
HISTORICAL_DIRTY_G7_B3_RUNTIME_SHA = "755e50ea2ad08c2d4218ae5d8cc612970b19e2a4"
CURRENT_G7_B3_RUNTIME_SHA = "61073b16a5b2c135e7ee467434ab39502ca3d194"
CURRENT_G7_B3_RUNTIME_SHORT_SHA = CURRENT_G7_B3_RUNTIME_SHA[:7]
POST_PR317_G7_B3_RUNTIME_SHA = "bbe23d53d14398378b4870de4cbf4bec0b045193"
POST_PR317_G7_B3_RUNTIME_SHORT_SHA = POST_PR317_G7_B3_RUNTIME_SHA[:7]
POST_PR319_G7_B3_RUNTIME_SHA = "a294727046024958c41b15f646512e68f3c04b47"
POST_PR319_G7_B3_RUNTIME_SHORT_SHA = POST_PR319_G7_B3_RUNTIME_SHA[:7]
POST_PR321_G7_B3_RUNTIME_SHA = "945db2bb5926ad7b01ead98c3283d55b77d2677d"
POST_PR321_G7_B3_RUNTIME_SHORT_SHA = POST_PR321_G7_B3_RUNTIME_SHA[:7]
CURRENT_G7_B3_FRC_EVIDENCE_DIR = (
    ROOT
    / "docs/release-evidence/foundation-runtime-concurrency/"
    / f"{CURRENT_G7_B3_RUNTIME_SHA}-frc-g7-b3-20260703"
)
CURRENT_G7_B3_FRC_EVIDENCE = (
    CURRENT_G7_B3_FRC_EVIDENCE_DIR
    / f"2026-07-03-211-foundation-alpha-poc-{CURRENT_G7_B3_RUNTIME_SHORT_SHA}-foundation-runtime-concurrency.json"
)
CURRENT_G7_B3_FRC_READINESS = (
    CURRENT_G7_B3_FRC_EVIDENCE_DIR
    / f"2026-07-03-211-foundation-alpha-poc-{CURRENT_G7_B3_RUNTIME_SHORT_SHA}-foundation-runtime-concurrency-readiness.json"
)
CURRENT_G7_B3_FRC_SUMMARY = (
    CURRENT_G7_B3_FRC_EVIDENCE_DIR
    / f"2026-07-03-211-foundation-alpha-poc-{CURRENT_G7_B3_RUNTIME_SHORT_SHA}-foundation-runtime-concurrency-summary.md"
)
POST_PR299_MAIN_SHA = "ba81a0b18da4d4d30c1a8ce44d4bf03bb051fca8"
ACTIVE_RUNTIME_SUBJECT_SHORT_SHA = ACTIVE_RUNTIME_SUBJECT_SHA[:7]
CURRENT_SOURCE_FRC_EVIDENCE_DIR = (
    ROOT
    / "docs/release-evidence/foundation-runtime-concurrency/"
    / f"{LATEST_VERIFIED_FRC_RUNTIME_SUBJECT_SHA}-frc-b0-20260630"
)
CURRENT_SOURCE_FRC_EVIDENCE = (
    CURRENT_SOURCE_FRC_EVIDENCE_DIR
    / f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-foundation-runtime-concurrency.json"
)
CURRENT_SOURCE_FRC_READINESS = (
    CURRENT_SOURCE_FRC_EVIDENCE_DIR
    / f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-foundation-runtime-concurrency-readiness.json"
)
CURRENT_SOURCE_FRC_SUMMARY = (
    CURRENT_SOURCE_FRC_EVIDENCE_DIR
    / f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-foundation-runtime-concurrency-summary.md"
)
FOUNDATION_ALPHA_BASELINE_RUNTIME_SUBJECT_SHA = "380de6bf9ffed5167f9bb2eaee8e63612a52c124"
ACTIVE_CLOSURE_SOURCE_TREE_SHA = "3c06c5351517028111c18a365ff9a24ed22ffa33"
FOUNDATION_ALPHA_BASELINE_RUNTIME_IMAGE = "ai-platform:380de6b-merged-main-runtime"
FOUNDATION_ALPHA_BASELINE_RUNTIME_IMAGE_ID = "sha256:e36e4dfad072cdd12b841019db3ccbcdef4b63ccf5262869c994757fef5663f9"
ACTIVE_RUNTIME_IMAGE = "ai-platform:96f27bb-b0-current-source-runtime-only-v2"
ACTIVE_RUNTIME_IMAGE_ID = "sha256:2640a006b4995bc01ebba965dc6b5b22be1bd28f6babc4b5a9bee7c91ce71e17"
ACTIVE_POC_SMOKE_EVIDENCE_FILE_ID = f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-runtime-poc-smoke"
ACTIVE_AUTH_RBAC_EVIDENCE_FILE_ID = f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-auth-rbac-smoke"
ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_FILE_ID = (
    f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-governance-runtime-smoke"
)
ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_FILE_ID = (
    f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-release-evidence-runtime-acceptance"
)
ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_FILE_ID = (
    f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-alert-trace-export-runtime-acceptance"
)
ACTIVE_POC_SMOKE_EVIDENCE_ID = ACTIVE_POC_SMOKE_EVIDENCE_FILE_ID
ACTIVE_AUTH_RBAC_EVIDENCE_ID = ACTIVE_AUTH_RBAC_EVIDENCE_FILE_ID
ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_ID = ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_FILE_ID
ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_ID = ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_FILE_ID
ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_ID = ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_FILE_ID
CBBFAFF_RUNTIME_SUBJECT_SHA = "cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55"
CBBFAFF_FRONTEND_PACKAGED_RUNTIME_BLOCKED_EVIDENCE_ID = (
    "2026-06-13-211-foundation-alpha-poc-cbbfaff-frontend-packaged-runtime-smoke-blocked"
)
FOUNDATION_ALPHA_POC_EVIDENCE = (
    ROOT
    / "docs/release-evidence/foundation-alpha-poc/3874281276c84a418bd08bda56d7ea55b52970b7/2026-06-11-211-foundation-alpha-poc-smoke.json"
)
FOUNDATION_ALPHA_POC_MERGED_EVIDENCE = (
    ROOT
    / "docs/release-evidence/foundation-alpha-poc/bf20432f9889efa8b367afdf512c641068ba30bc/2026-06-11-211-foundation-alpha-poc-merged-smoke.json"
)
FOUNDATION_ALPHA_POC_AUTH_RBAC_EVIDENCE = (
    ROOT
    / "docs/release-evidence/foundation-alpha-poc/bf20432f9889efa8b367afdf512c641068ba30bc/2026-06-11-211-foundation-alpha-poc-auth-rbac-smoke.json"
)
FOUNDATION_ALPHA_POC_CURRENT_MAIN_SMOKE_EVIDENCE = (
    ROOT
    / "docs/release-evidence/foundation-alpha-poc/8c0cffca63bc747fad0a5771f209acc8a608ab9e/2026-06-11-211-foundation-alpha-poc-current-main-smoke.json"
)
FOUNDATION_ALPHA_POC_CURRENT_MAIN_AUTH_RBAC_EVIDENCE = (
    ROOT
    / "docs/release-evidence/foundation-alpha-poc/8c0cffca63bc747fad0a5771f209acc8a608ab9e/2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json"
)
FOUNDATION_ALPHA_POC_ACTIVE_SMOKE_EVIDENCE = (
    ROOT
    / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_POC_SMOKE_EVIDENCE_FILE_ID}.json"
)
FOUNDATION_ALPHA_POC_ACTIVE_AUTH_RBAC_EVIDENCE = (
    ROOT
    / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_AUTH_RBAC_EVIDENCE_FILE_ID}.json"
)
FOUNDATION_ALPHA_POC_ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE = (
    ROOT
    / (
        "docs/release-evidence/foundation-alpha-poc/"
        f"{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_FILE_ID}.json"
    )
)
FOUNDATION_ALPHA_POC_ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE = (
    ROOT
    / (
        "docs/release-evidence/foundation-alpha-poc/"
        f"{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_FILE_ID}.json"
    )
)
FOUNDATION_ALPHA_POC_CBBFAFF_FRONTEND_PACKAGED_RUNTIME_BLOCKED_EVIDENCE = (
    ROOT
    / (
        "docs/release-evidence/foundation-alpha-poc/"
        f"{CBBFAFF_RUNTIME_SUBJECT_SHA}/{CBBFAFF_FRONTEND_PACKAGED_RUNTIME_BLOCKED_EVIDENCE_ID}.json"
    )
)
SCHEMA = ROOT / "app/schema.sql"

AUTHORITY_DOCS = [PRD, TECH_ACCEPTANCE, ROADMAP, GUARDRAILS, AGENTS]
TARGET_211_HOME_ROOT = "/home/" + "xinlin.jiang/"
TARGET_211_BACKEND = TARGET_211_HOME_ROOT + "ai-platform-phaseb/services/ai-platform"
TARGET_211_DEPLOY = TARGET_211_BACKEND + "/deploy/ai-platform"
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
    assert GITHUB_WORKFLOW.exists()
    guardrails_text = read(GUARDRAILS)
    assert "ai-platform Guardrails" in guardrails_text
    assert "Current Source Boundaries" in guardrails_text
    assert "P0 Gate Order" in guardrails_text

    assert "docs/agent-rules/ai-platform-guardrails.md" in read(PRD)
    assert "docs/agent-rules/github-issue-pr-workflow.md" in read(PRD)
    assert "docs/agent-rules/github-issue-pr-workflow.md" in read(GUARDRAILS)
    assert "docs/agent-rules/ai-platform-guardrails.md" in read(ROADMAP)
    assert "docs/agent-rules/ai-platform-guardrails.md" in read(AGENTS)


def test_agent_rules_keep_main_session_authority_separate_from_subagents():
    agents_text = read(AGENTS)
    workflow_text = read(MULTI_AGENT_CONTEXT_WORKFLOW)
    compact_agents_text = " ".join(agents_text.split())
    compact_workflow_text = " ".join(workflow_text.split())

    assert "Standing phrases such as `主线程全部授权`, `主线程有权限操作`, or `执行`" in agents_text
    assert (
        "do not grant sub-agents write, GitHub write, Docker, deployment, or remote runtime authority"
        in compact_agents_text
    )
    assert (
        "main-thread authorization is a direct-operation allowance, not a delegation allowance"
        in compact_workflow_text
    )
    assert (
        "Sub-agents stay read-only for GitHub, Docker, 211, deployment, and destructive operations"
        in compact_workflow_text
    )


def test_active_prd_v2_records_appendix_and_closure_workflow_authority():
    prd_text = read(PRD)
    old_prd_text = read(OLD_PRD)
    tech_text = read(TECH_ACCEPTANCE)
    workflow_text = read(GITHUB_WORKFLOW)
    compact_prd_text = " ".join(prd_text.split())
    compact_old_prd_text = " ".join(old_prd_text.split())
    compact_old_prd_without_quote_markers = compact_old_prd_text.replace("> ", "")

    assert "Status: active product PRD" in prd_text
    assert "S1 / Foundation Alpha historical baseline is" in prd_text
    assert "Current-source S1 status is not" in compact_prd_text
    assert "assumed from that closure" in compact_prd_text
    assert "Status: active companion acceptance document" in tech_text
    assert "The `9c669761` same-subject evidence pair can support `candidate_evidence_requires_review`" in tech_text
    assert "reviewed 2026-07-03 live-default G7/FRC evidence for `9c669761`" in tech_text
    assert "reviewed 2026-07-03 label-clean live-default G7/FRC evidence for historical `15903fd`" in tech_text
    assert "The historical `15903fd` label-clean evidence pair also records" in tech_text
    assert "The current post-PR #321 `945db2b` runtime subject now has source-marker reconciliation" in tech_text
    assert "all three API/worker in-container marker files" in tech_text
    assert "latest reviewed live-env G7 evidence and capacity visibility evidence remain historical `a294727` entries" not in tech_text
    assert "capacity visibility remains at `blocked_missing_load_test_evidence`" in tech_text
    assert "`g7_runtime_blocking_reasons=[]`" in tech_text
    assert "`status_upgrade_decision=not_approved_for_closure`" in tech_text
    assert "Same-subject Foundation Runtime concurrency evidence" in tech_text
    assert "an approved G7 status-upgrade artifact" in tech_text
    assert "B3 recorded load/profile evidence remain separate blockers" in tech_text
    assert "G7 remains blocked until Docker-provider smoke and hardening evidence exist" not in tech_text
    assert "Docker provider hardening and 211 smoke remain G7 blockers" not in tech_text
    assert "docs/operations/ai-platform-foundation-alpha-closure.md" in prd_text
    assert "docs/operations/ai-platform-foundation-alpha-closure.md" in tech_text
    assert "S2-0 latest-main runtime/concurrency/readiness refresh" in tech_text
    assert "2026-05-29 PRD remains a migration appendix" in prd_text
    assert "This PRD v2 is the active product source" in prd_text
    assert "tools/foundation_alpha_readiness.py --format json" in prd_text
    assert "tools/foundation_alpha_readiness.py --format json" in tech_text
    assert "docs/agent-rules/github-issue-pr-workflow.md" in prd_text
    assert "issue -> PR -> review -> merge -> deploy/smoke when required -> close issue with evidence" in prd_text
    assert "Use `Closes #N` or `Fixes #N` only when" in workflow_text
    assert OLD_PRD.exists()
    assert "Status: archived migration appendix" in old_prd_text
    assert "This 2026-05-29 PRD is no longer the active product authority" in old_prd_text
    assert "本文件曾是 `ai-platform` 产品方向总纲 PRD" in old_prd_text
    assert "当前产品方向的唯一总纲 PRD" not in old_prd_text
    assert (
        "G8 is a deferred platform-level multi-run orchestration parking-lot, "
        "while B3 SDK subagent fanout capacity evidence does not reopen or close G8"
    ) in compact_old_prd_without_quote_markers
    assert "G8 Deferred Platform Multi-Run Gate" in old_prd_text
    assert "历史标题曾写作 G8 Multi-Agent Controlled Beta" in old_prd_text
    assert "不再作为当前 beta 状态名" in old_prd_text
    assert "容量证据归 B3，不打开或关闭 G8" in old_prd_text
    assert "当前不按普通用户 multi-agent beta 推进" in old_prd_text
    assert "Long Task / Multi-Agent Runtime 仅作为历史 / deferred context 保留" in old_prd_text
    assert "SDK subagent use 属于 one governed platform run 内的执行层行为" in old_prd_text
    assert "平台级 multi-run 产品路线必须由未来重新打开的 G8 gate 定义" in compact_old_prd_text
    assert "Long Task / Multi-Agent Runtime 必须在 Foundation" not in old_prd_text
    assert "G8 Multi-Agent Controlled Beta |" not in old_prd_text
    assert "G8 Multi-Agent Controlled Beta，仅在前置 gate 通过后扩大" not in old_prd_text


def test_github_workflow_records_sdk_worker_diagnostic_layers():
    workflow_text = read(GITHUB_WORKFLOW)
    compact_workflow_text = " ".join(workflow_text.split())

    for expected in (
        "SDK / worker diagnostics must be layered",
        "Claude Agent SDK",
        "skill execution",
        "worker launch",
        "terminal execution",
        "user-facing runtime errors",
        "tool registration -> runner selection -> subprocess/terminal -> SDK event -> user-facing error",
        "minimal reproduction",
        "observable log or event evidence",
        "Generic `sdk_error`",
        "empty Bash input loops",
        "terminal run failures",
        "missing native skill evidence",
        "platform-controlled runner selection",
        "classified at the layer where they are observed",
    ):
        assert expected in compact_workflow_text

    assert (
        "Local diagnostic evidence by itself is `local partial` or `PR ready` evidence only"
        in compact_workflow_text
    )
    assert "not `reviewed`, `211 verified`, or `gate closable`" in compact_workflow_text
    assert "carries no #164 or stage/gate closure claim" in compact_workflow_text


def test_technical_acceptance_summarizes_backend_p0_productization_bundles():
    tech_text = read(TECH_ACCEPTANCE)
    compact_tech_text = " ".join(tech_text.split())

    assert "Backend P0 productization capabilities" in tech_text
    for capability in (
        "P0-1 memory/context usable",
        "P0-2 real sandbox usable",
        "P0-3 worker/model-gateway capacity",
        "P0-4 Skills management",
    ):
        assert capability in tech_text

    for evidence_level in (
        "`source_contract`",
        "`source_probe_on_target_runtime`",
        "`controlled_live_probe`",
        "`live_worker_run_payload`",
        "`live_platform_probe`",
        "`operator_reviewed_recorded_snapshot`",
    ):
        assert evidence_level in tech_text

    assert (
        "Status labels must stay separate: `local partial`, `PR ready`, `reviewed`, `merged`, "
        "`211 verified`, and `gate closable` are not interchangeable."
        in compact_tech_text
    )
    for boundary in (
        "P0 capability summaries are planning labels, not gate-closure evidence.",
        "Code absorption from backend reference projects still requires source pinning, license/provenance review, targeted tests, explicit gate wording, and runtime evidence when applicable.",
        "B3 operator-reviewed recorded snapshot source contract",
        "ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1",
        "does not raise production defaults or claim safe concurrency",
    ):
        assert boundary in compact_tech_text


def test_backend_prd_records_b3_operator_snapshot_and_reference_boundaries():
    backend_prd_text = read(BACKEND_PRD)
    compact_backend_prd_text = " ".join(backend_prd_text.split())

    for expected in (
        "Current Backend Productization State",
        "#164",
        ACTIVE_RUNTIME_SUBJECT_SHA,
        f"Reviewed runtime-relevant smoke and Foundation Runtime concurrency evidence exist for `{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}`",
        "not `gate closable`",
        "not exact current-source runtime verification",
        "Reopen when readiness reports runtime rollout, source/runtime drift, or a runtime-affecting merge.",
        "Near-term backend execution order is therefore B1/B2/B3/B4",
        "B0 freshness watch",
        "Re-run B0 only when readiness reports runtime rollout, source/runtime drift, or a runtime-affecting merge.",
        "B3 Worker And Model-Gateway Capacity",
        "10 concurrent user sessions",
        "peak 4 Claude Agent SDK subagents per session",
        "`operator_reviewed_recorded_snapshot`",
        "runtime_source_identity_and_image_labels",
        "tenant_user_skill_mix",
        "token_cost_ledger",
        "event_artifact_volume",
        "sandbox_pressure_and_cleanup",
        "latency_p50_p95_p99",
        "error_budget_and_dead_letters",
        "rollback_plan_and_stop_conditions",
        "does not raise production defaults or claim safe concurrency",
        "does not enable ordinary-user platform-level multi-run orchestration exposure",
        "does not close B3 or G9",
        "does not reopen G8",
        "Reference Code Projects",
        "Code adaptation candidate",
        "Runtime dependency proposal",
        "https://github.com/keycloak/keycloak",
        "https://github.com/goauthentik/authentik",
        "https://github.com/ory/kratos",
        "https://github.com/langchain-ai/langgraph",
        "https://github.com/mem0ai/mem0",
        "https://github.com/e2b-dev/E2B",
        "https://github.com/temporalio/sdk-python",
        "https://github.com/BerriAI/litellm",
        "https://github.com/Portkey-AI/gateway",
        "https://github.com/backstage/backstage",
        "https://github.com/open-webui/open-webui",
        "https://github.com/danny-avila/LibreChat",
        "https://github.com/openfga/openfga",
        "https://github.com/authzed/spicedb",
        "https://github.com/apache/casbin",
        "https://github.com/open-policy-agent/opa",
        "https://github.com/IBM/mcp-context-forge",
        "https://github.com/langfuse/langfuse",
        "https://github.com/open-telemetry/opentelemetry-collector",
        "https://github.com/promptfoo/promptfoo",
        "https://github.com/vibrantlabsai/ragas",
        "https://github.com/Giskard-AI/giskard-oss",
    ):
        assert expected in backend_prd_text

    for boundary in (
        "External projects are references only. ai-platform owns identity, tenancy, RBAC, audit, source authority, release evidence, and gate closure.",
        "Reading a project does not authorize copying code, adding dependencies, or changing runtime architecture.",
        "Repositories with GitHub license posture `Other`, AGPL/LGPL/copyleft terms, or unknown license posture are concept-only references by default.",
        "This is not `gate closable`, does not prove exact current-source runtime verification for later runtime-neutral docs/evidence/test commits, and does not close full G0 source authority because production auth rollout remains separate and future runtime-affecting source changes reopen B0",
        "Code adaptation still requires a focused issue with repository, commit/tag, license, tests, and runtime evidence where applicable.",
    ):
        assert boundary in compact_backend_prd_text

    assert "C:\\Users" not in backend_prd_text


def test_prd_roadmap_guardrails_share_current_gate_sequence():
    prd_text = read(PRD)
    roadmap_text = read(ROADMAP)
    guardrails_text = read(GUARDRAILS)
    gate_status_text = read(GATE_STATUS_DOC)

    current_gate_names = (
        "G0-G1 Source Authority / Security Baseline",
        "G2-G4 Control Plane MVP",
        "G5 Run Lifecycle / Worker Runtime V1",
        "G6 Tool / Skill / Memory Governance",
        "G7 Sandbox / Resource Hardening",
        "G8 Deferred Platform Multi-Run Gate",
        "G9 Observability / Quality / Ops",
        "G10 Internal Beta / Department Rollout",
    )
    for gate_name in current_gate_names:
        assert gate_name in prd_text or gate_name.replace("G0-G1 ", "G0 ") in prd_text
        assert gate_name in roadmap_text
        assert gate_name in guardrails_text
        assert gate_name in gate_status_text

    stale_gate_names = (
        "G4 Skills Governance",
        "G5 Memory/Context MVP",
        "G6 MCP/Tool Permission",
        "G9 Agent Frontend V1",
        "G10 Long Task / Multi-Agent",
        "G11 Beta",
    )
    for stale_gate_name in stale_gate_names:
        assert stale_gate_name not in prd_text

    compact_prd_text = " ".join(prd_text.split())
    assert "Reopen only for controlled SDK subagent load evidence" not in prd_text
    assert (
        "B3 SDK subagent load evidence may inform a later G8 decision, but it "
        "does not itself reopen or close G8"
    ) in compact_prd_text


def test_gate_status_snapshot_records_blockers_without_closure_claim():
    gate_status_text = read(GATE_STATUS_DOC)
    release_evidence_text = read(RELEASE_EVIDENCE_INDEX)
    compact_release_evidence_text = " ".join(release_evidence_text.split())

    assert "not automatic" in gate_status_text
    assert "gate-closure evidence" in gate_status_text
    assert "issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue" in gate_status_text
    assert "#17 frontend source migration" in gate_status_text
    assert "#21 capacity baseline" in gate_status_text
    assert "#21 is currently closed in GitHub" in gate_status_text
    assert "#21 remains open" not in gate_status_text
    assert "do_not_raise_without_recorded_load_test_evidence" in gate_status_text
    assert "packaged frontend image smoke/release acceptance" in gate_status_text
    assert "Foundation Alpha POC Smoke" in gate_status_text
    assert "latest reviewed 211 POC smoke remains useful historical" in gate_status_text
    assert "not current-main runtime verification" in gate_status_text
    assert "not production gate closure" in gate_status_text
    assert "current context public-summary" not in gate_status_text[:1000]
    assert "source_synced_runtime_pending" in gate_status_text
    assert "committed source-runtime" in gate_status_text
    assert "relation manifest" in gate_status_text
    assert "runtime_source_relation" in release_evidence_text
    assert "source-runtime-relation-manifest.json" in release_evidence_text
    assert "When several reviewed entries exist for the same gate and artifact kind" in release_evidence_text
    assert "the newest `captured_at` entry wins" in release_evidence_text
    assert "Older reviewed entries remain historical evidence" in compact_release_evidence_text
    assert "current_source_verified_by_running_runtime" in release_evidence_text
    assert "runtime_relevant_source_verified_by_running_runtime" in release_evidence_text
    assert "verified_runtime_subject" in release_evidence_text
    assert "controlled_poc_loop_verified_for_current_source" in release_evidence_text
    assert "reviewed_historical_runtime_evidence" in release_evidence_text
    assert "tools/foundation_alpha_readiness.py --format json" in gate_status_text
    assert ACTIVE_RUNTIME_SUBJECT_SHA in gate_status_text
    assert "d95107da2b5691781518bdbb8c4e5e76409869f3" in gate_status_text
    assert ACTIVE_RUNTIME_IMAGE in gate_status_text
    assert ACTIVE_RUNTIME_IMAGE_ID in gate_status_text
    assert "a63dbbd0b474cce3702b3485e6589f86155cf5aa" in gate_status_text
    assert "458f6056dd0fa533162e780a303d79ce1b3d0eec" in gate_status_text
    assert "9b02836262fb0f238a7f90b9705bf39a8b298158" in gate_status_text
    assert "cdc09ba8867d91e8db76570fbf158e6d082da7cf" in gate_status_text
    assert "8f454696be0e9c532fa86bc61ef353e4d3dec4f8" in gate_status_text
    assert "faa7ad6aa61637cbcdf3a22ce81de119762e96bf" in gate_status_text
    assert "a3f1d739e12686cba2e0b309de26a4e1127bd3a5" in gate_status_text
    assert "8c0cffca63bc747fad0a5771f209acc8a608ab9e" in gate_status_text
    assert "bf20432f9889efa8b367afdf512c641068ba30bc" in gate_status_text
    assert "3874281276c84a418bd08bda56d7ea55b52970b7" in gate_status_text
    assert "historical evidence only" in gate_status_text
    assert "stale runtime-subject label follow-up" not in gate_status_text
    assert "stale runtime/source label reconciliation" not in gate_status_text
    assert "signed package or SBOM review evidence" in gate_status_text
    assert "Keep feature flags" in gate_status_text
    assert "executor_private_payload" not in gate_status_text
    assert "raw_storage_key" not in gate_status_text
    assert "sandbox_workdir" not in gate_status_text
    assert "api_key" not in gate_status_text
    assert "C:\\Users" not in gate_status_text
    assert TARGET_211_HOME_ROOT not in gate_status_text


def test_gate_status_snapshot_records_s1_post_merge_211_verification_requirements():
    gate_status_text = read(GATE_STATUS_DOC)

    assert "S1 post-merge 211 verification requirements" in gate_status_text
    assert "after the #34-#39 stack is merged" in gate_status_text
    assert "under the recorded review exception" in gate_status_text
    assert "211 source snapshot" in gate_status_text
    assert "not a Git worktree" in gate_status_text
    assert ".ai-platform-source-revision" in gate_status_text
    assert ".ai-platform-source-snapshot.json" in gate_status_text
    assert "repo-local deploy composition" in gate_status_text
    assert "container image labels" in gate_status_text
    assert "runtime subject" in gate_status_text
    assert "source tree commit" in gate_status_text
    assert "release-evidence" in gate_status_text
    assert "runtime subject" in gate_status_text
    assert "governed_skill_runs" in gate_status_text
    assert "mcp_tool_permission_runtime_controls" in gate_status_text
    assert "memory_context_controls" in gate_status_text
    assert "reviewDecision" in gate_status_text
    assert "explicitly recorded project exception" in gate_status_text
    assert "ordinary_user_multi_agent_allowed=false" in gate_status_text
    assert "production_claim_allowed=false" in gate_status_text
    assert "docker_sandbox_hardened_claim_allowed=false" in gate_status_text
    assert "capacity_default_increase_allowed=false" in gate_status_text


def test_s2_sandbox_runtime_smoke_contract_records_pr44_evidence_without_closing_g6_g9():
    gate_status_text = read(GATE_STATUS_DOC)
    governance_text = read(GOVERNANCE_READINESS_DOC)
    roadmap_text = read(ROADMAP)
    release_evidence_text = read(RELEASE_EVIDENCE_INDEX)

    for text in (gate_status_text, governance_text, roadmap_text):
        assert "sandbox_runtime_smoke_contract" in text
        assert "211_sandbox_latency_split_smoke" in text
        assert "scripts/generate_sandbox_runtime_evidence_211.py" in text
        assert "scripts/verify_sandbox_runtime_211.py" in text
        assert "sudo -n docker" in text
        assert "ai-platform:local" in text
        assert "non_expansion_invariants" in text
        assert "ordinary_user_high_risk_sandbox_allowed=false" in text
        assert "ordinary_user_multi_agent_allowed=false" in text
        assert "sandbox_cold_start_latency_split_211_acceptance" in text
        assert "executor_context_pack_211_acceptance" in text
        assert "executor_context_pack_runtime_acceptance_contract" in text
        assert "ai-platform.executor-context-pack-runtime-acceptance.v1" in text
        assert "scripts/generate_executor_context_pack_evidence_211.py" in text
        assert "scripts/verify_executor_context_pack_211.py" in text
        assert "app.repositories.get_context_snapshot_for_worker" in text
        assert "app.context_builder.executor_context_pack_from_snapshot" in text
        assert "prompt_includes_bounded_summary" in text
        assert "fresh" in text
        assert "source_functions" in text
        assert "long_term_cross_session_memory_enabled=false" in text
        assert "PR #44" in text
        assert "does not close G6" in text or "does not close G6/G9" in text

    assert "Docker sandbox production hardening is closed" not in gate_status_text
    assert "Docker sandbox production hardening is closed" not in governance_text
    assert "Docker sandbox production hardening is closed" not in roadmap_text
    assert "2026-06-16-211-office-context-pr44-executor-context-pack-runtime-acceptance.json" in release_evidence_text
    assert "2026-06-17-211-office-context-8e0389e-executor-context-pack-runtime-acceptance.json" in release_evidence_text
    assert "2026-06-16-211-office-context-pr44-sandbox-latency-split-runtime-acceptance.json" in release_evidence_text
    assert "executor_context_pack_211_acceptance" in release_evidence_text
    assert "sandbox_cold_start_latency_split_211_acceptance" in release_evidence_text
    assert "ordinary_user_high_risk_sandbox_allowed=false" in release_evidence_text
    assert "ordinary_user_multi_agent_allowed=false" in release_evidence_text
    assert "production_concurrency_defaults_raised=false" in release_evidence_text
    assert "does not claim production Docker sandbox hardening" in release_evidence_text
    assert "Superseded insufficient PR #44 executor context-pack evidence" in release_evidence_text
    assert "does not close `executor_context_pack_211_acceptance`" in release_evidence_text
    assert "closes only the #22 `executor_context_pack_211_acceptance` runtime gap" in release_evidence_text
    assert "G6/G9 closure" in release_evidence_text
    assert "2026-07-01-211-b1-memory-context-workflow-smoke-96f27bb.json" in release_evidence_text
    assert "Reviewed 211 B1 memory/context smoke passed against `ai-platform:96f27bb-b0-current-source-runtime-only-v2`" in release_evidence_text
    assert "Since `96f27bb` has no runtime-affecting delta to current local source `830d352`" in release_evidence_text
    assert "this closes only the B1 `b1_runtime_evidence_review_against_merged_source` boundary" in release_evidence_text
    assert "This evidence does not close B1 as a product gate" in release_evidence_text
    assert "2026-07-01-211-b1-memory-context-workflow-smoke-427c8d1.json" in release_evidence_text
    assert "retained as superseded reviewed B1 history after the `96f27bb` refresh" in release_evidence_text
    assert "2026-06-19-211-b1-memory-context-workflow-smoke-87528bf.json" in release_evidence_text
    assert "Reviewed #128 B1 memory/context 211 smoke passed" in release_evidence_text
    assert "The 211 service checkout and legacy compose env-label caveats remain" in release_evidence_text
    assert "2026-06-19-211-b1-memory-context-workflow-smoke-75ab69b.json" in release_evidence_text
    assert "retained as superseded reviewed B1 history after the #128 `87528bf` refresh" in release_evidence_text
    assert "2026-06-19-211-b1-memory-context-workflow-smoke-52ac62c.json" in release_evidence_text
    assert "after the 2026-06-19 `52ac62c` refresh it is retained as historical evidence" in release_evidence_text
    assert "rollback assumptions source/operator contract is recorded by #116" in release_evidence_text
    assert "remaining B2/G7 hardening gaps are resource-limit policy, egress policy, and security-option policy evidence" in release_evidence_text
    assert "Source policy contracts for resource limits, egress policy, and security options are recorded by #120" in release_evidence_text
    assert "remaining runtime hardening evidence gaps are resource_limits_runtime_hardening_evidence, egress_runtime_hardening_evidence, and security_options_runtime_hardening_evidence" in release_evidence_text
    compact_governance_text = " ".join(governance_text.split())
    assert "Resource-limit policy evidence, egress-policy evidence, and security-option evidence remain PRD B2/G7 requirements" in compact_governance_text
    assert "The #120 source policy contract names the required controls and runtime evidence for resource limits, egress policy, and security options" in compact_governance_text
    assert "resource_limits_runtime_hardening_evidence, egress_runtime_hardening_evidence, and security_options_runtime_hardening_evidence" in compact_governance_text
    assert "rollback-assumption evidence is now a source/operator contract" in compact_governance_text
    assert "recorded_source_operator_contract" in governance_text
    assert "rollback_assumptions_evidence" in governance_text
    assert "Rollback assumptions are not Docker sandbox production hardening" in compact_governance_text


def test_foundation_alpha_closure_records_stage_complete_baseline_and_boundaries():
    closure_text = read(FOUNDATION_ALPHA_CLOSURE_DOC)
    prd_text = read(PRD)
    tech_text = read(TECH_ACCEPTANCE)
    roadmap_text = read(ROADMAP)
    gate_status_text = read(GATE_STATUS_DOC)

    assert "Status: Foundation Alpha historical baseline accepted" in closure_text
    assert ACTIVE_CLOSURE_SOURCE_TREE_SHA in closure_text
    assert FOUNDATION_ALPHA_BASELINE_RUNTIME_SUBJECT_SHA in closure_text
    assert FOUNDATION_ALPHA_BASELINE_RUNTIME_IMAGE in closure_text
    assert FOUNDATION_ALPHA_BASELINE_RUNTIME_IMAGE_ID in closure_text
    assert "runtime_current_for_runtime_relevant_source" in closure_text
    assert "This note is not a claim that every later source revision is already verified" in closure_text
    assert "foundation_alpha_stage_status=runtime_rollout_required" in closure_text
    assert "foundation_runtime_concurrency_evidence" in closure_text
    assert "S2-0 runtime/concurrency/readiness refresh" in closure_text
    assert "foundation_alpha_stage_complete=true" in closure_text
    assert "stage_acceptance_blockers=[]" in closure_text
    assert "runtime_relevant_source_verified_by_running_runtime=true" in closure_text
    assert "current_source_verified_by_running_runtime=false" in closure_text
    assert "controlled_poc_loop_verified_for_current_source=false" in closure_text
    assert "python tools\\foundation_alpha_readiness.py --format json" in closure_text
    assert "verified_foundation_runtime_concurrency" in closure_text
    assert "48 denied negative tool-permission reuse probes" in closure_text
    for issue_number in ("#15", "#16", "#17", "#21", "#22", "#23", "#33"):
        assert issue_number in closure_text
    assert "draft PR #44" in closure_text
    assert "is not an S1 blocker" in closure_text

    for boundary in (
        "raise production concurrency defaults",
        "broaden ordinary-user platform-level multi-run orchestration exposure",
        "claim Docker sandbox hardening",
        "permit department rollout",
        "enable long-term cross-session memory by default",
        "close packaged frontend image release acceptance",
        "close signed Skill package, SBOM, license, or vulnerability evidence",
    ):
        assert boundary in closure_text

    for authority_text in (prd_text, tech_text, roadmap_text, gate_status_text):
        assert "Foundation Alpha" in authority_text
        assert "platform-level multi-run orchestration" in authority_text
        assert "Docker sandbox" in authority_text

    assert "production readiness" in closure_text
    assert "not production readiness" in closure_text
    assert "C:\\Users" not in closure_text


def test_prd_records_s1_historical_baseline_and_latest_main_refresh_gate():
    prd_text = read(PRD)
    tech_text = read(TECH_ACCEPTANCE)
    roadmap_text = read(ROADMAP)
    closure_text = read(FOUNDATION_ALPHA_CLOSURE_DOC)
    compact_roadmap_text = " ".join(roadmap_text.split())

    for text in (prd_text, tech_text, roadmap_text, closure_text):
        assert "380de6b" in text
        assert "foundation_runtime_concurrency_evidence" in text
        assert "runtime_rollout_required" in text
        assert "S2-0" in text
        assert "C:\\Users" not in text

    assert "current-source S1 complete" in prd_text
    assert "latest current-source claims require a fresh readiness result" in prd_text
    assert "S2-0 latest-main evidence refresh" in prd_text
    assert "capacity-upgrade evidence gate" in prd_text
    assert "#21 has a recorded evidence plan or harness" not in prd_text
    assert "#21 recorded load evidence missing" not in prd_text
    assert "Blocks first-stage closure evidence" not in prd_text
    assert "fresh 211 auth/session/RBAC/tenant smoke is still required" not in tech_text
    assert "#21 remains blocked until recorded evidence exists" not in tech_text
    assert "当前 main/source 是否仍可宣称 current-source S1 complete" in compact_roadmap_text


def test_committed_source_runtime_relation_manifest_keeps_clean_checkout_readiness_truthful():
    import json

    payload = json.loads(read(SOURCE_RUNTIME_RELATION_MANIFEST))

    assert payload["schema_version"] == "ai-platform.source-runtime-relation-manifest.v1"
    assert payload["source_tree_commit_sha"] == CURRENT_SOURCE_RUNTIME_RELATION_SHA
    assert payload["runtime_subject_commit_sha"] == CURRENT_SOURCE_RUNTIME_RELATION_SHA
    assert payload["runtime_affecting_changes_since_runtime_subject"] == []
    assert payload["runtime_affecting_dirty_paths"] == []
    assert "C:\\Users" not in json.dumps(payload)
    assert TARGET_211_HOME_ROOT not in json.dumps(payload)


def test_latest_verified_foundation_runtime_concurrency_evidence_bundle_is_current_subject_and_bounded():
    import json

    release_evidence_index = read(RELEASE_EVIDENCE_INDEX)
    assert f"{LATEST_VERIFIED_FRC_RUNTIME_SUBJECT_SHA}-frc-b0-20260630" in release_evidence_index
    for path in (CURRENT_SOURCE_FRC_EVIDENCE, CURRENT_SOURCE_FRC_READINESS, CURRENT_SOURCE_FRC_SUMMARY):
        relative_path = path.relative_to(RELEASE_EVIDENCE_INDEX.parent).as_posix()
        assert path.exists()
        assert relative_path in release_evidence_index

    evidence = json.loads(read(CURRENT_SOURCE_FRC_EVIDENCE))
    readiness = json.loads(read(CURRENT_SOURCE_FRC_READINESS))
    summary_text = read(CURRENT_SOURCE_FRC_SUMMARY)

    assert evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert evidence["source_tree_commit_sha"] == LATEST_VERIFIED_FRC_RUNTIME_SUBJECT_SHA
    assert evidence["runtime_subject_commit_sha"] == LATEST_VERIFIED_FRC_RUNTIME_SUBJECT_SHA
    assert evidence["commit_sha"] == LATEST_VERIFIED_FRC_RUNTIME_SUBJECT_SHA
    assert evidence["runtime_subject_commit_sha"] == CURRENT_SOURCE_RUNTIME_RELATION_SHA
    assert evidence["artifact_kind"] == "foundation_runtime_concurrency"
    assert evidence["summary"]["concurrency_probe_source"] == "client_case_timestamps"
    assert evidence["summary"]["concurrency_window_sample_count"] == 12
    assert evidence["summary"]["concurrent_request_count"] == 12
    assert evidence["summary"]["tenant_count"] == 2
    assert evidence["summary"]["user_count"] == 4
    assert evidence["non_expansion_invariants"] == {
        "department_rollout_allowed": False,
        "docker_sandbox_hardened_claim_allowed": False,
        "long_term_cross_session_memory_enabled": False,
        "ordinary_user_multi_agent_allowed": False,
        "production_concurrency_increase_allowed": False,
    }

    assert readiness["status"] == "verified_foundation_runtime_concurrency"
    assert readiness["verified"] is True
    assert readiness["summary"] == evidence["summary"]
    assert readiness["non_expansion_invariants"] == evidence["non_expansion_invariants"]

    assert "Status: `verified_foundation_runtime_concurrency`" in summary_text
    assert "Concurrent requests: `12`" in summary_text
    assert "`ordinary_user_multi_agent_allowed`: `False`" in summary_text
    assert "`production_concurrency_increase_allowed`: `False`" in summary_text

    serialized = json.dumps([evidence, readiness], sort_keys=True) + summary_text
    assert "C:\\Users" not in serialized
    assert TARGET_211_HOME_ROOT not in serialized
    for forbidden in ("client_secret", "api_key", "AI_PLATFORM_LOGIN_PASSWORD", "BEGIN PRIVATE"):
        assert forbidden.lower() not in serialized.lower()


def test_foundation_alpha_poc_release_evidence_is_reviewed_redacted_and_bounded():
    import json

    assert FOUNDATION_ALPHA_POC_EVIDENCE.exists()
    assert FOUNDATION_ALPHA_POC_ACTIVE_SMOKE_EVIDENCE.exists()
    assert FOUNDATION_ALPHA_POC_ACTIVE_AUTH_RBAC_EVIDENCE.exists()
    assert FOUNDATION_ALPHA_POC_ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE.exists()
    assert FOUNDATION_ALPHA_POC_ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE.exists()
    assert FOUNDATION_ALPHA_POC_CURRENT_MAIN_SMOKE_EVIDENCE.exists()
    assert FOUNDATION_ALPHA_POC_CURRENT_MAIN_AUTH_RBAC_EVIDENCE.exists()
    assert FOUNDATION_ALPHA_POC_AUTH_RBAC_EVIDENCE.exists()
    evidence_text = read(FOUNDATION_ALPHA_POC_ACTIVE_SMOKE_EVIDENCE)
    payload = json.loads(evidence_text)

    assert payload["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert payload["evidence_id"] == ACTIVE_POC_SMOKE_EVIDENCE_ID
    assert payload["commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert "record_commit_sha" not in payload
    assert payload["gate"] == "Foundation Alpha POC"
    assert payload["artifact_kind"] == "211_runtime_smoke"
    assert payload["redaction_scan_status"] == "passed"
    assert payload["review_status"] == "reviewed"
    assert payload["source_ref"]["runtime_source_marker"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["source_ref"]["image"] == ACTIVE_RUNTIME_IMAGE
    assert payload["source_ref"]["image_id"] == ACTIVE_RUNTIME_IMAGE_ID
    assert payload["source_ref"]["image_labels"]["ai-platform.source-revision"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["source_ref"]["image_labels"]["org.opencontainers.image.revision"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["source_ref"]["repo_local_env_present"] is False
    assert payload["evidence_ref"]["result"] == "ok:true"
    assert payload["evidence_ref"]["runtime_checks"]["lambchat_frontend"]["status"] == 200
    assert payload["evidence_ref"]["runtime_checks"]["lambchat_frontend_origin_api"]["payload"]["status"] == "ok"
    assert payload["evidence_ref"]["runtime_checks"]["lambchat_frontend_origin_api"]["status"] == 200
    assert set(payload["evidence_ref"]["runtime_checks"]["lambchat_api_compat"]["statuses"].values()) == {200}
    assert payload["evidence_ref"]["runtime_checks"]["lambchat_api_compat"]["missing_permissions"] == []
    assert payload["evidence_ref"]["runtime_checks"]["context_snapshot_public_projection"]["summary_source"] == "chat_stream"
    assert payload["evidence_ref"]["runtime_checks"]["context_snapshot_public_projection"]["input_keys"] == [
        "attachments",
        "message",
    ]
    word_review = payload["evidence_ref"]["runtime_checks"]["word_review_attachment_chat"]
    assert word_review["run"]["status"] == "succeeded"
    assert word_review["playback"]["private_payload_leaked"] is False
    assert payload["evidence_ref"]["runtime_checks"]["artifact_download_isolation"]["checked_artifacts"] == 2
    download_results = payload["evidence_ref"]["runtime_checks"]["artifact_download_isolation"]["results"]
    assert [item["owner_status"] for item in download_results] == [200, 200]
    assert [item["cross_user_status"] for item in download_results] == [
        404,
        404,
    ]
    assert [item["cross_tenant_status"] for item in download_results] == [
        404,
        404,
    ]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["checked_artifacts"] == 2
    preview_results = payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["results"]
    assert [item["owner_status"] for item in preview_results] == [200, 200]
    assert [item["cross_user_status"] for item in preview_results] == [404, 404]
    assert [item["cross_tenant_status"] for item in preview_results] == [404, 404]
    assert sorted({item["owner_content_type"] for item in preview_results}) == [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    smoke_followups = "\n".join(payload["open_followups"])
    assert "alert_delivery_and_trace_export_211_acceptance" not in smoke_followups
    assert "Foundation Runtime concurrency evidence is blocked" not in smoke_followups
    assert "g7_docker_sandbox_hardening" not in smoke_followups
    assert "g8_ordinary_user_multi_agent_exposure" not in smoke_followups
    assert "production_concurrency_increase_allowed" not in evidence_text

    release_evidence_index = read(RELEASE_EVIDENCE_INDEX)
    gate_status_text = read(GATE_STATUS_DOC)
    compact_gate_status_text = " ".join(gate_status_text.split())
    compact_release_evidence_index = " ".join(release_evidence_index.split())
    assert f"{ACTIVE_AUTH_RBAC_EVIDENCE_FILE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_POC_SMOKE_EVIDENCE_FILE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_FILE_ID}.json" in release_evidence_index
    assert (
        f"Reviewed 211 smoke refresh passed for the `{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}` runtime subject"
        in compact_release_evidence_index
    )
    assert "The wrapped evidence entries have empty `open_followups`" in compact_release_evidence_index
    assert (
        "Foundation Runtime concurrency evidence passed with verifier status "
        f"`verified_foundation_runtime_concurrency` against the `{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}` runtime subject"
        in compact_release_evidence_index
    )
    assert (
        "This removes the current-subject `foundation_runtime_concurrency_evidence` readiness blocker for "
        f"`{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}`"
        in compact_release_evidence_index
    )
    assert "clean current source can still be ahead of the running image by runtime-neutral docs/evidence/tests commits" in compact_gate_status_text
    assert "while the local worktree is dirty with documentation/evidence updates" not in compact_gate_status_text
    assert "does not constitute current G7/B3 closure evidence for any current #164/G7/B3 closure claim" in compact_release_evidence_index
    assert "claim production readiness" in compact_release_evidence_index
    assert "external env-file label caveat" in compact_gate_status_text
    assert f"{ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_FILE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_FILE_ID}.json" in release_evidence_index
    assert "clears only the Foundation Runtime evidence blocker for the named `2bc3a35` runtime subject" in release_evidence_index
    assert "does not clear current-source/latest-main readiness after later runtime-affecting changes such as `f11309e`" in release_evidence_index
    assert "readiness must remain `runtime_rollout_required` until fresh rollout evidence exists" in release_evidence_index
    assert "2026-06-21-211-foundation-alpha-poc-e8e8a0a-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-21-211-foundation-alpha-poc-e8e8a0a-governance-runtime-smoke.json" in release_evidence_index
    assert "2026-06-21-211-foundation-alpha-poc-e8e8a0a-release-evidence-runtime-acceptance.json" in release_evidence_index
    assert "2026-06-21-211-foundation-alpha-poc-e8e8a0a-alert-trace-export-runtime-acceptance.json" in release_evidence_index
    assert "Foundation Alpha POC partial B0 evidence" in release_evidence_index
    assert "still lacks a passing `verify_poc_gate.py` runtime POC smoke entry" in release_evidence_index
    assert "HTTP 402 `Insufficient Balance`" in release_evidence_index
    assert "2026-06-13-211-foundation-alpha-poc-cbbfaff-governance-runtime-smoke.json" in release_evidence_index
    assert "2026-06-13-211-foundation-alpha-poc-cbbfaff-frontend-packaged-runtime-smoke-blocked.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-d95107d-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-d95107d-context-projection-smoke.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-a63dbbd-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-a63dbbd-smoke.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-458f605-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-12-211-foundation-alpha-poc-458f605-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-9b02836-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-9b02836-context-output-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-8f45469-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-8f45469-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-faa7ad6-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-faa7ad6-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-a3f1d73-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-a3f1d73-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-current-main-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-auth-rbac-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-merged-smoke.json" in release_evidence_index
    assert "2026-06-11-211-foundation-alpha-poc-smoke.json" in release_evidence_index

    forbidden_markers = (
        "executor_private_payload",
        "executor private payload",
        "raw_storage_key",
        "raw storage key",
        "sandbox_workdir",
        "sandbox workdir",
        "api_key",
        "bearer ",
        "database_url",
        "database url",
        "redis_url",
        "redis url",
        "sk-",
        "C:\\Users",
        TARGET_211_HOME_ROOT,
        "artifact_storage_key",
        "tenants/default/workspaces",
        "tenants/default",
    )
    lowered = evidence_text.lower()
    for marker in forbidden_markers:
        assert marker.lower() not in lowered

    changed_evidence_paths = [
        ROOT
        / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_POC_SMOKE_EVIDENCE_FILE_ID}.json",
        ROOT
        / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_AUTH_RBAC_EVIDENCE_FILE_ID}.json",
        ROOT
        / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_FILE_ID}.json",
        ROOT
        / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_FILE_ID}.json",
        ROOT
        / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_FILE_ID}.json",
        ROOT
        / (
            "docs/release-evidence/foundation-runtime-concurrency/"
            f"{ACTIVE_RUNTIME_SUBJECT_SHA}-frc-b0-20260630/"
            f"2026-06-30-211-foundation-alpha-poc-{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}-foundation-runtime-concurrency.json"
        ),
    ]
    for path in changed_evidence_paths[1:5]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "#286" in payload["pr_refs"]
    for path in (changed_evidence_paths[0], changed_evidence_paths[5]):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "#164" in payload["issue_refs"]

    auth_rbac_text = read(FOUNDATION_ALPHA_POC_ACTIVE_AUTH_RBAC_EVIDENCE)
    auth_rbac_payload = json.loads(auth_rbac_text)
    assert auth_rbac_payload["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert auth_rbac_payload["evidence_id"] == ACTIVE_AUTH_RBAC_EVIDENCE_ID
    assert auth_rbac_payload["commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert auth_rbac_payload["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert "record_commit_sha" not in auth_rbac_payload
    assert (auth_rbac_payload["source_ref"].get("runtime_image") or auth_rbac_payload["source_ref"].get("image")) == ACTIVE_RUNTIME_IMAGE
    assert auth_rbac_payload["source_ref"]["image_id"] == ACTIVE_RUNTIME_IMAGE_ID
    assert auth_rbac_payload["source_ref"]["image_labels"]["ai-platform.source-revision"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert auth_rbac_payload["evidence_ref"]["result"] == "ok:true"
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["unauthenticated_auth_me"]["status"] == 401
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["authenticated_auth_me"]["route"] == "/api/ai/auth/me"
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["authenticated_auth_me"]["status"] == 200
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["authenticated_auth_me"]["tenant_matches_requested"] is True
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["authenticated_auth_me"]["user_matches_requested"] is True
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["invalid_gateway_secret_auth_me"]["status"] == 403
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["ordinary_admin_runtime"]["status"] == 403
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["admin_runtime"]["status"] == 200
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["admin_runtime"]["tenant_matches_requested"] is True
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["admin_runtime"]["required_sections_present"] is True
    assert auth_rbac_payload["evidence_ref"]["runtime_checks"]["admin_runtime"]["forbidden_projection_terms_present"] is False
    assert auth_rbac_payload["redaction_scan_status"] == "passed"
    assert auth_rbac_payload["review_status"] == "reviewed"

    lowered_auth_rbac = auth_rbac_text.lower()
    for marker in forbidden_markers:
        assert marker.lower() not in lowered_auth_rbac

    runtime_acceptance_text = read(FOUNDATION_ALPHA_POC_ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE)
    runtime_acceptance_payload = json.loads(runtime_acceptance_text)
    acceptance = runtime_acceptance_payload["evidence_ref"]["runtime_checks"][
        "release_evidence_runtime_acceptance"
    ]
    runtime_export = acceptance["checks"]["runtime_export_acceptance"]
    retention = acceptance["checks"]["retention_runtime_acceptance"]
    assert runtime_acceptance_payload["evidence_id"] == ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_ID
    assert runtime_acceptance_payload["commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert runtime_acceptance_payload["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert runtime_acceptance_payload["source_ref"]["image"] == ACTIVE_RUNTIME_IMAGE
    assert runtime_acceptance_payload["source_ref"]["image_id"] == ACTIVE_RUNTIME_IMAGE_ID
    assert runtime_acceptance_payload["evidence_ref"]["result"] == "ok:true"
    assert acceptance["schema_version"] == "ai-platform.release-evidence-runtime-acceptance.v1"
    assert acceptance["ok"] is True
    assert acceptance["status"] == "accepted_for_operator_review"
    assert acceptance["open_gaps"] == []
    assert acceptance["does_not_close_g9"] is True
    assert runtime_export["status"] == "ready_for_operator_review"
    assert runtime_export["blocked_entry_count"] == 0
    assert runtime_export["safe_entry_fields_only"] is True
    assert retention["status"] == "accepted_review_first_policy"
    assert retention["schema_version"] == "ai-platform.release-evidence-retention-policy.v1"
    lowered_runtime_acceptance = runtime_acceptance_text.lower()
    for marker in forbidden_markers:
        assert marker.lower() not in lowered_runtime_acceptance

    alert_trace_text = read(FOUNDATION_ALPHA_POC_ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE)
    alert_trace_payload = json.loads(alert_trace_text)
    alert_trace_acceptance = alert_trace_payload["evidence_ref"]["runtime_checks"][
        "alert_trace_export_runtime_acceptance"
    ]
    alert_checks = alert_trace_acceptance["checks"]
    assert alert_trace_payload["evidence_id"] == ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_ID
    assert alert_trace_payload["commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert alert_trace_payload["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert alert_trace_payload["source_ref"]["image"] == ACTIVE_RUNTIME_IMAGE
    assert alert_trace_payload["source_ref"]["image_id"] == ACTIVE_RUNTIME_IMAGE_ID
    assert alert_trace_payload["evidence_ref"]["result"] == "ok:true"
    assert alert_trace_acceptance["schema_version"] == "ai-platform.alert-trace-export-runtime-acceptance.v1"
    assert alert_trace_acceptance["ok"] is True
    assert alert_trace_acceptance["status"] == "accepted_for_operator_review"
    assert alert_trace_acceptance["does_not_enable_alert_delivery"] is True
    assert alert_trace_acceptance["does_not_export_raw_runtime_payloads"] is True
    assert alert_trace_acceptance["does_not_close_g9"] is True
    assert alert_checks["ordinary_admin_runtime"]["status"] == 403
    assert alert_checks["admin_runtime_alerts_and_exports"]["status"] == 200
    assert alert_checks["admin_runtime_alerts_and_exports"]["alert_delivery_not_enabled"] is True
    assert alert_checks["admin_runtime_alerts_and_exports"]["trace_export_sources_public_only"] is True
    assert alert_checks["admin_runtime_alerts_and_exports"]["forbidden_projection_terms_present"] is False
    lowered_alert_trace = alert_trace_text.lower()
    for marker in forbidden_markers:
        assert marker.lower() not in lowered_alert_trace

    frontend_blocked_text = read(FOUNDATION_ALPHA_POC_CBBFAFF_FRONTEND_PACKAGED_RUNTIME_BLOCKED_EVIDENCE)
    frontend_blocked_payload = json.loads(frontend_blocked_text)
    frontend_blocked_smoke = frontend_blocked_payload["evidence_ref"]["runtime_checks"][
        "frontend_packaged_runtime_smoke"
    ]
    assert frontend_blocked_payload["evidence_id"] == CBBFAFF_FRONTEND_PACKAGED_RUNTIME_BLOCKED_EVIDENCE_ID
    assert frontend_blocked_payload["artifact_kind"] == "frontend_packaged_runtime_smoke"
    assert frontend_blocked_payload["commit_sha"] == CBBFAFF_RUNTIME_SUBJECT_SHA
    assert frontend_blocked_payload["runtime_subject_commit_sha"] == CBBFAFF_RUNTIME_SUBJECT_SHA
    assert frontend_blocked_payload["source_ref"]["runtime_commit"] == CBBFAFF_RUNTIME_SUBJECT_SHA
    assert frontend_blocked_payload["source_ref"]["runtime_source_marker"] == CBBFAFF_RUNTIME_SUBJECT_SHA
    assert frontend_blocked_payload["source_ref"]["image_labels"]["ai-platform.source-revision"] == (
        CBBFAFF_RUNTIME_SUBJECT_SHA
    )
    assert frontend_blocked_payload["source_ref"]["image_labels"]["org.opencontainers.image.revision"] == (
        CBBFAFF_RUNTIME_SUBJECT_SHA
    )
    assert frontend_blocked_payload["evidence_ref"]["result"] == "ok:true"
    assert frontend_blocked_payload["evidence_ref"]["schema_version"] == (
        "ai-platform.frontend-packaged-runtime-smoke.v1"
    )
    assert frontend_blocked_smoke["commit_sha"] == CBBFAFF_RUNTIME_SUBJECT_SHA
    assert frontend_blocked_smoke["runtime_host"] == "211"
    assert frontend_blocked_smoke["image_tag"] == "ai-platform-frontend:cbbfaff-smoke"
    assert frontend_blocked_smoke["docker_build"]["exit_code"] == 1
    assert "proxyconnect" in frontend_blocked_smoke["docker_build"]["log_tail"]
    assert "resolve source metadata" in frontend_blocked_smoke["docker_build"]["log_tail"]
    assert frontend_blocked_smoke["image_inspect"]["status"] == "not_built"
    assert frontend_blocked_smoke["build_provenance"]["status"] == "not_available"
    assert frontend_blocked_smoke["runtime_smoke"]["status"] == "not_run"
    assert frontend_blocked_smoke["leak_scan"]["status"] == "not_run"
    assert frontend_blocked_smoke["cleanup"]["container_removed"] is True
    assert "docker_registry_proxy_unreachable" in frontend_blocked_payload["notes"][0]
    assert "base_image_pull_failed" in frontend_blocked_payload["notes"][0]
    assert "node:22-alpine" in frontend_blocked_payload["notes"][1]
    assert "nginx:1.27-alpine" in frontend_blocked_payload["notes"][1]
    assert "not release acceptance" in frontend_blocked_payload["notes"][1]
    assert frontend_blocked_payload["redaction_scan_status"] == "passed"
    assert frontend_blocked_payload["review_status"] == "reviewed"
    lowered_frontend_blocked = frontend_blocked_text.lower()
    for marker in forbidden_markers:
        assert marker.lower() not in lowered_frontend_blocked

    gate_status_text = read(GATE_STATUS_DOC)
    assert "`/api/ai/auth/me`" in gate_status_text
    assert "tenant match" in gate_status_text
    assert "invalid gateway secret" in gate_status_text


def test_gate_status_snapshot_records_company_login_audit_readiness_fields():
    gate_status_text = read(GATE_STATUS_DOC)

    assert "company_login_audit_verified=true" in gate_status_text
    assert "ordinary_company_login_audit_count=12" in gate_status_text
    assert "admin_company_login_audit_count=36" in gate_status_text
    assert "broader auth/session/RBAC/tenant/redaction regression" in gate_status_text
    assert "not production gate closure" in gate_status_text


def test_foundation_alpha_runtime_evidence_subject_commit_parity_without_self_referential_record_commit():
    import json

    expected_artifact_kinds = {
        FOUNDATION_ALPHA_POC_ACTIVE_SMOKE_EVIDENCE: "211_runtime_smoke",
        FOUNDATION_ALPHA_POC_ACTIVE_AUTH_RBAC_EVIDENCE: "auth_rbac_smoke",
    }
    for path, expected_artifact_kind in expected_artifact_kinds.items():
        payload = json.loads(read(path))
        source_ref = payload["source_ref"]
        labels = source_ref["image_labels"]

        assert payload["artifact_kind"] == expected_artifact_kind
        assert "record_commit_sha" not in payload
        assert payload["commit_sha"] == payload["runtime_subject_commit_sha"]
        assert source_ref["runtime_source_marker"] == payload["runtime_subject_commit_sha"]
        assert labels["ai-platform.source-revision"] == payload["runtime_subject_commit_sha"]
        assert labels["org.opencontainers.image.revision"] == payload["runtime_subject_commit_sha"]
        assert source_ref["runtime_subject_label_status"] == "runtime_subject_label_current"


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
    assert compose_text.count("context: ../..") == 3
    assert "container_name: ai-platform-frontend" in compose_text
    assert "dockerfile: frontend/web/Dockerfile" in compose_text
    assert "${AI_PLATFORM_FRONTEND_PORT:-18001}:8080" in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text


def test_backend_dockerfile_defines_source_authority_label_contract():
    dockerfile = read(BACKEND_DOCKERFILE)
    compose_text = read(COMPOSE)
    compose = yaml.safe_load(compose_text)
    env_text = read(ENV_EXAMPLE)

    assert "ARG AI_PLATFORM_BUILD_COMMIT=unknown" in dockerfile
    assert "ARG AI_PLATFORM_BUILD_DIRTY=unknown" in dockerfile
    for label in (
        "org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.runtime-subject=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.source_revision=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.runtime_subject=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.source_tree_commit=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.source_commit=$AI_PLATFORM_BUILD_COMMIT",
        'ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"',
    ):
        assert label in dockerfile
    expected_backend_args = {
        "AI_PLATFORM_BUILD_COMMIT": "${AI_PLATFORM_BUILD_COMMIT:-unknown}",
        "AI_PLATFORM_BUILD_DIRTY": "${AI_PLATFORM_BUILD_DIRTY:-unknown}",
    }
    assert compose["services"]["api"]["build"]["args"] == expected_backend_args
    assert compose["services"]["worker"]["build"]["args"] == expected_backend_args
    assert "AI_PLATFORM_BUILD_COMMIT=unknown" in env_text
    assert "AI_PLATFORM_BUILD_DIRTY=unknown" in env_text


def test_env_template_satisfies_required_runtime_defaults_without_real_secrets():
    env_text = read(ENV_EXAMPLE)
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" in env_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.25:7263" in env_text
    assert "EXISTING_USER_INFO_BASE_URL=http://10.56.0.25:5166" in env_text
    assert "PUBLIC_SKILL_FILE_OVERLAY_MAX_BYTES=262144" in env_text
    assert "AI_PLATFORM_FRONTEND_PORT=18001" in env_text
    assert "AI_PLATFORM_FRONTEND_IMAGE=ai-platform-frontend:local" in env_text
    assert "AI_PLATFORM_API_UPSTREAM=http://api:8020" in env_text
    assert "CLAUDE_AGENT_SDK_MAX_TURNS=128" in env_text
    assert "CLAUDE_AGENT_SDK_EFFORT=xhigh" in env_text
    assert "CLAUDE_AGENT_SDK_MAX_THINKING_TOKENS=16384" in env_text
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


def test_compose_forwards_claude_agent_sdk_max_turns_to_api_and_worker():
    compose_text = read(COMPOSE)

    assert compose_text.count("CLAUDE_AGENT_SDK_MAX_TURNS: ${CLAUDE_AGENT_SDK_MAX_TURNS:-128}") == 2
    assert compose_text.count("CLAUDE_AGENT_SDK_EFFORT: ${CLAUDE_AGENT_SDK_EFFORT:-xhigh}") == 2
    assert (
        compose_text.count(
            "CLAUDE_AGENT_SDK_MAX_THINKING_TOKENS: ${CLAUDE_AGENT_SDK_MAX_THINKING_TOKENS:-16384}"
        )
        == 2
    )


def test_compose_forwards_public_skill_file_overlay_limit_to_api_and_worker():
    compose_text = read(COMPOSE)

    assert (
        compose_text.count(
            "PUBLIC_SKILL_FILE_OVERLAY_MAX_BYTES: ${PUBLIC_SKILL_FILE_OVERLAY_MAX_BYTES:-262144}"
        )
        == 2
    )


def test_agents_lock_211_runtime_verification_and_rebase_deploy_rules():
    agents_text = read(AGENTS)
    generator_text = read(ROOT / "scripts/generate_sandbox_runtime_evidence_211.py")

    assert "python3" in agents_text
    assert '--docker-cmd "sudo -n docker"' in agents_text
    assert "--cancel-image ai-platform:local" in agents_text
    assert "rebasing from the current/backup image" in agents_text
    assert "compose with `--no-build`" in agents_text
    assert '"ai-platform:local"' in generator_text
    assert "--runtime-mode" in generator_text
    assert "platform" in generator_text
    assert "busybox" not in generator_text


def test_sandbox_211_runtime_acceptance_runbook_requires_platform_mode():
    p0_plan = read(ROOT / "docs/superpowers/plans/2026-06-04-ai-platform-p0-closure.md")

    assert "--runtime-mode platform" in p0_plan
    assert "--sandbox-provider docker" in p0_plan
    assert "--sandbox-executor-image ai-platform:local" in p0_plan
    assert "executor-only callback evidence is not enough" in p0_plan
    assert "ai-platform.sandbox-latency-split.v1" in p0_plan


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
        ".ai-platform-source-revision",
        ".ai-platform-source-snapshot.json",
        ".codex/tmp/",
        ".codex/skills/",
        ".superpowers/sdd/",
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
    assert "current 211 static frontend deployment remains the active runtime entry" in combined_text
    assert "G8 platform-level multi-run orchestration and G10 workflow-owner rollout work" in combined_text
    assert "Docker compose one-command startup is not a current" in combined_text
    assert "tools/office_context_readiness.py" in combined_text
    assert "frontend run-playback context provenance" in combined_text
    assert "C:\\Users" not in combined_text
    assert "/api/ai/workbench" not in combined_text


def test_frontend_readme_matches_current_projection_audit_gate():
    readme_text = read(FRONTEND_README)

    assert "pass_with_policy_gaps" in readme_text
    assert "expected to fail" not in readme_text.lower()
    assert "continues to lint, type-check, and build" in readme_text
    assert "G6/G9" in readme_text


def test_gate_status_snapshot_records_memory_context_readiness_fields():
    gate_status_text = read(GATE_STATUS_DOC)

    assert "memory_context_controls" in gate_status_text
    assert "session_scoped_memory=true" in gate_status_text
    assert "ordinary_user_opt_out=true" in gate_status_text
    assert "retention_cleanup=true" in gate_status_text
    assert "delete_redaction=true" in gate_status_text
    assert "public_admin_projection_safe=true" in gate_status_text
    assert "long_term_cross_session_memory_fail_closed=true" in gate_status_text
    assert "ordinary-user governance/frontend rollout remains blocked" in gate_status_text


def test_governance_readiness_doc_records_b1_smoke_without_gate_closure():
    governance_text = read(GOVERNANCE_READINESS_DOC)
    memory_row = next(
        line for line in governance_text.splitlines() if line.startswith("| Memory governance |")
    )
    memory_columns = [column.strip() for column in memory_row.strip().strip("|").split("|")]
    memory_implemented = memory_columns[1]
    memory_remaining = memory_columns[2]

    assert "`runtime_acceptance_recorded`" in governance_text
    assert "keeps the B1 stage status label `local" in governance_text
    assert "partial`" in governance_text
    assert "`211_memory_enabled_document_workflow_smoke` out of G6 open gaps" in governance_text
    assert "`b1_issue_review_and_closure_evidence`" in governance_text
    assert "`b1_runtime_evidence_review_against_merged_source`" in governance_text
    assert "`b1_rollback_boundary`" in governance_text
    assert "`b1_memory_export_boundary` is recorded as a closed local contract" in governance_text
    assert "`ordinary_user_export_excludes_deleted_and_expired_records`" in governance_text
    assert "`ordinary_user_export_requires_session_scope_and_enabled_policy`" in governance_text
    assert "`admin_export_operator_projection_without_content_or_metadata`" in governance_text
    assert "runtime smoke layer" in governance_text
    assert "`211 verified`" in governance_text
    assert "B1 stage itself remains `local partial`" in governance_text
    assert "not `gate closable`" in governance_text
    assert "repo-local #75 closure evidence" in governance_text
    assert "If later runtime-affecting source changes" in governance_text
    assert "`87528bf30609092c3c4e947bdca477768af3f8e5`" in governance_text
    assert "`5cfe9569e9e0770869c6f9bfa1e6702d03ce563b`" in governance_text
    assert "closes only" in governance_text
    assert "211 service checkout remains dirty/behind" in governance_text
    assert "`9687a7720528e2f3068bfcbdccbee45f80458ec0`" not in governance_text
    assert "final #75 review and issue-closure evidence" not in governance_text
    assert "`tools/verify_b1_memory_context_workflow.py`" in governance_text
    assert "memory export boundary, and rollback" not in governance_text
    assert "`local_controls_ready_runtime_smoke_required`, and keeps status label" not in governance_text
    assert "carries `211_memory_enabled_document_workflow_smoke` in the G6 open gaps" not in governance_text
    assert "reviewed B1 `211_memory_enabled_document_workflow_smoke` evidence" in memory_implemented
    assert "B1 merged-source runtime evidence review for `87528bf`" in memory_implemented
    assert "B1 rollback boundary local operator contract" in memory_implemented
    assert "`211_memory_enabled_document_workflow_smoke`" not in memory_remaining
    assert "B1 rollback boundary" not in memory_remaining


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
        assert "all seven #21 load-test gates" in text
        for gate in (
            "api_read_write_burst",
            "run_creation_burst_by_tenant_and_user",
            "worker_processing_throughput",
            "queue_depth_and_lease_latency",
            "cancel_retry_resume_under_load",
            "sandbox_lease_creation_under_load",
            "model_gateway_timeout_and_backpressure",
        ):
            assert gate in text
        assert "include_maintenance_cleanup=false" in text
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

    assert "tools/capacity_operator_evidence_template_bundle.py" in capacity_text
    assert "tools/capacity_recorded_gate_batch_from_values.py" in capacity_text
    assert "New-Item -ItemType Directory -Force capacity-operator-inputs | Out-Null" in capacity_text
    assert "--output-dir capacity-operator-inputs" in capacity_text
    assert "--operator-input-dir capacity-operator-inputs" in capacity_text
    assert "The `--runtime-evidence-json` input must be the raw" in capacity_text
    assert "ai-platform.capacity-runtime-evidence.v1" in capacity_text
    assert "runtime_evidence_release_entry_not_supported" in capacity_text
    assert "ai-platform.capacity-operator-evidence-template-bundle.v1" in capacity_text
    assert "TODO_OPERATOR_REVIEWED_" in capacity_text
    assert "--host-sandbox-observation-json" in capacity_text
    assert "ai-platform.capacity-host-sandbox-observation.v1" in capacity_text
    assert "default_compose_mounts_docker_socket = false" in capacity_text
    assert (
        "Template bundle output is draft-only and is not recorded B3 evidence"
        in " ".join(capacity_text.split())
    )
    plan_text = read(G7_B3_EVIDENCE_CLOSURE_PLAN)
    compact_plan_text = " ".join(plan_text.split())
    assert "tools/capacity_recorded_gate_batch_from_values.py" in plan_text
    assert "directory-based fail-closed batch assembler" in compact_plan_text


def test_gate_status_records_foundation_runtime_concurrency_context_pack_blocker():
    gate_status_text = read(GATE_STATUS_DOC)
    roadmap_text = read(ROADMAP)
    release_evidence_text = read(RELEASE_EVIDENCE_INDEX)

    for text in (gate_status_text, roadmap_text):
        compact_text = " ".join(text.split())
        assert "foundation_runtime_concurrency_evidence" in text
        assert "ai-platform.foundation-runtime-concurrency.v1" in text
        assert "context_pack_version" in text
        assert "10+ concurrent" in text
        assert "dff48fb" in text
        assert "5d3d7e2" in text
        assert "79495bf" in text
        assert "380de6b" in text
        assert "negative decision-reuse probes" in text
        assert "Foundation Runtime concurrency evidence" in compact_text
        assert "Foundation Runtime" in text
        assert "concurrency" in text
        assert "platform-level multi-run orchestration" in text
        assert "production concurrency" in text
        assert "C:\\Users" not in text

    assert "dff48fbd454704af64871c039c59d396d8f9aaf7" in release_evidence_text
    assert "2026-06-14-211-foundation-alpha-poc-dff48fb-foundation-runtime-concurrency.json" in release_evidence_text
    assert "5d3d7e2207d625817d193898c22d29d2f487fa4b" in release_evidence_text
    assert "2026-06-14-211-foundation-alpha-poc-5d3d7e2-foundation-runtime-concurrency.json" in release_evidence_text
    assert "79495bf4954017351db6d19494a16099fe2ee0bf" in release_evidence_text
    assert "2026-06-14-211-foundation-alpha-poc-79495bf-foundation-runtime-concurrency.json" in release_evidence_text
    assert ACTIVE_RUNTIME_SUBJECT_SHA in release_evidence_text
    assert "a15c74f0fe98914a893ab7ea784c6be941e0cd71" in release_evidence_text
    assert "2026-06-17-211-foundation-alpha-poc-a15c74f-foundation-runtime-concurrency.json" in release_evidence_text
    assert "2026-06-15-211-foundation-alpha-poc-380de6b-foundation-runtime-concurrency.json" in release_evidence_text
    assert "verified_foundation_runtime_concurrency" in release_evidence_text
    assert "negative tool-permission reuse probes" in release_evidence_text
    assert "queue_probe_sample_count" in release_evidence_text
    assert "does not raise production concurrency defaults" in release_evidence_text
    assert "fresh current-subject evidence under #65" in release_evidence_text
    assert "open platform-level multi-run orchestration" in release_evidence_text


def test_gate_status_does_not_overstate_superseded_evidence_as_current():
    gate_status_text = read(GATE_STATUS_DOC)
    compact_text = " ".join(gate_status_text.split())

    assert "4039e4b source-runtime relation manifest and #138 evidence" not in gate_status_text
    assert "The #164 runtime-subject evidence scope" in compact_text
    assert (
        "4039e4b, 87528bf, 75ab69b, and #112/#124/#138 evidence are retained as superseded reviewed history"
        in compact_text
    )
    assert "`380de6b` evidence above is the historical Foundation Alpha baseline" in compact_text
    assert "active B0 latest-main reference is `87528bf` / #124" not in gate_status_text
    assert "e8e8a0a` runtime still lacks a passing runtime POC smoke" in gate_status_text
    assert "readiness must keep reporting" in gate_status_text
    assert (
        f"after the `{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}` source-runtime relation manifest and reviewed evidence"
        in compact_text
    )
    assert "runtime rollout requirement such as `source_synced_runtime_pending`" in gate_status_text
    assert "the `dab7dbc` / #164 evidence is the active B0 latest-main reference" not in gate_status_text
    assert "the `dab7dbc` / #164 evidence is the current reviewed runtime-subject reference" not in compact_text
    assert "the `d94d274` / #164 evidence is the current reviewed runtime-subject reference" not in compact_text
    assert (
        f"the `{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}` / B0 evidence is the latest reviewed runtime-subject reference"
        in compact_text
    )
    assert "The immediately superseded B0 runtime-subject refresh is `c3d6525d8980c43ce9d13a2fd9016bbe61597327`" in compact_text
    assert "the `e4c0e9d` / #164 evidence is the latest reviewed runtime-subject reference" not in compact_text
    assert "the `e7558cc` / #164 evidence is the latest reviewed runtime-subject reference" not in compact_text
    assert "the `17dc3ae` / #164 evidence is the latest reviewed runtime-subject reference" not in compact_text
    assert "the `0a9e70a` / #164 evidence is the latest reviewed runtime-subject reference" not in compact_text
    assert "the `df85a9f` / #164 evidence is the latest reviewed runtime-subject reference" not in compact_text
    assert "the `a4bded0` / #164 evidence is the latest reviewed runtime-subject reference" not in compact_text
    assert "the `e8e8a0a` / #164 evidence is the active B0 latest-main reference" not in gate_status_text
    assert "the `4039e4b` / #138 evidence is the active B0 latest-main reference" not in gate_status_text
    assert "when it consumes the 87528bf source-runtime relation manifest and #124 evidence" not in compact_text
    assert "when it consumes the 75ab69b source-runtime relation manifest and #112 evidence" not in compact_text
    assert "the `380de6b` evidence above is the active Foundation Alpha POC reference" not in gate_status_text


def test_gate_status_records_issue164_b0_runtime_refresh_with_source_caveats():
    gate_status_text = read(GATE_STATUS_DOC)
    backend_prd_text = read(BACKEND_PRD)
    combined_text = f"{gate_status_text}\n{backend_prd_text}"
    compact_text = " ".join(combined_text.split())

    for expected in (
        "#164",
        "e4c0e9d0298c684df369afecd29ec902fcc2221d",
        "ai-platform:e4c0e9d-issue164-post-pr206-runtime-only-v2",
        "runtime_rollout_required",
        "Foundation Runtime concurrency refresh",
        "external env-file caveat",
    ):
        assert expected in combined_text
    assert "Foundation Runtime concurrency refresh for the same `e4c0e9d` runtime subject verified 12 concurrent" in compact_text
    assert "This removes the `foundation_runtime_concurrency_evidence` blocker for the named `e4c0e9d` runtime subject only" in compact_text
    assert "external env-file label caveat" in compact_text
    assert "production auth rollout evidence" in compact_text
    assert "before any G0 closure claim" in compact_text
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" in compact_text
    assert (
        "The #164 runtime-subject evidence scope now sits behind the newer "
        f"`{ACTIVE_RUNTIME_SUBJECT_SHORT_SHA}` B0 runtime-subject refresh"
        in compact_text
    )
    assert "not exact current-source runtime verification" in compact_text

    for boundary in (
        "This records reviewed 211 runtime-subject evidence for historical #164 context",
        "does not constitute current G7/B3 closure evidence for any #164/G7/B3 closure claim",
        "does not close G0 source authority",
        "does not close B1/B2/B3 product gates",
        "does not raise production concurrency defaults",
        "does not claim Docker sandbox hardening",
        "does not enable ordinary-user platform-level multi-run orchestration exposure",
    ):
        assert boundary in compact_text


def test_capacity_docs_record_latest_211_bounded_probe_without_closing_gate():
    capacity_text = read(CAPACITY_BASELINE_DOC)
    gate_status_text = read(GATE_STATUS_DOC)

    assert "GitHub issue #21 is currently closed" in capacity_text
    assert "capacity-upgrade evidence gate" in capacity_text
    assert "remains open" in capacity_text
    assert "This evidence keeps #21 open" not in capacity_text
    assert "This follow-up evidence keeps #21 open" not in capacity_text
    assert "3d607c96b8d8e21f59461bd94cc4b64de1d49dd5" in capacity_text
    assert "ai-platform:3d607c9-g9-latency-acceptance" in capacity_text
    assert "probe_completed_not_gate_evidence" in capacity_text
    assert "sent_requests = 20" in capacity_text
    assert "status counts were `{\"200\": 20}`" in capacity_text
    assert "does_not_mark_gate_recorded = true" in capacity_text
    assert "not accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence" in capacity_text
    assert "still does not satisfy the" in capacity_text
    assert "recorded capacity-evidence gate" in capacity_text
    assert "must not be used to raise production defaults" in capacity_text
    assert "211 Runtime Evidence - 2026-07-02, commit `ae6b7e5`" in capacity_text
    assert "ai-platform:ae6b7e5-g7-b3-label-repair-v1" in capacity_text
    assert "HTTP `200`" in capacity_text
    assert "all required capacity sections" in capacity_text
    assert "The derived `ai-platform.capacity-profile-readiness.v1` result kept" in capacity_text
    assert "observed_peak_sdk_subagents_per_session" in capacity_text
    assert "does not prove the 10 sessions x peak 4 SDK subagents/session profile" in capacity_text
    assert "capacity_recorded_gate_evidence_packet.py" in capacity_text
    assert "ai-platform.capacity-recorded-gate-evidence-packet-result.v1" in capacity_text
    assert "bounded probe output cannot be promoted into recorded gate evidence" in capacity_text
    assert "--skip-maintenance-cleanup" in capacity_text
    assert "include_maintenance_cleanup=false" in capacity_text
    compact_capacity_text = " ".join(capacity_text.split())
    assert "211 Runtime Evidence - 2026-07-02, PR #304 runtime subject `decf33a`" in capacity_text
    assert "ai-platform:decf33a-g7-b3-post-300-followup-v1" in capacity_text
    assert "ai-platform-frontend:e2189d1" in capacity_text
    assert "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false" in capacity_text
    assert "returned HTTP `200`" in compact_capacity_text
    assert "2026-07-02-211-capacity-runtime-readiness-decf33a.json" in capacity_text
    assert "not a raw runtime payload export and is not recorded B3 load evidence" in compact_capacity_text
    assert "Fresh ad-hoc anonymous reads of" in gate_status_text
    assert "HTTP `401`" in gate_status_text
    assert "HTTP `403`" in gate_status_text
    assert "--gateway-secret-env" in capacity_text
    assert "AI_PLATFORM_GATEWAY_SECRET" in capacity_text
    assert "still visibility-only unless it is followed by approved load execution" in compact_capacity_text
    assert "profile `unproven_default`" in capacity_text
    assert "`profile_evidence` was empty" in capacity_text
    assert "This `decf33a` capture supersedes the earlier `4805031`" in compact_capacity_text
    assert "capacity-pending/HTTP-500 observation for the currently running `decf33a` runtime subject only" in compact_capacity_text
    assert "PR #304 is now merged at `a9c78efa812efe96b0366011a0c731cb11eb0099`" in compact_capacity_text
    assert "211 Runtime Evidence - 2026-07-02, PR #305 merge commit `28676df`" in capacity_text
    assert "ai-platform:28676df-g7-b3-current-main-runtime-only-v1" in capacity_text
    assert PR305_G7_B3_SHA in capacity_text
    assert "repo-local source marker still read `decf33a017e0b97e2a2992f80e3ccdc19152c1f4`" in compact_capacity_text
    assert "status `blocked_missing_admin_runtime_sections`" in capacity_text
    assert "the readiness result treated `sandbox` as missing" in compact_capacity_text
    assert "g7-current-main-28676df-20260702130121" in capacity_text
    assert "No module named 'pydantic'" in capacity_text
    assert "g7-current-main-28676df-workspace-user-fix-20260702135351" in capacity_text
    assert "could not create" in capacity_text
    assert "`/workspace/runtime`" in capacity_text
    assert "not reviewed deployed-runtime G7 evidence" in capacity_text
    assert "does not close G0 because the 211 repo-local source marker remains stale" in compact_capacity_text
    assert "Post-PR #306 Runtime Note - 2026-07-02, merge commit `9c669761`" in capacity_text
    assert "ai-platform:9c66976-g7-b3-workspace-owner-v1" in capacity_text
    assert PR306_G7_B3_SHA in capacity_text
    assert "No reviewed B3 capacity runtime evidence entry has been recorded for `9c669761`" in compact_capacity_text
    assert "g7-current-main-9c66976-20260702145801" in capacity_text
    assert "executed_task=false" in capacity_text
    assert "sandbox_provider=unknown" in capacity_text
    assert "[Errno 13] Permission denied: '[redacted-path]'" in capacity_text
    assert "g7-current-main-9c66976-sudo-20260702155816" in capacity_text
    assert "2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json" in capacity_text
    assert "sudo-context explicit G7" in capacity_text
    assert "g7-live-env-hardening-9c669761-sudo-20260703091724" in capacity_text
    assert "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json" in capacity_text
    assert "2026-07-03-211-foundation-alpha-poc-9c669761-foundation-runtime-concurrency.json" in capacity_text
    assert "Those G7/FRC records can support a G7" in capacity_text
    assert "`candidate_evidence_requires_review` reading for `9c669761`" in capacity_text
    assert "does not make G7 or B3 gate-closable" in capacity_text
    assert "C:\\Users" not in capacity_text

    for text in (capacity_text, gate_status_text):
        compact_capacity_or_status_text = " ".join(text.split())
        assert "B3 operator-reviewed recorded snapshot source contract" in text
        assert "ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1" in text
        assert "b3_10x4_sdk_subagents" in text
        assert "10 sessions x peak 4 SDK subagents/session" in text
        assert "target_profile_id = b3_10x4_sdk_subagents" in text
        assert "allowlisted `evidence_source`" in text or "allowlisted evidence source" in text
        assert "platform_runtime_profile" in text
        assert "live_worker_run_payload" in text
        assert "operator_reviewed_recorded_snapshot" in text
        assert "observed_concurrent_sessions >= 10" in text
        assert "observed_peak_sdk_subagents_per_session >= 4" in text
        assert "sdk_subagent_fanout_measurement_ref" in text
        assert "production_concurrency_defaults_raised = false" in text
        assert "safe_concurrency_claimed = false" in text
        assert "ordinary_user_platform_multi_run_orchestration_enabled = false" in text
        assert "legacy alias `ordinary_user_multi_agent_enabled = false`" in text
        assert (
            "normalizes it only to the canonical B3 packet-level non-expansion boolean"
            in compact_capacity_or_status_text
            or "readiness normalizes it only as B3 packet non-expansion evidence"
            in compact_capacity_or_status_text
        )
        assert "not a substitute for the route/status invariant" in compact_capacity_or_status_text
        assert "canonical platform-level multi-run flag" not in compact_capacity_or_status_text
        assert "runtime_source_identity_and_image_labels" in text
        assert "tenant_user_skill_mix" in text
        assert "token_cost_ledger" in text
        assert "event_artifact_volume" in text
        assert "sandbox_pressure_and_cleanup" in text
        assert "latency_p50_p95_p99" in text
        assert "error_budget_and_dead_letters" in text
        assert "rollback_plan_and_stop_conditions" in text
        assert "does_not_raise_defaults = true" in text
        assert "does_not_claim_safe_concurrency = true" in text
        assert "does_not_enable_ordinary_user_platform_multi_run_orchestration = true" in text
        assert "does_not_close_b3_gate = true" in text
        assert "source contract only" in text
        assert "does not raise production defaults" in text
        assert "does not close B3" in text
        assert "ordinary-user platform-level multi-run orchestration exposure" in " ".join(text.split())
    assert "C:\\Users" not in text

    assert "Reviewed `945db2b` B3 capacity visibility also exists" in gate_status_text
    assert "The latest reviewed `a294727` read-only capacity runtime evidence records Admin Runtime HTTP `200`" not in gate_status_text
    assert "current latest-status reading uses the reviewed `a294727` capacity visibility entry" not in gate_status_text
    assert "The current `a294727` entry is still fail-closed" not in gate_status_text
    assert (
        "The current `945db2b` entry is still fail-closed at "
        "`blocked_missing_load_test_evidence`"
        in " ".join(gate_status_text.split())
    )
    assert "the earlier reviewed `a294727`, `bbe23d5`, and `61073b1` visibility records are retained as prior baselines" in gate_status_text
    assert "`blocked_missing_admin_runtime_sections`" in gate_status_text
    assert "`sandbox` was missing/degraded" in gate_status_text
    assert "the earlier reviewed `a294727`, `bbe23d5`, and `61073b1` visibility records are retained as prior baselines" in gate_status_text
    assert HISTORICAL_DIRTY_G7_B3_RUNTIME_SHA in capacity_text
    assert "so `755e50e` is not latest clean `origin/main` runtime evidence" in compact_capacity_text
    assert "The latest reviewed capacity visibility entry is now the `945db2b` record" in compact_capacity_text
    assert "Even when a deployment profile sets `SANDBOX_CONTAINER_PROVIDER=docker`" in compact_capacity_text
    assert (
        "capacity baseline remains fail-closed until an approved G7 status-upgrade "
        "decision and B3 recorded load/profile evidence are present"
        in compact_capacity_text
    )
    assert "ai-platform:4805031-g7-b3-post-297-label-repair-v2" in capacity_text
    assert "ai-platform-frontend:ba81a0b" in capacity_text
    assert (
        "all seven operator-reviewed recorded load-test gates and the `b3_10x4_sdk_subagents` "
        "profile evidence are still missing"
        in " ".join(capacity_text.split())
    )
    assert "Admin Runtime HTTP `200`" in gate_status_text
    assert "all required Admin Runtime capacity sections present" not in gate_status_text
    assert "all required Admin Runtime sections observed" in gate_status_text
    assert "schema `ai-platform.capacity-runtime-evidence.v1`" in gate_status_text
    assert "nested gate readiness `blocked_missing_load_test_evidence`" in gate_status_text
    assert "every required `b3_10x4_sdk_subagents` profile evidence field are still missing" in gate_status_text
    assert "The latest reviewed `a294727` read-only capacity runtime evidence records Admin Runtime HTTP `200`" not in gate_status_text
    assert "Record approved load evidence for the seven gates before raising any production default" in gate_status_text
    assert "This is source/runtime visibility plus source contract only, not B3 closure" in gate_status_text


def test_current_b3_sandbox_diagnostic_documents_socket_boundary_without_gate_closure():
    index_text = read(RELEASE_EVIDENCE_INDEX)
    gate_status_text = read(GATE_STATUS_DOC)
    plan_text = read(G7_B3_EVIDENCE_CLOSURE_PLAN)
    compact_index_text = " ".join(index_text.split())
    compact_gate_status_text = " ".join(gate_status_text.split())
    compact_plan_text = " ".join(plan_text.split())

    assert CURRENT_G7_B3_SANDBOX_DIAGNOSTIC.is_file()
    assert POST_PR317_B3_SANDBOX_DIAGNOSTIC.is_file()
    assert POST_PR319_B3_HOST_SANDBOX_OBSERVATION.is_file()
    assert POST_PR321_B3_HOST_SANDBOX_OBSERVATION.is_file()
    payload = json.loads(CURRENT_G7_B3_SANDBOX_DIAGNOSTIC.read_text(encoding="utf-8"))
    post_pr317_payload = json.loads(POST_PR317_B3_SANDBOX_DIAGNOSTIC.read_text(encoding="utf-8"))
    post_pr319_payload = json.loads(POST_PR319_B3_HOST_SANDBOX_OBSERVATION.read_text(encoding="utf-8"))
    post_pr321_payload = json.loads(POST_PR321_B3_HOST_SANDBOX_OBSERVATION.read_text(encoding="utf-8"))

    assert "2026-07-04-211-b3-sandbox-observation-61073b1.json" in index_text
    assert "2026-07-04-211-b3-host-sandbox-observation-bbe23d5.json" in index_text
    assert "2026-07-04-211-b3-host-sandbox-observation-a294727.json" in index_text
    assert "2026-07-05-211-b3-host-sandbox-observation-945db2b.json" in index_text
    assert "API container Docker socket absent" in compact_index_text
    assert "default compose does not mount Docker socket" in compact_index_text
    assert "`docker-compose.sandbox.yml` is the explicit socket-bearing path" in compact_index_text
    assert "overview sandbox remains degraded/unavailable" in compact_index_text
    assert "host_sandbox_observation_status=accepted" in compact_index_text
    assert "readiness_status_after_host_observation=blocked_missing_load_test_evidence" in compact_index_text
    assert "diagnostic only and does not close B3/G7" in compact_index_text
    assert "same-subject host-side replay records `host_sandbox_observation_status=accepted`" in compact_gate_status_text
    assert "`readiness_status_after_host_observation=blocked_missing_load_test_evidence`" in compact_gate_status_text
    assert "reviewed B3 capacity visibility for `945db2b`" in compact_plan_text
    assert "host-side sandbox observation status `accepted`" in compact_plan_text
    assert "`bbe23d5` and `61073b1` visibility entries remain retained as prior baseline evidence only" in compact_plan_text

    assert payload["schema_version"] == "ai-platform.release-evidence-diagnostic-entry.v1"
    assert payload["evidence_id"] == "2026-07-04-211-b3-sandbox-observation-61073b1"
    assert payload["commit_sha"] == CURRENT_G7_B3_RUNTIME_SHA
    assert payload["runtime_subject_commit_sha"] == CURRENT_G7_B3_RUNTIME_SHA
    assert payload["issue_refs"] == ["#21"]
    assert payload["review_status"] == "diagnostic_only_not_reviewed_release_evidence"
    assert payload["does_not_close_b3"] is True
    assert payload["does_not_close_g7"] is True
    assert payload["does_not_mark_b3_recorded_evidence"] is True
    assert payload["does_not_make_211_verified"] is True
    assert payload["does_not_make_gate_closable"] is True
    assert post_pr317_payload["schema_version"] == "ai-platform.capacity-host-sandbox-observation.v1"
    assert post_pr317_payload["evidence_id"] == "2026-07-04-211-b3-host-sandbox-observation-bbe23d5"
    assert post_pr317_payload["commit_sha"] == POST_PR317_G7_B3_RUNTIME_SHA
    assert post_pr317_payload["runtime_subject_commit_sha"] == POST_PR317_G7_B3_RUNTIME_SHA
    assert post_pr317_payload["diagnostic_only"] is True
    assert post_pr317_payload["does_not_mark_b3_recorded_evidence"] is True
    assert post_pr317_payload["does_not_close_b3"] is True
    assert post_pr319_payload["schema_version"] == "ai-platform.capacity-host-sandbox-observation.v1"
    assert post_pr319_payload["evidence_id"] == "2026-07-04-211-b3-host-sandbox-observation-a294727"
    assert post_pr319_payload["commit_sha"] == POST_PR319_G7_B3_RUNTIME_SHA
    assert post_pr319_payload["runtime_subject_commit_sha"] == POST_PR319_G7_B3_RUNTIME_SHA
    assert post_pr319_payload["diagnostic_only"] is True
    assert post_pr319_payload["does_not_mark_b3_recorded_evidence"] is True
    assert post_pr319_payload["does_not_close_b3"] is True
    assert post_pr321_payload["schema_version"] == "ai-platform.capacity-host-sandbox-observation.v1"
    assert post_pr321_payload["evidence_id"] == "2026-07-05-211-b3-host-sandbox-observation-945db2b"
    assert post_pr321_payload["commit_sha"] == POST_PR321_G7_B3_RUNTIME_SHA
    assert post_pr321_payload["runtime_subject_commit_sha"] == POST_PR321_G7_B3_RUNTIME_SHA
    assert post_pr321_payload["diagnostic_only"] is True
    assert post_pr321_payload["does_not_mark_b3_recorded_evidence"] is True
    assert post_pr321_payload["does_not_close_b3"] is True

    observations = payload["observations"]
    assert observations["api_container_docker_socket_present"] is False
    assert observations["default_compose_mounts_docker_socket"] is False
    assert observations["sandbox_compose_mounts_docker_socket"] is True
    assert observations["socket_bearing_compose_path"] == "deploy/ai-platform/docker-compose.sandbox.yml"

    sandbox = payload["admin_runtime_overview_sandbox"]
    assert sandbox["container_observation_degraded"] is True
    assert sandbox["list_runtime_containers_status"] == "unavailable"
    assert sandbox["leases"]["active"] == 0
    assert sandbox["leases"]["released"] == 100

    replay = payload["host_side_snapshot_replay"]
    assert replay["replay_status"] == "diagnostic_replay_only_not_reviewed_release_evidence"
    assert replay["host_sandbox_observation_status"] == "accepted"
    assert replay["admin_runtime_missing_sections_after_host_observation"] == []
    assert replay["readiness_status_after_host_observation"] == "blocked_missing_load_test_evidence"
    assert replay["missing_load_test_gates_after_host_observation"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert replay["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert replay["does_not_mark_b3_recorded_evidence"] is True
    assert replay["does_not_make_gate_closable"] is True

    assert "default stack must stay no-socket" in " ".join(payload["notes"])
    assert "controlled host-side observation or explicit approved sandbox compose path" in " ".join(
        payload["required_next_steps"]
    )


def test_current_status_docs_summarize_g8_b3_boundaries_without_overclaiming():
    gate_status_text = read(GATE_STATUS_DOC)
    roadmap_text = read(ROADMAP)
    combined_text = f"{gate_status_text}\n{roadmap_text}"
    compact_text = " ".join(combined_text.split())
    compact_roadmap_text = " ".join(roadmap_text.split())
    compact_gate_status_text = " ".join(gate_status_text.split())

    assert "Current Reading Guide" in gate_status_text
    assert "single current gate/runtime status matrix" in compact_gate_status_text
    assert "当前路线进展读法" in roadmap_text
    assert "Historical progress retained below current state" in roadmap_text
    assert "`945db2b` image" in roadmap_text
    assert "PR #321 `945db2b` runtime refresh" in compact_roadmap_text
    current_gate_table = gate_status_text.split("## Current Gate Status", 1)[1].split(
        "## Issue-Driven Thin Spots",
        1,
    )[0]
    assert "The table below is a gate/evidence matrix" in current_gate_table
    assert "Rows that mention `96f27bb` describe reviewed 2026-06-30" in current_gate_table
    assert "GitHub `main` now includes PR #322 merge commit" in current_gate_table
    assert POST_PR321_G7_B3_RUNTIME_SHA in current_gate_table
    assert "latest reviewed 211 runtime subject remains PR #315 merge commit" not in " ".join(current_gate_table.split())
    assert "current GitHub `main`, the 211 repo-local source marker, and the 211 API/worker canonical runtime image labels are at `15903fdfe96ffcfba9daa1252741111017dcf832`" not in " ".join(current_gate_table.split())
    assert "`ae6b7e5` FRC evidence is recorded as Foundation Runtime POC correctness evidence" in " ".join(current_gate_table.split())
    assert "and B3 follow-ups remain open" in " ".join(current_gate_table.split())
    assert (
        "the `ae6b7e5` one-shot G7 verifier artifacts for "
        "`g7-current-main-ae6b7e5-20260701172910` are wrapped in reviewed repo-local "
        "G7 sandbox release evidence"
        in " ".join(current_gate_table.split())
    )
    assert (
        "the `ae6b7e5` one-shot G7 verifier artifacts for "
        "`g7-current-main-ae6b7e5-20260701172910` are wrapped in reviewed repo-local "
        "G7 sandbox release evidence"
        in " ".join(current_gate_table.split())
    )
    assert "The `96f27bb` source-runtime relation is historical evidence only" in current_gate_table
    assert "2026-07-02 `ae6b7e5` verified Foundation Runtime concurrency evidence" in current_gate_table
    assert "do not prove current-main `ae6b7e5` Foundation Runtime concurrency" not in current_gate_table
    assert "completed G8/B3 cleanup" not in gate_status_text
    assert "已完成：G8/B3" not in roadmap_text
    assert "Historical G8 exposure wording in old evidence/follow-up keys is" in compact_gate_status_text
    assert (
        "not as SDK subagent availability or B3 capacity evidence"
        in compact_gate_status_text
    )
    assert "旧普通用户 multi-agent exposure 说法会把普通用户平台级 parent/child" in compact_roadmap_text
    assert "只能作为历史 evidence/follow-up 含义读取" in compact_roadmap_text
    assert "不能作为当前状态名" in compact_roadmap_text
    assert (
        "At the earlier post-PR #316 status-sync slice, GitHub `main` included PR #316 "
        "merge commit `5fe44827708fe24441a4c451dee9c691281d3c21`. PR #316 "
        "merged reviewed docs/test/evidence-status cleanup only; it did not deploy "
        "a new 211 runtime"
        in compact_gate_status_text
    )
    assert (
        "The clean current-main `61073b1` G7 verifier evidence is present and "
        "passed all eight checks"
        in compact_gate_status_text
    )
    assert (
        "same-subject Foundation Runtime concurrency evidence for `61073b1` "
        "is now recorded with `verified_foundation_runtime_concurrency`"
        in compact_gate_status_text
    )
    assert "earlier post-PR #316 `main` slice or the `61073b1` runtime subject `211 verified`" in compact_gate_status_text
    assert (
        "本轮 post-PR #321 legacy source-marker cleanup runtime refresh 的 GitHub `main` 已包含 PR #321 merge "
        f"commit `{POST_PR321_G7_B3_RUNTIME_SHA}`"
        in compact_roadmap_text
    )
    assert (
        "211 repo-local source marker、 source snapshot、API/worker image labels 和三个 API/worker in-container marker "
        "`/app/.ai-platform-source-revision`、`/app/.codex-source-revision`、 `/app/.source-commit` 已绑定 "
        f"`{POST_PR321_G7_B3_RUNTIME_SHORT_SHA}`"
        in compact_roadmap_text
    )
    assert "API/worker 运行 `ai-platform:945db2b-g7-legacy-source-markers-v1`" in compact_roadmap_text
    assert "`755e50e` dirty-runtime v2 G7/FRC/capacity visibility 都作为历史 reviewed candidate evidence 保留" in compact_roadmap_text
    assert "legacy in-container marker files at `9c669761` and `28676df`" in compact_gate_status_text
    assert PR311_G7_B3_SHA not in current_gate_table
    assert PR312_G7_B3_SHA not in current_gate_table
    assert "PR #308 is merged into GitHub `main` at `15903fdfe96ffcfba9daa1252741111017dcf832`" in compact_gate_status_text
    assert "reviewDecision` remained empty" in compact_gate_status_text
    assert PR304_G7_B3_SHA in combined_text
    assert PR305_G7_B3_SHA in combined_text
    assert PR306_G7_B3_SHA in combined_text
    assert PR308_G7_B3_SHA in combined_text
    assert HISTORICAL_DIRTY_G7_B3_RUNTIME_SHA in combined_text
    assert CURRENT_G7_B3_RUNTIME_SHA in combined_text
    assert "merged=false" not in compact_gate_status_text
    assert "open draft" not in compact_gate_status_text
    assert "Reviewed 211 runtime evidence for earlier subjects remains historical same-subject evidence only" in compact_gate_status_text
    assert "PR #305 `codex/g7-b3-post304-doc-state` 已 merge 到 GitHub `main`" in compact_roadmap_text
    assert "PR #306 `codex/g7-b3-current-main-runtime` 随后已 squash-merge 到 GitHub `main`" in compact_roadmap_text
    assert "PR #308 `codex/g8-b3-doc-status-cleanup` 已 squash-merge 到 GitHub `main`" in compact_roadmap_text
    assert "PR #304 `codex/g7-b3-post-300-followup` 已 merge 到 GitHub `main`" in compact_roadmap_text
    assert "Earlier current-live `4805031` observation remains historical operational context" in compact_gate_status_text
    assert "API/worker images, labels, and `SANDBOX_EXECUTOR_IMAGE` were observed at `ai-platform:4805031-g7-b3-post-297-label-repair-v2`" in compact_gate_status_text
    assert "captured `4805031` G7 audit was no longer blocked by executor-image drift" in compact_gate_status_text
    assert "`blocking_reasons=[]`" in compact_gate_status_text
    assert (
        '`required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`'
        in compact_gate_status_text
    )
    assert "Current GitHub `main` is PR #296 merge commit" not in gate_status_text
    assert "post-PR #296 current-main runtime rollout" not in gate_status_text
    assert PR297_G7_B3_SHA in combined_text
    assert POST_PR299_MAIN_SHA in combined_text
    assert "ae6b7e52c656fd8296cf039834ce8d8559b01228" in combined_text
    assert "PR #296 document-state cleanup" in gate_status_text
    assert "`source_tree_commit_sha=ae6b7e52c656fd8296cf039834ce8d8559b01228`" in combined_text
    assert "`runtime_subject_commit_sha=ae6b7e52c656fd8296cf039834ce8d8559b01228`" in combined_text
    assert "`snapshot_source=codex_origin_main_archive_sync`" in combined_text
    assert "bd690f72723080beeb820d07679da59d84c7913e" in combined_text
    assert "ai-platform:ae6b7e5-g7-current-main-runtime-only-v1" in combined_text
    assert "ai-platform:ae6b7e5-g7-b3-label-repair-v1" in combined_text
    assert "ai-platform:4805031-g7-b3-post-297-label-repair-v2" in combined_text
    assert "ai-platform-frontend:ba81a0b" in combined_text
    assert "stale_runtime_alias_label_mismatch" in combined_text
    assert "clears only `stale_runtime_alias_label_mismatch`" in compact_gate_status_text
    assert "旧 `stale_runtime_alias_label_mismatch` 已由 reviewed `ae6b7e5` label-repair evidence 清掉" in compact_roadmap_text
    assert "canonical labels、legacy alias labels 和 in-container source markers 指向" in compact_roadmap_text
    assert "`4805031fc3333ccbf38224172e4e85e21c0630bb`；frontend image 是" in compact_roadmap_text
    assert "external env-file label" in compact_gate_status_text
    assert "外部 runtime env file" in compact_roadmap_text
    assert "211 source is still `bd690f7`" not in gate_status_text
    assert "source marker 仍是 `bd690f7" not in roadmap_text
    assert "211 backend source marker 仍是 `ae6b7e52c656fd8296cf039834ce8d8559b01228`" in compact_roadmap_text
    assert "source/runtime parity with followups open" not in gate_status_text
    assert "API labels point to `df85a9f` and worker labels point to `bd690f7`" not in current_gate_table
    assert "API/worker runtime images are currently split" not in current_gate_table
    assert "d318f9f6a68b4c17e221eb32705b3f31d349227a" in combined_text
    assert "ai-platform:d318f9f-g7-b3-runtime-only-v1" in combined_text
    assert "g7-runtime-probe-20260701203418" in combined_text
    assert "all eight verifier checks passing" in compact_gate_status_text
    assert "8 个 verifier checks" in " ".join(roadmap_text.split())
    assert "not a reviewed local `docs/release-evidence/b2-sandbox/...` entry" in compact_gate_status_text
    assert "不是 reviewed local release-evidence entry" in " ".join(roadmap_text.split())
    assert "repo-local reviewed release-evidence entry" in compact_gate_status_text
    assert "包装为 repo-local reviewed G7 sandbox runtime smoke evidence" in compact_roadmap_text
    assert "`source_tree_dirty=false`" in combined_text
    assert "Do not call G7 complete, B3 complete, Foundation Alpha complete, production-ready, `211 verified`, or `gate closable`" in compact_gate_status_text
    assert "g7-current-main-28676df-workspace-user-fix-20260702135351" in compact_gate_status_text
    assert "executor workspace ownership bug under `cap_drop=[\"ALL\"]`" in compact_gate_status_text
    assert "led to PR #306" in compact_gate_status_text
    assert "The `9c669761` live-default G7/FRC pair and the `15903fd` label-clean live-default G7/FRC pair are now historical same-subject evidence" in compact_gate_status_text
    assert "The `755e50e` dirty-runtime v2 G7 verifier plus same-subject FRC success advance" in compact_gate_status_text
    assert "The post-PR #321 `945db2b` legacy source-marker cleanup rollout advances the current runtime source-authority slice" in compact_gate_status_text
    assert "Fresh reviewed `945db2b` G7 live-env hardening evidence and B3 capacity visibility are now recorded" in compact_gate_status_text
    assert "same-subject Foundation Runtime concurrency evidence, approved G7 status upgrade, B3 recorded load/profile evidence, and #164 current closure evidence are not present" in compact_gate_status_text
    assert "#164 history is historical B0 latest-main runtime-refresh context only" in compact_gate_status_text
    assert "do not use it as current G7/B3, Foundation Alpha, production-ready, gate-closable, or closure evidence" in compact_gate_status_text
    assert "The latest reviewed G7 verifier is the `945db2b` run" in compact_gate_status_text
    assert "all three API/worker in-container source marker files bound to `945db2b`" in compact_gate_status_text
    assert "no reviewed `945db2b` G7 live-env verifier is recorded yet" not in compact_gate_status_text
    assert "the historical `bbe23d5` operator status-review artifact is recorded but explicitly sets `status_upgrade_decision=not_approved_for_closure`" in compact_gate_status_text
    assert "`15903fd` label-clean live-env G7 evidence plus same-subject Foundation Runtime concurrency evidence as the current G7 runtime input" not in compact_gate_status_text
    assert "At the `15903fd` slice, the 2026-07-03 label-clean readback confirmed API/worker live defaults used" in compact_gate_status_text
    assert "this removed that subject's G7 executor-image drift and stale legacy alias blockers" in compact_gate_status_text
    assert "These entries are not full issue/gate closure or current-source `211 verified`" in gate_status_text
    assert "`status=candidate_evidence_requires_review`" in gate_status_text
    assert "`blocking_reasons=[]`" in gate_status_text
    assert (
        "The later historical dirty-runtime subject `755e50e` has a reviewed G7 live-env "
        "hardening entry"
        in " ".join(gate_status_text.split())
    )
    assert (
        "whose readiness file reports `verified_foundation_runtime_concurrency`, "
        "`verified=true`, `failures=[]`, 12 concurrent requests/runs/sessions "
        "across 2 tenants and 4 users"
        in " ".join(gate_status_text.split())
    )
    assert (
        "The earlier blocked FRC attempt at `/tmp/frc-755e50e-20260703T090109Z` "
        "is superseded by same-subject FRC evidence"
        in " ".join(gate_status_text.split())
    )
    assert (
        '`required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`'
        in gate_status_text
    )
    assert (
        "The compose external env-file label and local runtime-affecting source delta are "
        "G0/source-authority and production-hardening boundaries"
        in compact_gate_status_text
    )
    assert "B3 seven-gate load evidence remains the B3 blocker" in compact_gate_status_text
    assert "no-masq egress network blocked the callback exception path" in compact_text
    assert "bind the callback receiver on `0.0.0.0`" in compact_gate_status_text
    assert "`http://host.docker.internal:{port}/callback`" in combined_text
    assert "verifier-helper callback default fix" in compact_text
    assert "codex/g8-b3-status-refresh" in combined_text
    assert "PR #294 merged `codex/g8-b3-status-refresh` into `main`" in compact_gate_status_text
    assert "513cc5e2280c35218e7edf297b7f02494e82a164" in combined_text
    assert "current GitHub `main` through PR #296" in compact_gate_status_text
    assert "PR #297 then advanced G7/B3 evidence-closure tooling and merged at `4805031fc3333ccbf38224172e4e85e21c0630bb`" in compact_gate_status_text
    assert "g7-current-main-ae6b7e5-20260701172910" in combined_text
    assert "2026-07-01-211-g7-runtime-identity-label-repair-ae6b7e5.json" in combined_text
    assert "sha256:59d9c73fe449fd3285aa88bc38dcc1aa6b96a4569ed4b9d447773c9fea0f5140" in combined_text
    assert "runtime probe results, sandbox evidence, and verifier summary" in compact_gate_status_text
    assert "`runtime_mode=platform`" in combined_text
    assert "`sandbox_provider=docker`" in combined_text
    assert "`executed_task=true`" in combined_text
    assert "`callback_auth=token`" in combined_text
    assert "`cancel_stops_container=true`" in combined_text
    assert "PR #294 is open" not in combined_text
    assert "PR #296 is open" not in combined_text
    assert "complete PR #294 review and merge" not in compact_text
    assert "callback exception path" in roadmap_text
    assert "blocker diagnostic" in roadmap_text
    assert "PR #294 已把 `codex/g8-b3-status-refresh` source/test 变更合入 `main`" in " ".join(roadmap_text.split())
    assert "then-current-main one-shot formal verifier" in roadmap_text
    assert (
        "docs/release-evidence/foundation-runtime-concurrency/"
        "ae6b7e52c656fd8296cf039834ce8d8559b01228-frc-g7-b3-20260702/"
        "2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency.json"
        in combined_text
    )
    assert "verified_foundation_runtime_concurrency" in combined_text
    assert "12 concurrent requests/sessions/runs" in combined_text
    assert "`client_case_timestamps`" in combined_text
    assert "7 类 Foundation Runtime checks 全部" in compact_roadmap_text
    assert "Foundation Runtime POC correctness evidence only" in compact_text
    assert "current-main Foundation Runtime concurrency evidence is still missing" not in combined_text
    assert "current-main Foundation Runtime concurrency evidence has not been recorded" not in combined_text
    assert "current-main Foundation Runtime concurrency evidence is missing" not in combined_text
    assert "`SANDBOX_CONTAINER_PROVIDER=docker`" in gate_status_text
    assert "`SANDBOX_EXECUTOR_IMAGE=ai-platform:ae6b7e5-g7-b3-label-repair-v1`" in gate_status_text
    assert "`SANDBOX_EGRESS_POLICY_ENABLED=true`" in combined_text
    assert "`SANDBOX_EXECUTOR_IMAGE=ai-platform:local`" not in gate_status_text
    assert "`SANDBOX_EGRESS_POLICY_ENABLED=false`" not in combined_text
    assert "g7-current-main-label-repair-probe-20260701201919" in combined_text
    assert "2026-07-01-211-g7-sandbox-runtime-hardening-ae6b7e5.json" in combined_text
    assert "g7-live-env-hardening-ae6b7e5-20260702045743" in combined_text
    assert "2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json" in combined_text
    assert "resource-limit cleanup" in compact_gate_status_text
    assert "egress default-deny with scoped callback exception" in compact_gate_status_text
    assert "non-privileged security options" in compact_gate_status_text
    assert "clears the older live executor-image and egress-policy blockers" in compact_gate_status_text
    assert "PR #304 runtime subject `decf33a` also has same-subject reviewed G7/FRC evidence" in compact_gate_status_text
    assert "2026-07-02-211-g7-sandbox-live-env-hardening-decf33a.json" in compact_gate_status_text
    assert "G7 对 `ae6b7e5` 证据集不是 blocked，而是 `candidate_evidence_requires_review`" in compact_roadmap_text
    assert "G7 对捕获时的 `4805031` runtime subject 的 evidence-only 读法是 `candidate_evidence_requires_review`" in compact_roadmap_text
    assert "当时 211 live env 读到 executor image 已是 `ai-platform:4805031-g7-b3-post-297-label-repair-v2`" in compact_roadmap_text
    assert "then-live `4805031` audit 的 G7 读法是 `candidate_evidence_requires_review`" in compact_roadmap_text
    assert "不是 `live_api_sandbox_executor_image_not_current_main_bound`" in compact_roadmap_text
    assert "G7 对 PR #304 runtime subject `decf33a` reviewed evidence + same-subject FRC 的读法也可以到 `candidate_evidence_requires_review`" in compact_roadmap_text
    assert "PR #306 已 merge 且 211 API/worker 已跑到 9c669761" in compact_roadmap_text
    assert "PR #308 已 merge 且 211 API/worker 已跑到 `15903fd` label-clean image" in compact_roadmap_text
    assert (
        "external env-file redacted readback 可支持 G7 live-default posture，"
        "但完整 env/source-authority review 仍是 G0/source-authority / production-hardening 非闭合边界"
        in compact_roadmap_text
    )
    assert (
        "G7/B3 的证据边界现在应拆开读：G7 对历史 `9c669761`、`15903fd` 和 `755e50e` "
        "可到 candidate / local-partial 层级但未获 closure approval"
        in compact_roadmap_text
    )
    assert "当前路线读法已前进到 PR #321 `945db2b` runtime refresh" in compact_roadmap_text
    assert "source marker、source snapshot、API/worker image labels 和三个 API/worker in-container marker 均已绑定 `945db2b`" in compact_roadmap_text
    assert "最新 reviewed G7 live-env verifier 和 B3 capacity visibility 已前进到 `945db2b`" in compact_roadmap_text
    assert "最新 reviewed G7 live-env verifier 和 B3 capacity visibility 仍是历史 `a294727` evidence" not in compact_roadmap_text
    assert "当前 `945db2b` 仍不是 G0/G7 closure" in compact_roadmap_text
    assert "B3 recorded load evidence 和 `b3_10x4_sdk_subagents` profile evidence 仍缺" in compact_roadmap_text
    assert "GitHub issue #164 只作为历史 B0 latest-main runtime-refresh 证据上下文" in compact_roadmap_text
    assert "不得作为当前 G7/B3/#164 closure、gate closable 或 production-ready 证据" in compact_roadmap_text
    assert "当前最新 reviewed capacity visibility verdict 仍是历史 `a294727` 的 `blocked_missing_load_test_evidence`" not in compact_roadmap_text
    assert "总体 B3 closure 仍是 `local partial`" in compact_roadmap_text
    assert "因为七门 recorded load evidence 和 `b3_10x4_sdk_subagents` profile evidence 未齐备" in compact_roadmap_text
    compact_g7_b3_evidence_plan = " ".join(read(G7_B3_EVIDENCE_CLOSURE_PLAN).split())
    assert "reviewed B3 capacity visibility for `945db2b`" in compact_g7_b3_evidence_plan
    assert "approved G7 status-upgrade evidence are missing for `945db2b`" in compact_g7_b3_evidence_plan
    assert (
        "the historical `bbe23d5` operator status-review artifact is recorded "
        "but sets `status_upgrade_decision=not_approved_for_closure`"
        in compact_g7_b3_evidence_plan
    )
    assert "`deployed verifier failed` / `local partial`" not in compact_roadmap_text
    assert "tools/g7_b3_completion_audit.py" in gate_status_text
    assert "the captured `4805031` G7 audit was no longer blocked by executor-image drift" in compact_gate_status_text
    assert "A post-merge read-only 211 poll observed API/worker still running `ai-platform:decf33a-g7-b3-post-300-followup-v1`" in compact_gate_status_text
    assert "supports the historical current-source runtime rollout gap for `a9c78ef` rather than closing it" in compact_gate_status_text
    assert "source-authority" in compact_gate_status_text
    assert "source-authority and B3 load-evidence boundaries are tracked separately" in compact_gate_status_text
    assert "optional reviewed release-evidence entries" in compact_gate_status_text
    assert "later reviewed label-repair, live-env hardening, and FRC evidence" in compact_gate_status_text
    assert "stale `stale_runtime_alias_label_mismatch`, fake-provider, or missing-FRC observations" in compact_gate_status_text
    assert "Audit cleanup note" in roadmap_text
    assert "旧 sanitized runtime observations 必须叠加同一 runtime subject 的 later reviewed label-repair、live-env hardening、Foundation Runtime concurrency evidence 后再判断" in compact_roadmap_text
    assert "旧的 stale alias、fake-provider 或 missing-FRC observations 会被误读成当前 G7 blockers" in compact_roadmap_text
    assert "fail-closed G7/B3 blocker list" in gate_status_text
    assert "not runtime or load evidence by itself" in compact_gate_status_text
    assert "不是 G7 closure approval 或 B3 load evidence" in compact_roadmap_text
    assert "B3 load evidence" in compact_roadmap_text
    for expected in (
        "d318f9f",
        "2026-07-01-211-g7-sandbox-runtime-smoke-ae6b7e5.json",
        "2026-07-01-211-g7-sandbox-runtime-hardening-ae6b7e5.json",
        "2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json",
        "2026-07-01-211-g7-runtime-identity-label-repair-ae6b7e5.json",
        "2026-07-02-211-g7-sandbox-live-env-hardening-4805031.json",
        "2026-07-02-211-g7-sandbox-live-env-hardening-decf33a.json",
        "2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json",
        "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json",
        "2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean.json",
        "2026-07-03-211-g7-operator-status-review-15903fd-label-clean.json",
        "2026-07-03-211-g7-sandbox-live-env-hardening-755e50e.json",
        "g7-live-env-hardening-755e50e-principal-userid-fix-v2-container-20260703115120",
        "2026-07-05-211-g7-sandbox-live-env-hardening-945db2b-live-default.json",
        "2026-07-05-211-capacity-runtime-readiness-945db2b.json",
        "2026-07-05-211-b3-host-sandbox-observation-945db2b.json",
    ):
        assert expected in combined_text
    assert "The earlier post-PR #319 runtime refresh slice is retained as historical evidence" in compact_text
    assert "the a294727 G7 verifier passed all eight checks" in compact_text
    assert "legacy marker files still showed `28676df`" in compact_text
    assert "PR #321 supersedes that legacy marker mismatch for the current runtime subject" in compact_text
    assert "2026-07-04-211-g7-sandbox-live-env-hardening-a294727-source-marker-fix.json" in current_gate_table
    assert "2026-07-04-211-capacity-runtime-readiness-a294727.json" in current_gate_table
    assert "2026-07-04-211-b3-host-sandbox-observation-a294727.json" in current_gate_table
    assert "2026-07-05-211-g7-sandbox-live-env-hardening-945db2b-live-default.json" in current_gate_table
    assert "2026-07-05-211-capacity-runtime-readiness-945db2b.json" in current_gate_table
    assert "2026-07-05-211-b3-host-sandbox-observation-945db2b.json" in current_gate_table
    assert "#321 current `945db2b` slice" in current_gate_table
    assert "Current 211 API/worker images run `ai-platform:945db2b-g7-legacy-source-markers-v1`" in " ".join(current_gate_table.split())
    assert "Reviewed `945db2b` live-env hardening evidence now records all eight verifier checks passing" in " ".join(current_gate_table.split())
    assert "keep B3 blocked until recorded load/profile evidence exists" in current_gate_table
    assert "live API/worker default `SANDBOX_EXECUTOR_IMAGE` still points to `ai-platform:4805031-g7-b3-post-297-label-repair-v2`" not in current_gate_table
    assert "same-subject FRC for `9c669761` is still missing" not in current_gate_table
    assert "no reviewed repo-local current-main G7 release-evidence entry" not in current_gate_table
    assert "still unreviewed `/tmp` evidence" not in current_gate_table
    assert "stale `bd690f7` alias labels remain" not in current_gate_table
    assert "containers retain stale underscore alias labels" not in current_gate_table
    assert "source_synced_runtime_pending_followups_open" in gate_status_text
    assert "the merged source changes are deployed/evidence progress only" in compact_gate_status_text
    assert "foundation_alpha_stage_status=runtime_rollout_required" in gate_status_text
    assert "Fresh local readiness may select those `ae6b7e5` entries plus the `ae6b7e5` Foundation Runtime concurrency entry as reviewed historical evidence" in compact_gate_status_text
    assert "`stage_acceptance_blockers=[]`" in gate_status_text
    assert "because current source and runtime observations are newer than the reviewed `ae6b7e5` evidence subject" in compact_gate_status_text
    assert "source-rollout/dirty-tree gap" in compact_gate_status_text
    assert "Current committed readiness still selects the older `96f27bb` POC evidence pair" not in gate_status_text
    assert "same-subject evidence pairing gap" not in gate_status_text

    assert "G8 is a deferred parking-lot for platform-owned parent/child multi-run orchestration" in compact_text
    assert "G8 Deferred Platform Multi-Run Gate" in combined_text
    assert 'old title "G8 Multi-Agent Controlled Beta"' in combined_text or 'old title was "G8 Multi-Agent Controlled Beta"' in combined_text
    assert "current status must not use that beta title" in compact_text
    assert "must not be read as ordinary-user platform-level multi-run product exposure" in compact_text
    assert "not a current ordinary-user product route" in compact_text
    assert "B3 is the capacity evidence track for selected Claude Agent SDK subagent fanout profiles" in compact_text
    assert "b3_10x4_sdk_subagents" in combined_text
    assert "probe_completed_not_gate_evidence" in combined_text
    assert "bounded B3 sweep covered all seven harness gates" in gate_status_text
    assert "bounded sweep 覆盖七个 harness gates" in roadmap_text
    assert "probe_only_not_recorded" in combined_text
    assert "does_not_mark_gate_recorded = true" in combined_text
    assert "sent_requests = 10" in combined_text
    assert "stop_condition_status = passed" in combined_text
    assert "missing_sections=[]" in combined_text
    assert "blocked_missing_load_test_evidence" in combined_text
    assert "all seven recorded load-test gates and the B3 profile evidence remain missing" in compact_text
    assert "七个 recorded load-test gates 仍缺失" in compact_roadmap_text
    assert "bounded `api_read_write_burst` probe was only" not in gate_status_text
    assert "`api_read_write_burst` probe 只是" not in roadmap_text
    assert "2026-07-02 `ae6b7e5` read-only capacity runtime evidence" in roadmap_text
    assert "Admin Runtime HTTP `200`" in roadmap_text
    assert "required capacity sections present" in roadmap_text
    assert "PR #319 / `a294727` 已作为历史 reviewed G7/capacity evidence 保留" in compact_roadmap_text
    assert "g7-live-env-hardening-a294727-source-marker-fix-20260704170251" in compact_roadmap_text
    assert "all eight checks passing" in compact_roadmap_text
    assert "blocked_missing_admin_runtime_sections" in roadmap_text
    assert "当前最新 reviewed capacity visibility verdict 是 `945db2b` 的 `blocked_missing_load_test_evidence`" in compact_roadmap_text
    assert "总体 B3 closure 仍是 `local partial`" in compact_roadmap_text
    assert "2026-07-02 PR #304 runtime subject `decf33a` 又补了一次 read-only" in roadmap_text
    assert "Admin Runtime no-cleanup overview 返回 HTTP `200`" in compact_roadmap_text
    assert "七个 `missing_load_test_gates` 全部" in roadmap_text
    assert "PR #304 已 merge" in roadmap_text
    assert "current-main `211 verified`" in roadmap_text
    assert "nested gate readiness `blocked_missing_load_test_evidence`" in compact_roadmap_text
    assert "`b3_10x4_sdk_subagents` operator-reviewed profile evidence 仍全部缺失" in compact_roadmap_text
    assert "must not be treated as ordinary-user platform-level multi-run orchestration exposure evidence" in compact_text
    assert "旧 G8 普通用户平台级 multi-run follow-up 不再作为 Foundation Alpha 顶层 `open_followups`" in compact_text
    assert "当前权威状态名不再 使用旧的 `g8_ordinary_user_multi_agent_exposure` / 泛化 multi-agent exposure 命名" in compact_text
    assert "These historical controlled slices do not reopen G8" in roadmap_text
    assert "do not represent ordinary-user platform-level multi-run product exposure" in compact_roadmap_text
    assert "owner-scoped public-safe readiness counts for an explicitly marked historical multi-agent dependency chain" in compact_roadmap_text
    assert "historical, admin-only platform multi-run ledger slice behind controls" in compact_roadmap_text
    assert "ordinary-user platform-level multi-run readiness counts" not in roadmap_text
    assert "first controlled write-side multi-agent runtime ledger slice" not in roadmap_text
    assert "multi-agent fanout exposure" not in roadmap_text
    assert "SDK subagent fanout capacity inside governed platform runs" in roadmap_text
    assert "--host-sandbox-observation-json <host-sandbox-observation.json>" in roadmap_text
    assert "for no-socket default-stack visibility only" in compact_roadmap_text
    assert "g8_ordinary_user_multi_agent_exposure" not in gate_status_text
    assert "旧的 `g8_ordinary_user_multi_agent_exposure` / 泛化 multi-agent exposure 命名" in compact_roadmap_text
    assert (
        "do not report `g8_ordinary_user_multi_agent_exposure` as a B3 blocker or closure field"
        in " ".join(read(TECH_ACCEPTANCE).split())
    )
    assert "current B3 evidence must not emit it" in " ".join(read(TECH_ACCEPTANCE).split())
    assert (
        "Legacy evidence keys such as `g8_ordinary_user_multi_agent_exposure` are retained only as historical negative follow-up names"
        in " ".join(read(RELEASE_EVIDENCE_INDEX).split())
    )
    assert "ordinary_user_multi_agent_exposure" not in gate_status_text
    assert (
        "`ordinary_user_platform_multi_run_orchestration_enabled=false`. The former is a route/status concept"
        in compact_gate_status_text
    )
    assert "--g7-status-upgrade-review-json" in gate_status_text
    assert (
        "an accepted future G7 status-upgrade review can remove only the G7 status-upgrade blocker"
        in compact_gate_status_text
    )
    assert (
        "must still not close B3, mark G7/B3 closure, or make the overall gate closable"
        in compact_gate_status_text
    )
    capacity_text = read(CAPACITY_BASELINE_DOC)
    compact_capacity_text = " ".join(capacity_text.split())
    assert (
        "The current route/status blocked-expansion invariant remains `ordinary_user_platform_multi_run_orchestration_exposure=false`"
        in compact_capacity_text
    )
    assert (
        "the B3 packet boolean is not a substitute for the route/status invariant"
        in compact_capacity_text
    )

    release_evidence_text = read(RELEASE_EVIDENCE_INDEX)
    compact_release_evidence_text = " ".join(release_evidence_text.split())
    assert "The 2026-07-05 post-PR #321 runtime refresh has GitHub `main` including PR #322 documentation merge" in compact_release_evidence_text
    assert "runs API/worker image `ai-platform:945db2b-g7-legacy-source-markers-v1`" in compact_release_evidence_text
    assert "Current status remains `local partial`" in compact_release_evidence_text
    assert "The earlier 2026-07-04 post-PR #316 status-sync baseline has GitHub `main` including PR #316 merge" in compact_release_evidence_text
    assert "At that earlier slice, the reviewed 211 runtime subject was PR #315 merge commit" in compact_release_evidence_text
    assert "latest reviewed 211 runtime subject remains PR #315 merge commit" not in compact_release_evidence_text
    assert "2026-07-04-211-g7-sandbox-live-env-hardening-bbe23d5-post-317.json" in release_evidence_text
    assert "2026-07-04-211-g7-operator-status-review-bbe23d5-post-317.json" in release_evidence_text
    assert "2026-07-04-211-foundation-alpha-poc-bbe23d5-foundation-runtime-concurrency.json" in release_evidence_text
    assert "2026-07-04-211-foundation-alpha-poc-bbe23d5-foundation-runtime-concurrency-readiness.json" in release_evidence_text
    assert "2026-07-04-211-foundation-alpha-poc-bbe23d5-foundation-runtime-concurrency-summary.md" in release_evidence_text
    assert "2026-07-04-211-capacity-runtime-readiness-bbe23d5.json" in release_evidence_text
    assert (
        "reviewed G7 runtime-subject evidence for that earlier clean-main slice "
        "is the `61073b1` live-env hardening entry below"
        in compact_release_evidence_text
    )
    assert "2026-07-03-211-g7-sandbox-live-env-hardening-61073b1-clean-main.json" in release_evidence_text
    assert "2026-07-03-211-g7-operator-status-review-61073b1-clean-main.json" in release_evidence_text
    assert "2026-07-03-211-foundation-alpha-poc-61073b1-foundation-runtime-concurrency.json" in release_evidence_text
    assert "2026-07-03-211-foundation-alpha-poc-61073b1-foundation-runtime-concurrency-readiness.json" in release_evidence_text
    assert "2026-07-03-211-foundation-alpha-poc-61073b1-foundation-runtime-concurrency-summary.md" in release_evidence_text
    assert "2026-07-03-211-capacity-runtime-readiness-61073b1.json" in release_evidence_text
    assert "2026-07-03-211-deployment-image-cleanup-61073b1-clean-main.json" in release_evidence_text
    assert "Same-subject Foundation Runtime concurrency evidence for the clean current-main `61073b1` subject" in compact_release_evidence_text
    assert "does not prove the `b3_10x4_sdk_subagents` capacity profile" in compact_release_evidence_text
    assert "then-current B0 runtime-relevant source subject" in compact_release_evidence_text
    assert "Historical readiness tooling may treat `4039e4b` as that named runtime subject" in compact_release_evidence_text
    assert "this moved the `15903fd` same-subject G7 runtime evidence set to `candidate_evidence_requires_review` at that slice" in compact_release_evidence_text
    assert "this moves the current G7 runtime evidence set" not in release_evidence_text
    assert AE6B7E5_CURRENT_MAIN_SHA in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-runtime-poc-smoke.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-auth-rbac-smoke.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-governance-runtime-smoke.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-release-evidence-runtime-acceptance.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-alert-trace-export-runtime-acceptance.json" in release_evidence_text
    assert "Reviewed `ae6b7e5` Foundation Alpha POC evidence set passed against `ai-platform:ae6b7e5-g7-b3-label-repair-v1`" in compact_release_evidence_text
    assert "same-subject Auth/RBAC, governance runtime smoke, release-evidence runtime acceptance, and alert/trace export runtime acceptance are also reviewed and redaction-passed" in compact_release_evidence_text
    assert "Fresh local readiness may select the same-subject `ae6b7e5` POC/Auth/Governance/Release/Alert evidence set plus `ae6b7e5` Foundation Runtime concurrency evidence as historical reviewed evidence" in compact_release_evidence_text
    assert "same runtime subject still lacks a paired reviewed Auth/RBAC smoke entry" not in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency-readiness.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency-summary.md" in release_evidence_text
    assert "2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json" in release_evidence_text
    assert PR297_G7_B3_SHA in release_evidence_text
    assert PR304_G7_B3_SHA in release_evidence_text
    assert PR305_G7_B3_SHA in release_evidence_text
    assert PR306_G7_B3_SHA in release_evidence_text
    assert "2026-07-03-211-deployment-image-cleanup-15903fd-label-clean.json" in release_evidence_text
    assert "Reviewed 211 deployment-image cleanup evidence for the PR #308 `15903fd` label-clean rollout" in compact_release_evidence_text
    assert "This is deployment hygiene evidence only; it does not approve G7 closure" in compact_release_evidence_text
    assert "does not make `15903fd` `211 verified`, and does not constitute current G7/B3 closure evidence for any current #164/G7/B3 closure claim" in compact_release_evidence_text
    assert "PR #306 merged and 211 API/worker now run `ai-platform:9c66976-g7-b3-workspace-owner-v1`" in compact_release_evidence_text
    assert "Earlier deployed-runtime verifier run `g7-current-main-9c66976-20260702145801` did not execute a task" in compact_release_evidence_text
    assert "2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json" in release_evidence_text
    assert "Reviewed PR #306 explicit verifier-path G7 sandbox hardening artifacts" in compact_release_evidence_text
    assert "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json" in release_evidence_text
    assert "2026-07-03-211-foundation-alpha-poc-9c669761-foundation-runtime-concurrency.json" in release_evidence_text
    assert "Reviewed PR #306 live-default G7 sandbox hardening artifacts" in compact_release_evidence_text
    assert "2026-07-03-211-g7-operator-status-review-9c669761.json" in release_evidence_text
    assert "Reviewed operator status-review artifact for the paired PR #306 `9c669761` live-default G7 sandbox hardening evidence" in compact_release_evidence_text
    assert "`status_upgrade_decision=not_approved_for_closure`" in release_evidence_text
    assert "this G7 evidence can reach `candidate_evidence_requires_review` with `blocking_reasons=[]`" in compact_release_evidence_text
    assert "does not provide B3 recorded load evidence" in compact_release_evidence_text
    assert "2026-07-02-211-capacity-runtime-readiness-28676df.json" in release_evidence_text
    assert "2026-07-03-211-capacity-runtime-readiness-755e50e.json" in release_evidence_text
    assert "Reviewed redacted dirty-runtime v2 capacity visibility for `755e50e`" in compact_release_evidence_text
    assert "Reviewed redacted clean current-main capacity visibility for `61073b1`" in compact_release_evidence_text
    assert "This is B3 visibility and fail-closed readiness evidence only; it does not close B3" in release_evidence_text
    assert "diagnostic only, not a reviewed release-evidence entry" in release_evidence_text
    assert "Patched-source diagnostic run `g7-current-main-28676df-workspace-user-fix-20260702135351` passed all eight verifier checks" in compact_release_evidence_text
    assert "Reviewed redacted PR #305 merge-commit capacity visibility for `28676df`" in compact_release_evidence_text
    assert "blocked_missing_admin_runtime_sections" in release_evidence_text
    assert "because `sandbox` was treated as missing" in release_evidence_text
    assert "2026-07-02-211-capacity-runtime-readiness-decf33a.json" in release_evidence_text
    assert "Reviewed redacted PR #304 runtime-subject capacity visibility for `decf33a`" in release_evidence_text
    assert "The readiness status remains `blocked_missing_load_test_evidence`" in release_evidence_text
    assert "This is B3 visibility and fail-closed readiness evidence only" in release_evidence_text
    assert "2026-07-02-211-g7-sandbox-live-env-hardening-decf33a.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-decf33a-foundation-runtime-concurrency.json" in release_evidence_text
    assert "Together with the same-subject `decf33a` Foundation Runtime concurrency entry below" in release_evidence_text
    assert "PR #304 later merged at `a9c78efa812efe96b0366011a0c731cb11eb0099`, but this does not close G7" in release_evidence_text
    assert "does not constitute current G7/B3 closure evidence for any current #164/G7/B3 closure claim" in release_evidence_text
    assert "2026-07-02-211-g7-sandbox-live-env-hardening-4805031.json" in release_evidence_text
    assert "2026-07-02-211-foundation-alpha-poc-4805031-foundation-runtime-concurrency.json" in release_evidence_text
    assert "Together with the same-subject `4805031` Foundation Runtime concurrency entry below" in release_evidence_text
    assert "this evidence-only audit can reach `candidate_evidence_requires_review` with `blocking_reasons=[]`" in release_evidence_text
    assert "current-live `4805031` G7 audit is no longer blocked by `live_api_sandbox_executor_image_not_current_main_bound`" in release_evidence_text
    assert "clears the earlier `ae6b7e5` live executor-image and egress-policy blockers" in release_evidence_text
    assert "This is Foundation Runtime POC correctness evidence only" in release_evidence_text
    assert "current-main Foundation Runtime concurrency evidence is not recorded" not in release_evidence_text

    assert "#164 history is historical B0 runtime-refresh context only" in compact_release_evidence_text
    assert "do not use it as current G7/B3, Foundation Alpha, production-ready, gate-closable, or closure evidence" in compact_release_evidence_text
    assert "do not call the current source `211 verified`" in compact_text
    assert "`gate closable`, B3 complete, G7 complete, Foundation Alpha complete, or production-ready" in compact_text
    assert "already captured 211 current-main verifier artifacts" not in read(G7_B3_EVIDENCE_CLOSURE_PLAN)
    compact_tech_text = " ".join(read(TECH_ACCEPTANCE).split())
    assert "`local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, and `gate closable`" in compact_tech_text


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
        assert "ai-platform.release-evidence-runtime-acceptance.v1" in text
        assert "alert_trace_export_runtime_acceptance" in text
        assert "g9_runtime_export_and_retention_acceptance" in text
        assert "alert_delivery_and_trace_export_211_acceptance" in text
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
        assert "--write-evidence-scaffold" in text
        assert "--evidence-root docs/release-evidence/skill-release" in text
        assert "ai-platform.skill-release-evidence-scaffold.v1" in text
        assert "external-release-evidence/<skill-id>/..." in text
        assert "tools/skill_release_dashboard_readiness.py" in text
        assert "ai-platform.skill-release-readiness.v1" in text
        assert "ai-platform.skill-release-dashboard-readiness.v1" in text
        assert "ai-platform.skill-release-dashboard-contract.v1" in text
        assert "ai-platform.skill-dependency-review-policy.v1" in text
        assert "ai-platform.skill-signed-package-evidence-contract.v1" in text
        assert "ai-platform.skill-dependency-review-runtime-acceptance.v1" in text
        assert "skill_signed_package_evidence_contract" in text
        assert "ai-platform.skill-release-review.v1" in text
        assert "sbom_reviewed" in text
        assert "license_policy_reviewed" in text
        assert "vulnerability_reviewed" in text
        assert "dependency_vulnerability_or_license_policy" in text
        assert "skill_dependency_review_policy_runtime_acceptance" in text
        assert "tools/verify_governance_runtime_smoke.py" in text
        assert "docs/release-evidence/skill-release-runtime" in text
        assert "admin_skill_release_dashboard_contract" in text
        assert "admin_skill_release_dashboard_runtime_acceptance" in text
        assert "admin_skill_release_dashboard_visual_acceptance" in text
        assert "admin_skill_release_dashboard_211_acceptance" in text
        assert "admin_skill_release_dashboard_acceptance" not in text
        assert "tools/tool_policy_bulk_review_readiness.py" in text
        assert "ai-platform.tool-policy-bulk-review-readiness.v1" in text
        assert "ai-platform.tool-policy-bulk-review-dashboard-contract.v1" in text
        assert "admin_policy_bulk_review_dashboard_contract" in text
        assert "admin_policy_bulk_review_runtime_acceptance" in text
        assert "admin_policy_bulk_review_visual_acceptance" in text
        assert "admin_policy_bulk_review_211_acceptance" in text
        assert "admin_policy_bulk_review_and_dashboard_acceptance" not in text
        assert "does not close G6" in text
        assert "executor_private_payload" not in text
        assert "raw_storage_key" not in text
        assert "sandbox_workdir" not in text
        assert "C:\\Users" not in text


def test_office_context_docs_track_source_level_context_pack_versioning_without_gate_closure():
    governance_text = read(GOVERNANCE_READINESS_DOC)
    roadmap_text = read(ROADMAP)
    gate_status_text = read(GATE_STATUS_DOC)

    for text in (governance_text, roadmap_text, gate_status_text):
        assert "source-level context-pack persistence/versioning" in text
        assert "source_level_context_pack_persistence_and_versioning" in text
        assert "context_pack_version" in text
        assert "context_pack_generated_at" in text
        assert "PR #44" in text
        assert "211 executor context-pack" in text
        assert "frontend run-playback context provenance" in text
        assert "long-term cross-session memory" in text
        assert "ordinary-user platform-level multi-run orchestration exposure" in text
        assert "C:\\Users" not in text

    for text in (governance_text, gate_status_text):
        assert "Context-pack persistence/versioning, 211 executor" not in text
        assert "context-pack persistence/executor injection/frontend provenance acceptance" not in text


def test_frontend_docs_record_packaged_runtime_smoke_contract_and_211_blocker():
    frontend_text = read(FRONTEND_MIGRATION_DOC)
    roadmap_text = read(ROADMAP)
    governance_text = read(GOVERNANCE_READINESS_DOC)
    gate_status_text = read(GATE_STATUS_DOC)

    for text in (frontend_text, roadmap_text):
        assert "tools/frontend_packaged_runtime_smoke.py" in text
        assert "ai-platform.frontend-packaged-runtime-smoke.v1" in text
        assert "ai-platform.frontend-packaged-runtime-smoke-evidence.v1" in text
        assert "frontend_release.packaged_runtime_smoke.<commit_sha>" in text
        assert "305bc40" in text
        assert "83a500e" in text
        assert "6088d5d" in text
        assert "docker_registry_proxy_unreachable" in text
        assert "base_image_pull_failed" in text
        assert "Docker daemon" in text
        assert "node:22-alpine" in text
        assert "nginx:1.27-alpine" in text
        assert "not release acceptance" in text
        assert "C:\\Users" not in text

    for text in (governance_text, gate_status_text, roadmap_text):
        assert "packaged_runtime_smoke_contract" in text
        assert "ai-platform.frontend-packaged-runtime-smoke.v1" in text
        assert "frontend_packaged_runtime_smoke_evidence_missing" in text
        assert "docker_capable_host_only_no_local_windows_docker" in text
        assert "frontend_packaged_image_delivery_and_release_acceptance" in text
        assert "does not close" in text or "does not by itself close" in text


def test_frontend_prd_closure_matrix_records_current_211_boundary_without_overclosing_parent():
    matrix_text = read(FRONTEND_PRD_CLOSURE_MATRIX)
    compact_text = " ".join(matrix_text.split())

    for expected in (
        "Single active closure PR",
        "Refs #81",
        "PR #267",
        "matrix necessarily changes the head SHA after the file is written",
        "`PR ready` after checks; `211 verified` only when live provenance",
        "not `reviewed`, not `merged`, not `gate closable` while open",
        "GitHub `reviewDecision` empty at the latest check",
        "projection audit, lint, build, trace",
        "packaged image build",
        "Must be checked live against the current PR head before claiming `211 verified`",
        "Latest PR #267 211 deploy evidence comment; it must use `Refs #81` only",
        "PR #264",
        "94f0b20fcf441fdcbde730a1edafb2c1dbdcbf59",
        "Prior merged evidence remains PR #264",
        "company-account browser login",
        "ordinary workflow",
        "admin workflow",
        "Right context panel",
        "shareChannelFailClosedSource.test.ts",
        "governancePhase1Closure.test.ts",
        "frontendPhase1ClosureContract.test.ts",
        "Phase 2 backend-backed expansion is not a frontend-only closure item.",
    ):
        assert expected in matrix_text

    for boundary in (
        "Status boundary: this is not a full-program `gate closable` claim.",
        "Formal GitHub review metadata is still absent",
        "Codex usage-limit blocker instead of a review",
        "must not use `Closes #81`",
        "Credentials are read only from gitignored environment files",
        "Evidence and comments must record only the source variable names and `redacted` placeholders",
        "does not support `reviewed`, `merged`, or a full-program `gate closable` claim",
        "not a full-program `gate closable` issue until the active PR is reviewed and merged",
        "share ACL unavailable/denied/revoked/expired states",
        "governed channel import unavailable state",
        "fail-closed group availability toggles",
        "MCP lifecycle governance without raw server controls",
    ):
        assert boundary in compact_text

    for remaining_backend_scope in (
        "department/group Skill marketplace policy writes",
        "MCP lifecycle and policy assignment",
        "session-share ACL creation and lifecycle",
        "users/roles/departments, model admin, settings, and notifications",
    ):
        assert remaining_backend_scope in compact_text

    assert "AI_PLATFORM_LOGIN_PASSWORD=" not in matrix_text
    assert "password:" not in matrix_text
    assert "C:\\Users" not in matrix_text
    assert "\nCloses #81" not in matrix_text
    assert "merged-main 211 verified" not in matrix_text


def test_prd_records_claude_sdk_execution_boundary_without_second_runtime():
    prd_text = read(PRD)
    tech_text = read(TECH_ACCEPTANCE)
    roadmap_text = read(ROADMAP)
    guardrails_text = read(GUARDRAILS)
    multi_agent_workflow_text = read(MULTI_AGENT_CONTEXT_WORKFLOW)
    compact_prd_text = " ".join(prd_text.split())
    compact_roadmap_text = " ".join(roadmap_text.split())
    compact_guardrails_text = " ".join(guardrails_text.split())
    compact_multi_agent_workflow_text = " ".join(multi_agent_workflow_text.split())

    assert "Claude Agent SDK is the current primary execution kernel" in prd_text
    assert "Historical references not listed above are intentionally omitted" in prd_text
    assert "Current PRD scope treats them as execution-kernel behavior inside one governed" in compact_prd_text
    assert "not part of the current PRD core field set" in prd_text
    assert "Executor-private logs" in prd_text
    assert "private artifact metadata are never the platform source" in prd_text
    assert "truth" in prd_text
    assert "A second independent runtime/control plane" in prd_text
    assert "Platform-owned parent/child multi-run orchestration as a current requirement" in prd_text
    assert "Platform-owned multi-run multi-agent runtime as a current requirement" not in prd_text
    assert "create a separate platform multi-run scheduler" in prd_text
    assert "SDK-internal agents or subagents are not platform-visible multi-run" in prd_text
    assert "new-api" not in prd_text
    assert "AgentScope" not in prd_text
    assert "DeerFlow" not in prd_text
    assert "G8 Deferred Platform Multi-Run Gate" in prd_text
    assert 'old title "G8 Multi-Agent Controlled Beta"' in prd_text
    assert "Current Claude Agent SDK Agent/subagent fanout capability" in prd_text
    assert "Current execution-layer multi-agent/subagent capability" not in prd_text
    assert "G10 Internal Beta / Department Rollout" in prd_text
    assert "Advanced Claude Agent SDK Task Patterns" in tech_text
    assert "Historical references not listed above are intentionally omitted" in tech_text
    assert (
        "SDK-private subagents are not automatically platform-visible multi-run "
        "or parent/child orchestration"
    ) in tech_text
    assert "new-api" not in tech_text
    assert "AgentScope" not in tech_text
    assert "DeerFlow" not in tech_text
    assert "platform multi-run / SDK subagent expansion" in compact_guardrails_text
    assert "shared contracts, multi-agent runtime" not in compact_guardrails_text
    assert "assistant's working process" in multi_agent_workflow_text
    assert "deferred G8 platform-level multi-run orchestration route" in compact_multi_agent_workflow_text
    assert "B3 SDK subagent fanout capacity evidence" in compact_multi_agent_workflow_text
    assert (
        "Assistant sub-agents in this workflow do not prove, open, or close "
        "ordinary-user platform-level multi-run product exposure"
    ) in compact_multi_agent_workflow_text
    assert "product-level multi-agent runtime" not in multi_agent_workflow_text

    assert "Long Task Product Contract / Office Artifact Flow" in roadmap_text
    assert "Long Task / Platform Multi-Run Orchestration / SDK Subagent Patterns" in roadmap_text
    assert "Long Task / Multi-Agent Runtime" not in roadmap_text
    assert "later platform multi-run orchestration gate" in roadmap_text
    assert "later multi-agent runtime gate" not in roadmap_text
    assert "historical platform multi-run dispatcher events" in roadmap_text
    assert "deployed multi-agent runtime events" not in roadmap_text
    assert "execution kernel for skills, tools, artifacts, token/cost accounting, and agent/subagent execution" in compact_roadmap_text
    assert "执行层路线是 Claude Agent SDK" in roadmap_text
    assert "C:\\Users" not in prd_text
    assert "C:\\Users" not in tech_text
    assert "C:\\Users" not in roadmap_text


def test_skills_marketplace_public_api_documents_backed_file_overlay_contract():
    contract = read(SKILLS_MARKETPLACE_PUBLIC_API)

    assert "PUT `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped UTF-8 text file overlay" in contract
    assert "Binary/base64 asset overlays remain out of scope" in contract
    assert "DELETE `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped tombstone" in contract
    assert "Marketplace file previews continue to read released Skill snapshots" in contract
    assert "skill_file_write_contract_not_backed" not in contract
    assert "skill_file_delete_contract_not_backed" not in contract
    assert "durable per-user skill file storage" not in contract
