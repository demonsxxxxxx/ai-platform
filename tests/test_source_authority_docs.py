import re
from pathlib import Path


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
SOURCE_ARCHITECTURE_ADR = ROOT / "docs/adr/0006-domain-first-modular-monolith.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def test_agent_coding_contract_has_one_authority_and_risk_scaled_evidence():
    agents = read(AGENTS)
    claude = read(CLAUDE)
    workflow = read(GITHUB_WORKFLOW)
    local_test_execution = read(LOCAL_TEST_EXECUTION)
    pull_request_template = read(PULL_REQUEST_TEMPLATE)
    agents_flat = " ".join(agents.split())
    claude_flat = " ".join(claude.split())
    workflow_flat = " ".join(workflow.split())

    assert "## Change Control" in agents
    assert re.search(r"`AGENTS\.md` is .*repository coding authority", agents)
    assert "A focused ordinary change may use its pull request" in agents_flat
    assert "goal-sized or high-risk change" in agents_flat
    high_risk_boundaries = (
        "authentication, authorization, tenant or workspace isolation",
        "secrets, credentials, or ordinary-user projection redaction",
        "destructive lifecycle, retention, schema migration, or irreversible data compatibility",
        "sandbox, command, tool, Skill, MCP, or executor admission",
        "public API, callback, event, or streaming protocols",
        "workflow, image, release, deployment, or rollback authority",
    )
    for high_risk_boundary in high_risk_boundaries:
        assert high_risk_boundary in agents_flat
        assert high_risk_boundary in workflow_flat
        assert high_risk_boundary in " ".join(pull_request_template.split())
    assert "single repository coding authority" in claude_flat
    assert "must not duplicate or weaken it" in claude_flat
    assert "high-risk Change Contract" in claude_flat
    assert "A focused ordinary change may use its pull request" in workflow_flat
    assert "does not require a separate issue" in workflow_flat
    assert "authors do not copy SHAs" in workflow_flat
    assert "falsifiable regression test" in workflow_flat
    assert "Pull-request text written by the author is not proof" in workflow_flat
    assert "review comments are the disposition record" in workflow_flat.casefold()
    assert "python tools/run_test_stage.py" in agents
    assert "docs/agent-rules/local-test-execution.md" in agents
    for execution_rule in (
        "test_isolation_failure",
        "test_timeout",
        "invalid_test_plan",
        "required_dependency_missing",
        "passed_with_skips",
        "spawnSync",
        "only direct-pytest exception",
        "Git-tracked",
        "any skip returns a non-zero exit",
        "a fixture cannot own a production runtime task",
        "tasks created only by a test",
    ):
        assert execution_rule in local_test_execution
    for heading in (
        "## Purpose",
        "## Scope",
        "## Verification",
        "## Risk",
        "## High-risk changes only",
    ):
        assert heading in pull_request_template
    for required_field in (
        "Problem and intended outcome:",
        "Changed behavior and owning modules:",
        "Explicit non-goals:",
        "Falsifiable regression test:",
        "Commands run and observed results:",
        "Checks not run and why:",
        "Reached boundaries and preserved invariants:",
        "Design or Change Contract:",
        "Independent review and rollback or migration plan:",
    ):
        assert required_field in pull_request_template
    for removed_boilerplate in (
        "Issue / Change Contract:",
        "Full base SHA / candidate head SHA:",
        "Writable paths / forbidden paths",
        "ai-platform.review-findings.v1",
        "REPLACE_ME",
        "CI/build, packaged-artifact, deployment/runtime",
        "Closes`/`Fixes",
    ):
        assert removed_boilerplate not in pull_request_template


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
    assert "The gate itself MUST be introduced in a later PR" in architecture_flat
    assert "status: accepted" in adr
    assert "decision_issue: 962" in adr
    assert "API, worker, executor, and maintenance entrypoints" in adr
    assert "sandbox entrypoints" not in adr


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
    workflow = read(MULTI_AGENT_WORKFLOW)

    assert "Historical runtime observations" in agents
    assert "not a project status ledger" in ci_governance_flat
    assert "Current work, owners, exceptions, and completion state" in ci_governance_flat
    assert "controller checkpoint" in workflow
    assert "one mutation lease" in workflow


def test_release_runbook_remains_the_only_executable_release_authority():
    runbook = read(RUNBOOK)

    assert "Canonical Exact-Main Command" in runbook
    assert "deploy-main-commit" in runbook
    assert '--docker-cmd "sudo -n docker"' in runbook
    assert "final source/runtime parity" in runbook
    assert "same release authority" in runbook
    assert "s72 gateway runbook" not in runbook


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


def test_release_evidence_index_is_a_contract_not_a_status_snapshot():
    index = read(RELEASE_EVIDENCE_INDEX)

    assert "not a current-status" in index
    assert "docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json" in index
    assert "Do not commit generated Markdown" in index
    assert "runtime_subject_commit_sha" in index
    assert "ordinary pull requests" in index.casefold()
    assert "Actions artifacts" in index
    assert "authorized runtime procedure" in index
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
