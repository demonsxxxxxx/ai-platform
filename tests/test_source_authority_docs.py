import re
from pathlib import Path

import pytest

from tools.check_documentation import check_document_links


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
DOCS_INDEX = ROOT / "docs/README.md"
MULTI_AGENT_WORKFLOW = ROOT / "docs/agent-rules/multi-agent-context-workflow.md"
GITHUB_WORKFLOW = ROOT / "docs/agent-rules/github-issue-pr-workflow.md"
LOCAL_TEST_EXECUTION = ROOT / "docs/agent-rules/local-test-execution.md"
PULL_REQUEST_TEMPLATE = ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
CLAUDE = ROOT / "CLAUDE.md"
RUNBOOK = ROOT / "docs/operations/release-operations-runbook.md"
RELEASE_EVIDENCE_INDEX = ROOT / "docs/release-evidence/README.md"
SOURCE_ARCHITECTURE = ROOT / "docs/architecture/source-code-architecture.md"
CI_TEST_READINESS_GOVERNANCE = ROOT / "docs/architecture/ci-test-readiness-governance.md"
SDK_DIAGNOSTIC_HISTORY = ROOT / "docs/agent-rules/history/github-sdk-diagnostic-examples.md"
DATA_LIFECYCLE = ROOT / "docs/architecture/single-enterprise-data-lifecycle.md"
SOURCE_ARCHITECTURE_ADR = ROOT / "docs/adr/0006-domain-first-modular-monolith.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def local_link_targets(document: Path, root: Path) -> set[Path]:
    findings, targets = check_document_links(root, document, check_anchors=False, require_files=True)
    assert not findings, "\n".join(findings)
    return targets


def test_authority_map_and_direct_owners_have_resolvable_local_file_links():
    owners = local_link_targets(DOCS_INDEX, ROOT)
    for owner in sorted(owners):
        if owner.suffix == ".md":
            local_link_targets(owner, ROOT)


