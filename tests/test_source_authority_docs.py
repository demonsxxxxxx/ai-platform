from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
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
README = ROOT / "README.md"
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


def test_guardrails_use_current_authority_sources_only():
    guardrails_text = read(GUARDRAILS)

    assert "Current user instruction in the active session" in guardrails_text
    assert "Current code, tests, and fresh 211 runtime evidence" in guardrails_text
    assert "docs/superpowers/" not in guardrails_text


def test_release_authority_and_runbook_keep_debian_mirrors_in_the_backend_build_boundary():
    dockerfile_text = read(BACKEND_DOCKERFILE)
    release_authority_text = read(ROOT / "tools/release_authority.py")
    runbook_text = read(ROOT / "docs/operations/211-release-operations-runbook.md")

    assert "ARG APT_MIRROR" in dockerfile_text
    assert "ARG APT_SECURITY_MIRROR" in dockerfile_text
    assert dockerfile_text.count("FROM python:3.11-slim-bookworm") == 2
    assert "http://deb.debian.org/debian-security" in dockerfile_text
    assert "https://deb.debian.org/debian-security" in dockerfile_text
    assert "--apt-mirror" in release_authority_text
    assert "--apt-security-mirror" in release_authority_text
    assert "probe-apt-mirrors" in release_authority_text
    assert "Range" in release_authority_text
    assert "complete clear-signed PGP envelope" in runbook_text
    assert "Codename" in runbook_text
    assert "oldoldstable" in runbook_text and "`-security` suffix" in runbook_text
    assert '"requested"' in release_authority_text and '"applied"' in release_authority_text
    assert "mirrors.ustc.edu.cn/debian" in runbook_text
    assert "mirrors.ustc.edu.cn/debian-security" in runbook_text
    assert "probe-apt-mirrors" in runbook_text
    assert "curl --fail --silent --show-error --head" not in runbook_text
    assert "registry-mirrors" not in dockerfile_text
    assert "PIP_TRUSTED_HOST" not in release_authority_text
    assert "trusted=yes" not in dockerfile_text
    assert "allow-unauthenticated" not in dockerfile_text


def test_agent_rules_keep_main_session_authority_separate_from_subagents():
    agents_text = read(AGENTS)
    workflow_text = read(MULTI_AGENT_CONTEXT_WORKFLOW)
    compact_agents_text = " ".join(agents_text.split())
    compact_workflow_text = " ".join(workflow_text.split())

    assert "single source for task lifetimes, ownership, authority" in compact_agents_text
    assert (
        "User authorization for one task or main session does not automatically grant"
        in compact_workflow_text
    )
    assert "A task may mutate only subjects explicitly covered by its dispatch" in compact_workflow_text
    assert "Direct controller mutation is break-glass only" in compact_workflow_text
    assert "broad standing authorization is insufficient" in compact_workflow_text


def test_github_workflow_records_sdk_worker_diagnostic_layers():
    workflow_text = read(GITHUB_WORKFLOW)
    compact_workflow_text = " ".join(workflow_text.split())

    for expected in (
        "SDK, worker, skill, terminal, or user-facing runtime diagnostics",
        "tool registration -> runner selection -> subprocess/terminal -> SDK event -> user-facing error",
        "minimal reproduction",
        "observable log/event evidence",
        "Historical examples are non-normative",
        "docs/agent-rules/history/github-sdk-diagnostic-examples.md",
    ):
        assert expected in compact_workflow_text


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


def test_default_compose_uses_current_repo_context_and_no_docker_socket():
    compose_text = read(COMPOSE)
    assert "context: ../.." not in compose_text
    assert "container_name: ai-platform-frontend" in compose_text
    assert "${AI_PLATFORM_FRONTEND_IMAGE:?set AI_PLATFORM_FRONTEND_IMAGE}" in compose_text
    assert "${AI_PLATFORM_SOURCE_COMMIT:?set AI_PLATFORM_SOURCE_COMMIT}" in compose_text
    assert "${AI_PLATFORM_FRONTEND_PORT:-18001}:8080" in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text


