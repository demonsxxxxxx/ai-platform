from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md"
OLD_PRD = ROOT / "docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md"
TECH_ACCEPTANCE = ROOT / "docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md"
ROADMAP = ROOT / "docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md"
GUARDRAILS = ROOT / "docs/agent-rules/ai-platform-guardrails.md"
GITHUB_WORKFLOW = ROOT / "docs/agent-rules/github-issue-pr-workflow.md"
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
GATE_STATUS_DOC = ROOT / "docs/operations/ai-platform-gate-status.md"
RELEASE_EVIDENCE_INDEX = ROOT / "docs/release-evidence/README.md"
SOURCE_RUNTIME_RELATION_MANIFEST = (
    ROOT / "docs/release-evidence/foundation-alpha-poc/source-runtime-relation-manifest.json"
)
ACTIVE_RUNTIME_SUBJECT_SHA = "ac9a86bbea14a28748867cade8d80b2f9ff420ec"
ACTIVE_RUNTIME_IMAGE = "ai-platform:ac9a86b-s1-merged"
ACTIVE_RUNTIME_IMAGE_ID = "sha256:1d13f440107c7bd39184b8c640c6e9a9c7e9bc0755d7e20e145cd8cafbccb7ee"
ACTIVE_POC_SMOKE_EVIDENCE_ID = "2026-06-13-211-foundation-alpha-poc-ac9a86b-runtime-poc-smoke"
ACTIVE_AUTH_RBAC_EVIDENCE_ID = "2026-06-13-211-foundation-alpha-poc-ac9a86b-auth-rbac-smoke"
ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_ID = (
    "2026-06-13-211-foundation-alpha-poc-ac9a86b-governance-runtime-smoke"
)
ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_ID = (
    "2026-06-13-211-foundation-alpha-poc-ac9a86b-release-evidence-runtime-acceptance"
)
ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_ID = (
    "2026-06-13-211-foundation-alpha-poc-ac9a86b-alert-trace-export-runtime-acceptance"
)
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
    / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_POC_SMOKE_EVIDENCE_ID}.json"
)
FOUNDATION_ALPHA_POC_ACTIVE_AUTH_RBAC_EVIDENCE = (
    ROOT
    / f"docs/release-evidence/foundation-alpha-poc/{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_AUTH_RBAC_EVIDENCE_ID}.json"
)
FOUNDATION_ALPHA_POC_ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE = (
    ROOT
    / (
        "docs/release-evidence/foundation-alpha-poc/"
        f"{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_ID}.json"
    )
)
FOUNDATION_ALPHA_POC_ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE = (
    ROOT
    / (
        "docs/release-evidence/foundation-alpha-poc/"
        f"{ACTIVE_RUNTIME_SUBJECT_SHA}/{ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_ID}.json"
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


def test_active_prd_v2_records_appendix_and_closure_workflow_authority():
    prd_text = read(PRD)
    tech_text = read(TECH_ACCEPTANCE)
    workflow_text = read(GITHUB_WORKFLOW)

    assert "Status: active product PRD" in prd_text
    assert "Status: active companion acceptance document" in tech_text
    assert "2026-05-29 PRD remains a migration appendix" in prd_text
    assert "This PRD v2 is the active product source" in prd_text
    assert "tools/foundation_alpha_readiness.py --format json" in prd_text
    assert "tools/foundation_alpha_readiness.py --format json" in tech_text
    assert "docs/agent-rules/github-issue-pr-workflow.md" in prd_text
    assert "issue -> PR -> review -> merge -> deploy/smoke when required -> close issue with evidence" in prd_text
    assert "Use `Closes #N` or `Fixes #N` only when" in workflow_text
    assert OLD_PRD.exists()


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
        "G8 Multi-Agent Controlled Beta",
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


def test_gate_status_snapshot_records_blockers_without_closure_claim():
    gate_status_text = read(GATE_STATUS_DOC)
    release_evidence_text = read(RELEASE_EVIDENCE_INDEX)

    assert "not automatic" in gate_status_text
    assert "gate-closure evidence" in gate_status_text
    assert "issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue" in gate_status_text
    assert "#17 frontend source migration" in gate_status_text
    assert "#21 capacity baseline" in gate_status_text
    assert "Foundation Runtime concurrency evidence" in gate_status_text
    assert "foundation_runtime_concurrency_evidence" in gate_status_text
    assert "10+ concurrent" in gate_status_text
    assert "does not raise production concurrency defaults" in gate_status_text
    assert "#21 is currently closed in GitHub" in gate_status_text
    assert "#21 remains open" not in gate_status_text
    assert "do_not_raise_without_recorded_load_test_evidence" in gate_status_text
    assert "packaged frontend image smoke/release acceptance" in gate_status_text
    assert "Foundation Alpha POC Smoke" in gate_status_text
    assert "211 POC smoke refreshed for the merged #34-#39 S1 runtime subject" in gate_status_text
    assert "not production gate closure" in gate_status_text
    assert "current context public-summary" not in gate_status_text[:1000]
    assert "source_synced_runtime_pending" in gate_status_text
    assert "committed source-runtime" in gate_status_text
    assert "relation manifest" in gate_status_text
    assert "runtime_source_relation" in release_evidence_text
    assert "source-runtime-relation-manifest.json" in release_evidence_text
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
    assert "/home/xinlin.jiang/" not in gate_status_text


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
    assert payload["source_tree_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["runtime_affecting_changes_since_runtime_subject"] == []
    assert "C:\\Users" not in json.dumps(payload)
    assert "/home/xinlin.jiang/" not in json.dumps(payload)


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
    assert payload["evidence_ref"]["runtime_checks"]["same_origin_api_health"]["payload_status"] == "ok"
    assert payload["evidence_ref"]["runtime_checks"]["lambchat_api_compat"]["all_required_routes_http_200"] is True
    assert payload["evidence_ref"]["runtime_checks"]["context_snapshot_public_projection"]["summary_source"] == "chat_stream"
    assert payload["evidence_ref"]["runtime_checks"]["context_snapshot_public_projection"]["input_keys"] == [
        "attachments",
        "message",
    ]
    assert payload["evidence_ref"]["runtime_checks"]["document_review_attachment_run"]["status"] == "succeeded"
    assert payload["evidence_ref"]["runtime_checks"]["document_review_attachment_run"]["artifact_types"] is None
    assert payload["evidence_ref"]["runtime_checks"]["document_review_attachment_run"]["playback_status"] == 200
    assert payload["evidence_ref"]["runtime_checks"]["document_review_attachment_run"]["matched_download_artifact_count"] == 1
    assert payload["evidence_ref"]["runtime_checks"]["document_review_attachment_run"]["matched_preview_artifact_count"] == 1
    assert payload["evidence_ref"]["runtime_checks"]["document_review_attachment_run"]["private_payload_leaked"] is False
    assert payload["evidence_ref"]["runtime_checks"]["artifact_download_isolation"]["checked_artifacts"] == 2
    assert payload["evidence_ref"]["runtime_checks"]["artifact_download_isolation"]["owner_statuses"] == [200, 200]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_download_isolation"]["cross_user_statuses"] == [
        404,
        404,
    ]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_download_isolation"]["cross_tenant_statuses"] == [
        404,
        404,
    ]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["checked_artifacts"] == 1
    assert payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["owner_statuses"] == [200]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["cross_user_statuses"] == [404]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["cross_tenant_statuses"] == [404]
    assert payload["evidence_ref"]["runtime_checks"]["artifact_preview_isolation"]["owner_content_types"] == [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    smoke_followups = "\n".join(payload["open_followups"])
    assert "alert_delivery_and_trace_export_211_acceptance" not in smoke_followups
    assert "Signed package or SBOM review evidence" in smoke_followups

    release_evidence_index = read(RELEASE_EVIDENCE_INDEX)
    assert f"{ACTIVE_AUTH_RBAC_EVIDENCE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_POC_SMOKE_EVIDENCE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_GOVERNANCE_RUNTIME_EVIDENCE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_RELEASE_EVIDENCE_RUNTIME_ACCEPTANCE_ID}.json" in release_evidence_index
    assert f"{ACTIVE_ALERT_TRACE_EXPORT_RUNTIME_ACCEPTANCE_ID}.json" in release_evidence_index
    assert "Foundation Runtime Concurrency" in release_evidence_index
    assert "foundation-runtime-concurrency-evidence-211-20260614-013347.json" in release_evidence_index
    assert "foundation-runtime-concurrency-readiness-211-20260614-013347.json" in release_evidence_index
    assert "verified for 2 tenants, 4 users, and 12 concurrent" in release_evidence_index
    assert "does not raise production concurrency defaults" in release_evidence_index
    assert "open ordinary-user multi-agent" in release_evidence_index
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
        "/home/xinlin.jiang/",
        "artifact_storage_key",
        "tenants/default/workspaces",
        "tenants/default",
    )
    lowered = evidence_text.lower()
    for marker in forbidden_markers:
        assert marker.lower() not in lowered

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

    for path in (
        FOUNDATION_ALPHA_POC_ACTIVE_SMOKE_EVIDENCE,
        FOUNDATION_ALPHA_POC_ACTIVE_AUTH_RBAC_EVIDENCE,
    ):
        payload = json.loads(read(path))
        source_ref = payload["source_ref"]
        labels = source_ref["image_labels"]

        assert payload["artifact_kind"] == "211_runtime_smoke"
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
    assert compose_text.count("context: ../..") == 2
    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text


def test_env_template_satisfies_required_runtime_defaults_without_real_secrets():
    env_text = read(ENV_EXAMPLE)
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" in env_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.25:7263" in env_text
    assert "EXISTING_USER_INFO_BASE_URL=http://10.56.0.25:5166" in env_text
    assert "CLAUDE_AGENT_SDK_MAX_TURNS=48" in env_text
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

    assert compose_text.count("CLAUDE_AGENT_SDK_MAX_TURNS: ${CLAUDE_AGENT_SDK_MAX_TURNS:-48}") == 2


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
        ".ai-platform-source-revision",
        ".ai-platform-source-snapshot.json",
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


def test_gate_status_snapshot_records_memory_context_readiness_fields():
    gate_status_text = read(GATE_STATUS_DOC)

    assert "memory_context_controls" in gate_status_text
    assert "session_scoped_memory=true" in gate_status_text
    assert "ordinary_user_opt_out=true" in gate_status_text
    assert "retention_cleanup=true" in gate_status_text
    assert "delete_redaction=true" in gate_status_text
    assert "public_admin_projection_safe=true" in gate_status_text
    assert "long_term_cross_session_memory_fail_closed=true" in gate_status_text
    assert "ordinary-user expansion remains blocked" in gate_status_text


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


def test_capacity_docs_record_latest_211_bounded_probe_without_closing_gate():
    capacity_text = read(CAPACITY_BASELINE_DOC)

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
        assert "skill_signed_package_evidence_contract" in text
        assert "ai-platform.skill-release-review.v1" in text
        assert "sbom_reviewed" in text
        assert "license_policy_reviewed" in text
        assert "vulnerability_reviewed" in text
        assert "dependency_vulnerability_or_license_policy" in text
        assert "skill_dependency_review_policy_runtime_acceptance" in text
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


def test_frontend_docs_record_packaged_runtime_smoke_contract_and_211_blocker():
    frontend_text = read(FRONTEND_MIGRATION_DOC)
    roadmap_text = read(ROADMAP)

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


def test_prd_records_claude_code_deerflow_boundary_without_second_runtime():
    prd_text = read(PRD)
    roadmap_text = read(ROADMAP)

    assert "Claude Agent SDK is the current primary execution kernel" in prd_text
    assert "DeerFlow 2.0 remains a long-horizon workflow and subagent/concurrency concept" in prd_text
    assert "It is not a second ai-platform runtime, scheduler, or memory" in prd_text
    assert "authority" in prd_text
    assert "Executor-private logs" in prd_text
    assert "private artifact metadata are never the platform source" in prd_text
    assert "truth" in prd_text
    assert "A second independent runtime/control plane" in prd_text
    assert "direct replacement for ai-platform worker/runtime" in prd_text
    assert "CLI-internal subagents are not automatically platform multi-agent runs" in prd_text
    assert "G8 Multi-Agent Controlled Beta" in prd_text
    assert "G10 Internal Beta / Department Rollout" in prd_text
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