@pytest.mark.parametrize("link", ["missing.md", "../outside.md", "%2e%2e/outside.md"])
def test_local_link_check_rejects_missing_and_escaping_targets(tmp_path: Path, link: str):
    root = tmp_path / "repository"
    root.mkdir()
    (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
    document = root / "README.md"
    document.write_text(f"[owner]({link})\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="link_target_missing|link_outside_repository"):
        local_link_targets(document, root)


def test_local_link_check_preserves_fragments_and_skips_external_urls(tmp_path: Path):
    document = tmp_path / "README.md"
    owner = tmp_path / "owner with spaces.md"
    owner.write_text("# Owner\n", encoding="utf-8")
    document.write_text(
        "[self](#owner)\n[owner](owner%20with%20spaces.md#owner)\n"
        "[web](https://example.test/missing.md)\n[web](//example.test/missing.md)\n",
        encoding="utf-8",
    )

    assert local_link_targets(document, tmp_path) == {document, owner}


def test_documentation_index_names_the_only_durable_authority_surfaces():
    index = read(DOCS_INDEX)

    assert "not a project status report" in index
    assert "sole executable release" in index
    assert "Reviewed, redacted evidence" in index
    for relative_path in (
        "agent-rules/multi-agent-context-workflow.md",
        "agent-rules/github-issue-pr-workflow.md",
        "agent-rules/local-test-execution.md",
        "architecture/source-code-architecture.md",
        "architecture/ci-test-readiness-governance.md",
        "adr/0006-domain-first-modular-monolith.md",
        "operations/release-operations-runbook.md",
        "release-evidence/README.md",
    ):
        assert relative_path in index


def test_agent_entrypoints_share_one_instruction_source():
    imports = [line[1:].strip() for line in read(CLAUDE).splitlines() if line.startswith("@")]

    assert imports == ["AGENTS.md"]
    assert (CLAUDE.parent / imports[0]).resolve() == AGENTS.resolve()
    assert AGENTS.is_file()


def test_agent_rule_navigation_and_pr_template_remain_usable():
    # Check navigable contracts, not prose used to describe optional workflows.
    required_links = {
        AGENTS: {
            "docs/README.md",
            "docs/agent-rules/local-test-execution.md",
            "docs/agent-rules/github-issue-pr-workflow.md",
            "docs/agent-rules/multi-agent-context-workflow.md",
            "docs/operations/release-operations-runbook.md",
        },
        GITHUB_WORKFLOW: {
            "../README.md",
            "local-test-execution.md",
            "../architecture/ci-test-readiness-governance.md",
            "../operations/release-operations-runbook.md",
        },
        LOCAL_TEST_EXECUTION: {
            "../architecture/ci-test-readiness-governance.md",
            "github-issue-pr-workflow.md",
        },
        SDK_DIAGNOSTIC_HISTORY: {"../github-issue-pr-workflow.md"},
        SOURCE_ARCHITECTURE: {
            "../../.github/workflows/ai-platform-trusted-governance-v2.yml",
            "../agent-rules/github-issue-pr-workflow.md",
            "../../.github/PULL_REQUEST_TEMPLATE.md",
            "../../architecture-policy.json",
            "../../schemas/architecture-policy.v1.schema.json",
        },
        DATA_LIFECYCLE: {
            "../operations/release-operations-runbook.md",
            "../operations/redis-streams-sse-cutover-acceptance.md#explicit-legacy-state-retirement",
            "run-lifecycle-boundary.md",
            "agent-profile-persistence-boundary.md",
        },
        MULTI_AGENT_WORKFLOW: {
            "github-issue-pr-workflow.md",
            "../operations/release-operations-runbook.md",
        },
    }
    for document, expected_links in required_links.items():
        links = set(re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", read(document)))
        assert expected_links <= links, document
        local_link_targets(document, ROOT)

    template = read(PULL_REQUEST_TEMPLATE)
    assert "docs/agent-rules/github-issue-pr-workflow.md" in template
    assert "Retirement / Compatibility Disposition" in template
    assert "retirement and compatibility disposition" in read(AGENTS)
    for retirement_field in (
        "Superseded production paths:",
        "Superseded tests and selectors:",
        "Superseded documentation and configuration:",
        "Retained compatibility surfaces and removal proof:",
        "Post-change absence or inventory check (command and result):",
    ):
        assert retirement_field in template
    for required_field in (
        "Falsifiable regression test:",
        "Reached boundaries and preserved invariants:",
        "Design or Change Contract:",
    ):
        assert required_field in template


def test_source_architecture_authority_has_required_sections_and_anchors():
    architecture = read(SOURCE_ARCHITECTURE)
    architecture_flat = " ".join(architecture.split())
    adr = read(SOURCE_ARCHITECTURE_ADR)
    headings = {
        line.strip()
        for line in architecture.splitlines()
        if line.startswith("## ")
    }

    assert "not a project status report" in architecture_flat
    assert {
        "## 2. Target package tree",
        "## 3. Dependency direction",
        "## 7. Compatibility contract",
        "## 8. Deletion proof",
        "## 9. Migration and behavior replay",
        "## 10. Current-to-target mapping",
        "## 12. Executable governance",
    } <= headings
    for package in (
        "bootstrap",
        "kernel",
        "platform",
        "identity",
        "agent_apps",
        "skills",
        "conversations",
        "runs",
        "context",
        "files",
        "artifacts",
        "object_lifecycle",
        "streaming",
        "mcp",
        "execution",
        "sandbox",
        "compat",
    ):
        assert f"  {package}/" in architecture
    for deletion_surface in (
        "| Private function/class |",
        "| Python module/package |",
        "| CLI/script/entrypoint |",
        "| Provider/executor/parser/plugin |",
        "| Public HTTP/SSE/callback route |",
        "| Environment/configuration key |",
        "| Database column/table/state/event |",
        "| Import compatibility facade |",
    ):
        assert deletion_surface in architecture
    assert "Cross-domain Python calls use the owning domain's `api.py`" in adr
    assert "Compatibility is exceptional and evidence-based" in architecture
    assert "app/repositories.py" in architecture
    assert "app/models.py" in architecture
    for authority_anchor in (
        "callback-batch receipt remains part of the Sandbox Runtime",
        "only `object_lifecycle` claims, receipts, fails, dead-letters, or requeues",
        "one database Unit of Work",
        "At most one outbox row exists for the typed target identity",
        "The baseline still runs maintenance in the shared worker loop",
        "telemetry or inventory source and reproducible query",
        "Manual return-value comparison alone is insufficient",
        "architecture-policy.json",
        "schemas/architecture-policy.v1.schema.json",
    ):
        assert authority_anchor in architecture_flat
    assert "status: accepted" in adr
    assert "decision_issue: 962" in adr
    assert "API, worker, executor, and maintenance entrypoints" in adr
    assert "sandbox entrypoints" not in adr


def test_source_architecture_distinguishes_executed_gates_from_review_evidence():
    architecture = " ".join(read(SOURCE_ARCHITECTURE).split())
    workflow = read(ROOT / ".github/workflows/ai-platform-trusted-governance-v2.yml")
    for tool in ("trusted_governance.py", "code_governance.py", "architecture_governance.py"):
        assert f"tools/{tool}" in architecture
        assert f'python -P "$GOVERNANCE_BASE_WORKTREE/tools/{tool}"' in workflow
    assert "exact base/head candidate" in architecture
    assert "does not read PR bodies" in architecture
    assert "require human review" in architecture
    assert "reviewers must inspect or rerun" in architecture
    assert "contract is a disposition ledger" not in architecture
    assert "MUST be introduced in a later PR" not in architecture


def test_sdk_diagnostic_history_does_not_restore_a_removed_rule():
    history = " ".join(read(SDK_DIAGNOSTIC_HISTORY).split())
    assert "historical context, not current merge gates" in history
    assert "generic layered diagnostic rule" not in history


def test_release_environment_reference_does_not_claim_configured_approval():
    runbook = " ".join(read(RUNBOOK).split())
    assert "`packaging-publish`" in runbook
    assert "Recheck the actual environment settings" in runbook
    assert "including protection rules and deployment branch policy" in runbook
    assert "verify Backend and Frontend checks for the selected exact main commit" in runbook
    assert "Publication is protected by" not in runbook
    assert "selects the named environment" in runbook
    assert "main-source, environment," not in runbook


def test_ci_test_readiness_governance_separates_evidence_and_tracks_completion():
    governance = read(CI_TEST_READINESS_GOVERNANCE)
    governance_flat = " ".join(governance.split())
    architecture = read(SOURCE_ARCHITECTURE)
    headings = {
        line.strip()
        for line in governance.splitlines()
        if line.startswith("## ")
    }

    assert "[`ci-test-readiness-governance.md`](ci-test-readiness-governance.md)" in architecture
    assert (
        "[`../agent-rules/local-test-execution.md`](../agent-rules/local-test-execution.md)"
        in governance
    )
    assert {
        "## 2. Evidence levels",
        "## 3. Test model",
        "## 4. Required CI topology",
        "## 5. Runtime readiness boundary",
        "## 6. Retirement and ownership",
    } <= headings
    for evidence_level in (
        "| Source |",
        "| Focused test |",
        "| CI/build |",
        "| Packaged image |",
        "| Deployment |",
        "| Runtime |",
        "| External acceptance |",
    ):
        assert evidence_level in governance
    for contract in (
        "A missing service is a failure, not a skip",
        "must not be scanned by a live health endpoint",
        "Product tests do not wait for author-written review metadata",
        "A test appears in one required lane",
        "Current work, owners, exceptions, and completion state belong in the active pull",
    ):
        assert contract.casefold() in governance_flat.casefold()
    for dynamic_status in (
        "Authority baseline audited:",
        "Ledger refreshed:",
        "Unified disposition ledger",
        "Pending PR #",
        "Completed in source",
    ):
        assert dynamic_status not in governance


def test_governance_rules_keep_status_and_release_authority_out_of_history_docs():
    agents = read(AGENTS)
    ci_governance = read(CI_TEST_READINESS_GOVERNANCE)
    ci_governance_flat = " ".join(ci_governance.split())
    workflow = " ".join(read(MULTI_AGENT_WORKFLOW).split())

    assert "Historical evidence" in agents
    assert "not a project status ledger" in ci_governance_flat
    assert "Current work, owners, exceptions, and completion state" in ci_governance_flat
    assert "active task record" in workflow
    assert "one mutation lease" in workflow


def test_release_runbook_remains_the_only_executable_release_authority():
    runbook = read(RUNBOOK)

    assert "python3 deploy.py --env-file /absolute/path/to/.env" in runbook
    assert "ai-platform-internal-test.tar.gz" in runbook
    assert "ai-platform-production.tar.gz" not in runbook
    assert "immutable Deployment Release" in runbook
    assert "do not mix package files" in runbook
    assert "final runtime acceptance" in runbook
    assert "task ownership, readiness, and release leases live in" not in runbook
    assert "s72 gateway runbook" not in runbook
    assert "deploy-main-commit" not in runbook
    assert "incoming/latest-main.json" not in runbook


def test_decommissioned_runtime_is_not_an_active_source_authority():
    retired_host = "10.56.0." + "211"
    retired_connection = "s" + "211"
    retired_runbook = ROOT / "docs/operations" / ("211" + "-release-operations-runbook.md")
    retired_guardrails = ROOT / "docs/agent-rules/ai-platform-guardrails.md"
    retired_tools = (
        ROOT / "scripts" / ("generate_executor_context_pack_evidence_" + "211.py"),
        ROOT / "scripts" / ("verify_executor_context_pack_" + "211.py"),
        ROOT / "scripts" / ("generate_sandbox_runtime_evidence_" + "211.py"),
        ROOT / "scripts" / ("verify_sandbox_runtime_" + "211.py"),
    )

    assert not retired_runbook.exists()
    assert not retired_guardrails.exists()
    assert all(not path.exists() for path in retired_tools)

    active_sources = (
        AGENTS,
        DOCS_INDEX,
        RUNBOOK,
        RELEASE_EVIDENCE_INDEX,
        ROOT / "README.md",
        ROOT / "frontend/web/README.md",
        ROOT / "frontend/web/scripts/prd-closure-browser-smoke.mjs",
        ROOT / "app/settings.py",
        ROOT / "app/office_context_readiness.py",
        ROOT / "deploy/ai-platform/docker-compose.yml",
        ROOT / "scripts/generate_executor_context_pack_evidence.py",
        ROOT / "scripts/verify_executor_context_pack.py",
        ROOT / "scripts/generate_sandbox_runtime_evidence.py",
        ROOT / "scripts/verify_sandbox_runtime.py",
    )
    active_text = "\n".join(read(path) for path in active_sources)
    assert retired_host not in active_text
    assert retired_connection not in active_text
    assert "211_api_worker_runtime" not in active_text
    assert "211 verified" not in active_text


def test_release_evidence_index_is_a_contract_not_a_status_snapshot():
    index = read(RELEASE_EVIDENCE_INDEX)

    assert "not a current-status" in index
    assert "docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json" in index
    assert "Do not commit generated Markdown" in index
    assert "runtime_subject_commit_sha" in index
    assert "ordinary pull requests" in index.casefold()
    assert "Actions artifacts" in index
    assert "authorized runtime procedure" in index
    index_flat = " ".join(index.split())
    assert "runtime verification for its exact deployed subject" in index_flat
    assert "Historical entries retain their original subject and labels" in index_flat
    assert "current overall status" not in index


def test_historical_status_and_manual_release_docs_are_not_retained():
    removed_paths = (
        "docs/operations/ai-platform-gate-status.md",
        "docs/operations/ai-platform-capacity-baseline.md",
        "docs/operations/ai-platform-governance-readiness.md",
        "docs/operations/ai-platform-observability-readiness.md",
        "docs/operations/ai-platform-parallel-session-board.md",
        "docs/operations/frontend-static-release-deploy.md",
        "docs/frontend/ai-platform-frontend-migration.md",
        "docs/frontend/prd-closure-browser-smoke.md",
        "docs/frontend/prd-frontend-closure-matrix.md",
        "docs/release-evidence/frontend-complete/backend-gap-summary.md",
    )

    for relative_path in removed_paths:
        assert not (ROOT / relative_path).exists()


def test_release_evidence_keeps_concurrency_artifacts_machine_readable():
    evidence_root = RELEASE_EVIDENCE_INDEX.parent / "foundation-runtime-concurrency"

    assert list(evidence_root.rglob("*.json"))
    assert not list(evidence_root.rglob("*.md"))


def test_schema_lifecycle_documents_destructive_source_contracts():
    lifecycle = " ".join(read(DATA_LIFECYCLE).split())
    schema = read(ROOT / "app/schema.sql")
    migrations = read(ROOT / "app/schema_migrations.py")
    # These are compatibility boundaries, not an editorial wording inventory.
    for retired_surface, source_removal in (
        ("publication columns", "drop column if exists stream_publication_state"),
        ("run_tool_permission_requests", "drop table if exists run_tool_permission_requests"),
        ("compatibility triggers", "drop trigger if exists trg_agent_profile_legacy_insert_compatibility"),
    ):
        assert retired_surface in lifecycle
        assert source_removal in schema
    assert 'RETIRED_RELATIONS = ("run_tool_permission_requests",)' in migrations
    assert "not binary rollback-compatible" in migrations
    assert "old binary fails readiness" in lifecycle
    assert "Migrations in this phase are additive" not in lifecycle
    assert "Apply additive core schema" not in migrations


def test_schema_lifecycle_separates_retry_safety_from_rollback_authority():
    lifecycle = " ".join(read(DATA_LIFECYCLE).split())
    migrations = read(ROOT / "app/schema_migrations.py")
    runbook = " ".join(read(RUNBOOK).split())
    assert "session-scoped advisory coordinator lock" in lifecycle
    assert "transaction-scoped advisory lock" in lifecycle
    assert "pg_advisory_xact_lock" in migrations
    assert "pg_try_advisory_lock" in migrations
    assert "committed destructive cutover reversible" in lifecycle
    assert "core transaction can already be committed" in lifecycle
    assert "only between supported versions with evidence" in lifecycle
    assert "entire installed schema" in lifecycle
    assert "separate reversibility evidence" in lifecycle
    assert "no automatic down migration" in lifecycle
    assert "explicit deployment authority" in lifecycle
    assert "does not establish a verified restore" in lifecycle
    assert "operator-authorized restore" in lifecycle
    assert "operator-authorized restore" in runbook
    assert "Do not edit migration checksums" in runbook