def test_nonroot_runtime_source_contract_does_not_claim_runtime_acceptance():
    dockerfile = read(BACKEND_DOCKERFILE)
    compose_text = read(COMPOSE)
    phase_text = read(ROOT / "docs/operations/2026-07-11-s1b-nonroot-runtime-identity.md")

    assert "USER 10001:10001" in dockerfile
    assert 'user: "10001:10001"' in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text
    assert "Status: `source design approved`" in phase_text or "Status: `local partial`" in phase_text
    assert "does not claim S1B, B2, G7, 211, deployment" in phase_text
    assert "211 verified" not in phase_text


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
    assert compose["services"]["api"]["image"] == "${AI_PLATFORM_IMAGE:?set AI_PLATFORM_IMAGE}"
    assert compose["services"]["worker"]["image"] == "${AI_PLATFORM_IMAGE:?set AI_PLATFORM_IMAGE}"
    assert compose["services"]["api"]["labels"]["ai-platform.source-dirty"] == "false"
    assert compose["services"]["worker"]["labels"]["ai-platform.source-dirty"] == "false"
    assert "AI_PLATFORM_SOURCE_COMMIT=" in env_text
    assert "AI_PLATFORM_BUILD_COMMIT=" in env_text
    assert "AI_PLATFORM_BUILD_DIRTY=false" in env_text


def test_env_template_satisfies_required_runtime_defaults_without_real_secrets():
    env_text = read(ENV_EXAMPLE)
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" in env_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.25:7263" in env_text
    assert "EXISTING_USER_INFO_BASE_URL=http://10.56.0.25:5166" in env_text
    assert "PUBLIC_SKILL_FILE_OVERLAY_MAX_BYTES=262144" in env_text
    assert "AI_PLATFORM_FRONTEND_PORT=18001" in env_text
    assert "AI_PLATFORM_FRONTEND_IMAGE=" in env_text
    assert "AI_PLATFORM_API_UPSTREAM=http://api:8020" in env_text
    assert "WORKER_CLAUDE_AGENT_SDK_ENABLED=false" in env_text
    assert "CLAUDE_AGENT_SDK_ENABLED=false" not in set(env_text.splitlines())
    assert "CLAUDE_AGENT_SDK_MAX_TURNS=128" in env_text
    assert "CLAUDE_AGENT_SDK_EFFORT=xhigh" in env_text
    assert "CLAUDE_AGENT_SDK_MAX_THINKING_TOKENS=16384" in env_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.211" not in env_text
    assert "sk-" not in env_text
    assert "Bearer " not in env_text


def test_readme_documents_worker_only_claude_agent_sdk_switch():
    readme_text = read(README)

    assert "WORKER_CLAUDE_AGENT_SDK_ENABLED=true" in readme_text
    assert "`CLAUDE_AGENT_SDK_ENABLED=true`" not in readme_text


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


def test_compose_keeps_claude_agent_sdk_execution_switch_worker_only():
    compose = yaml.safe_load(read(COMPOSE))
    api_env = compose["services"]["api"]["environment"]
    worker_env = compose["services"]["worker"]["environment"]

    assert "CLAUDE_AGENT_SDK_ENABLED" not in api_env
    assert worker_env["CLAUDE_AGENT_SDK_ENABLED"] == "${WORKER_CLAUDE_AGENT_SDK_ENABLED:-false}"


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
    runbook_text = read(ROOT / "docs/operations/211-release-operations-runbook.md")
    generator_text = read(ROOT / "scripts/generate_sandbox_runtime_evidence_211.py")

    assert "docs/operations/211-release-operations-runbook.md" in agents_text
    assert "python3" in runbook_text
    assert '--docker-cmd "sudo -n docker"' in runbook_text
    assert "--cancel-image ai-platform:local" in runbook_text
    assert "current or backup" in runbook_text
    assert "recreate with `--no-build`" in runbook_text
    assert "max depth exceeded" in runbook_text
    assert "chmod +x /app/docker-entrypoint.sh" in runbook_text
    assert '"ai-platform:local"' in generator_text
    assert "--runtime-mode" in generator_text
    assert "platform" in generator_text
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
    assert "The prior `945db2b` entry remains fail-closed at `blocked_missing_load_test_evidence`" in " ".join(gate_status_text.split())
    assert "the earlier reviewed `a294727`, `bbe23d5`, and `61073b1` visibility records are retained as prior baselines" in gate_status_text
    assert "`blocked_missing_admin_runtime_sections`" in gate_status_text
    assert "`sandbox` was missing/degraded" in gate_status_text
    assert "the earlier reviewed `a294727`, `bbe23d5`, and `61073b1` visibility records are retained as prior baselines" in gate_status_text
    assert HISTORICAL_DIRTY_G7_B3_RUNTIME_SHA in capacity_text
    assert "so `755e50e` is not latest clean `origin/main` runtime evidence" in compact_capacity_text
    assert "The latest reviewed capacity visibility entry is now the `945db2b` record" in compact_capacity_text
    assert "Even when a deployment profile sets `SANDBOX_CONTAINER_PROVIDER=docker`" in compact_capacity_text
    assert "capacity baseline remains fail-closed until clean-main B3 recorded load/profile evidence is reviewed" in compact_capacity_text
    assert "approved G7 status-upgrade evidence is present" in compact_capacity_text
    assert "ai-platform:4805031-g7-b3-post-297-label-repair-v2" in capacity_text
    assert "ai-platform-frontend:ba81a0b" in capacity_text
    assert "/tmp/ai-platform-b3-39aa862-recorded-live-20260705T074525Z" in capacity_text
    assert "`status=operator_value_files_ready`" in capacity_text
    assert "`status=recorded_gate_batch_input_accepted`" in capacity_text
    assert "`readiness.status=ready_for_operator_review`" in capacity_text
    assert "Admin Runtime HTTP `200`" in gate_status_text
    assert "all required Admin Runtime capacity sections present" not in gate_status_text
    assert "all required Admin Runtime sections observed" in gate_status_text
    assert "schema `ai-platform.capacity-runtime-evidence.v1`" in gate_status_text
    assert "nested gate readiness `blocked_missing_load_test_evidence`" in gate_status_text
    assert "accepted `b3_10x4_sdk_subagents` profile packet" in gate_status_text
    assert "10 terminal `succeeded` runs" in gate_status_text
    assert "40 total Agent calls" in gate_status_text
    assert "capacity-recorded-gate-batch-snapshot-from-live-run.json" in gate_status_text
    assert "`status=recorded_gate_batch_input_accepted`" in gate_status_text
    assert "`readiness.status=ready_for_operator_review`" in gate_status_text
    assert "The latest reviewed `a294727` read-only capacity runtime evidence records Admin Runtime HTTP `200`" not in gate_status_text
    assert "The clean `53887e2` branch-runtime recorded evidence is now repo-local and reviewed" in gate_status_text
    assert "recorded B3 evidence remains operator-review-required, not closure" in gate_status_text
    assert "Reproduce the same recorded-batch path on a clean committed image" not in gate_status_text
    assert "source contract only plus dirty validation evidence" not in gate_status_text


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


def test_skills_marketplace_public_api_documents_backed_file_overlay_contract():
    contract = read(SKILLS_MARKETPLACE_PUBLIC_API)

    assert "PUT `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped UTF-8 text file overlay" in contract
    assert "Binary/base64 asset overlays remain out of scope" in contract
    assert "DELETE `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped tombstone" in contract
    assert "Marketplace file previews continue to read released Skill snapshots" in contract
    assert "skill_file_write_contract_not_backed" not in contract
    assert "skill_file_delete_contract_not_backed" not in contract
    assert "durable per-user skill file storage" not in contract


def test_multi_agent_workflow_keeps_task_authority_and_release_ownership_separate():
    workflow = read(MULTI_AGENT_CONTEXT_WORKFLOW)
    compact_workflow = " ".join(workflow.split())

    assert "### Disposable probes" in workflow
    assert "one-shot, read-only context-isolation task" in compact_workflow
    assert "### Persistent tasks" in workflow
    assert "Exactly one persistent writer holds a given write scope" in compact_workflow
    assert "## Release Lifecycle" in workflow
    assert "exactly one project-bound persistent release task and one mutation lease" in compact_workflow


def test_governance_docs_remove_stale_rules_without_weakening_release_invariants():
    agents = read(AGENTS)
    guardrails = read(GUARDRAILS)
    github_workflow = read(GITHUB_WORKFLOW)
    compact_github_workflow = " ".join(github_workflow.split())
    multi_agent_workflow = read(MULTI_AGENT_CONTEXT_WORKFLOW)
    compact_multi_agent_workflow = " ".join(multi_agent_workflow.split())
    runbook = read(ROOT / "docs/operations/211-release-operations-runbook.md")
    history = read(
        ROOT / "docs/agent-rules/history/github-sdk-diagnostic-examples.md"
    )

    assert "Frontend source is maintained in `frontend/web`" in guardrails
    assert "Frontend source is maintained in `frontend/web`" not in agents
    assert "Move frontend source into this repository" not in agents
    assert "Frontend source should move into this repository" not in guardrails
    assert "#15/#16/#17" not in agents
    assert "#15/#16/#17" not in guardrails

    assert "actually observed on the PR count as CI gates" in compact_github_workflow
    assert (
        "Acceptance-blocking findings cannot be deferred to claim readiness or closure, "
        "and any unresolved Critical or Important finding prevents `reviewed`."
        in compact_github_workflow
    )
    assert "Until backend CI/CD is configured" not in github_workflow
    assert "Historical examples are non-normative" in github_workflow
    for pr in ("PR #165", "PR #168", "PR #169"):
        assert pr not in github_workflow
        assert pr in history

    assert "preferred implementation of the release" in guardrails
    assert "not an independent product-acceptance gate" in guardrails
    assert "persistent ownership, mutation leases, and break-glass authority" in guardrails
    assert "Never release from a local source archive" in guardrails
    assert "project-bound persistent release task" in compact_multi_agent_workflow
    assert "one mutation lease" in compact_multi_agent_workflow
    assert "final source/runtime parity" in runbook

    assert "### Persistent tasks" in multi_agent_workflow
    assert "### Disposable probes" in multi_agent_workflow
    assert "No tool output found" in multi_agent_workflow
    assert "orphan-call protocol error" in compact_multi_agent_workflow
    assert "do not guess a result or replay" in compact_multi_agent_workflow


def test_governance_docs_keep_cross_cutting_rules_in_one_authoritative_file():
    docs = {
        "agents": read(AGENTS),
        "guardrails": read(GUARDRAILS),
        "github": read(GITHUB_WORKFLOW),
        "workflow": read(MULTI_AGENT_CONTEXT_WORKFLOW),
        "runbook": read(ROOT / "docs/operations/211-release-operations-runbook.md"),
    }

    unique_contracts = {
        "workflow": (
            "one-shot, read-only context-isolation task",
        ),
        "guardrails": (
            TARGET_211_BACKEND,
            "Frontend source is maintained in `frontend/web`",
        ),
        "github": (
            "`local partial`",
            "`gate closable`",
        ),
        "runbook": (
            '--docker-cmd "sudo -n docker"',
            "max depth exceeded",
        ),
    }

    for owner, phrases in unique_contracts.items():
        for phrase in phrases:
            assert phrase in docs[owner]
            for other_name, other_text in docs.items():
                if other_name != owner:
                    assert phrase not in other_text
