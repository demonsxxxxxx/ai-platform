from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
DOCS_INDEX = ROOT / "docs/README.md"
MULTI_AGENT_WORKFLOW = ROOT / "docs/agent-rules/multi-agent-context-workflow.md"
GITHUB_WORKFLOW = ROOT / "docs/agent-rules/github-issue-pr-workflow.md"
RUNBOOK = ROOT / "docs/operations/release-operations-runbook.md"
S72_RUNBOOK = ROOT / "docs/operations/s72-opensandbox-gateway-runbook.md"
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
        "architecture/source-code-architecture.md",
        "architecture/ci-test-readiness-governance.md",
        "adr/0006-domain-first-modular-monolith.md",
        "operations/release-operations-runbook.md",
        "operations/s72-opensandbox-gateway-runbook.md",
        "release-evidence/README.md",
    ):
        assert relative_path in index


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
    assert "Authority baseline audited: `6c010079782afe30ada5f75c44600939f0381b13`" in governance
    assert {
        "## 2. Evidence levels",
        "## 3. Target test model",
        "## 4. Required CI topology",
        "## 5. Runtime readiness boundary",
        "## 6. Unified disposition ledger",
        "## 8. Completion criteria for this governance program",
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
        "A missing required service is a CI failure, not a skip",
        "MUST NOT be scanned by a live health endpoint",
        "Completion proof / remaining exit",
        "required integration suites cannot pass by skipping missing dependencies",
        "required aggregators execute tested failure paths",
        "the final ledger has no unowned `audit and assign` entries",
    ):
        assert contract in governance_flat


def test_governance_rules_keep_status_and_release_authority_out_of_history_docs():
    agents = read(AGENTS)
    github = read(GITHUB_WORKFLOW)
    workflow = read(MULTI_AGENT_WORKFLOW)

    assert "Historical runtime observations" in agents
    assert "repository status pages" in github
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
        ROOT / "app/b2_sandbox_readiness.py",
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


def test_s72_runbook_owns_gateway_install_and_rollback_contracts():
    runbook = " ".join(read(S72_RUNBOOK).split())

    assert "deploy/opensandbox/install-s72.sh" in runbook
    assert "deploy/opensandbox/rollback-s72.sh" in runbook
    assert "one mutation lease" in runbook
    assert "root-owned, clean source checkout" in runbook
    assert "OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA" in runbook
    assert "/etc/opensandbox-gateway" in runbook
    assert "`0750`" in runbook
    assert "`0640`" in runbook
    assert "`0440`" in runbook
    assert "tls/upstream-ca.pem" in runbook
    assert "system trust store" in runbook
    assert "Before an ai-platform provider switch" in runbook
    assert "/run/lock/opensandbox-gateway-s72-install.lock" in runbook
    assert "recovery snapshot" in runbook
    assert "install-s72.sh --recover" in runbook
    assert "same-parent rename" in runbook
    assert "MANIFEST.identity" in runbook and "SNAPSHOT.seal" in runbook
    assert "self-authenticating transaction-record chain" in runbook
    assert "reserved -> snapshot-published" in runbook
    assert "release-published -> staged" in runbook
    assert "runtime-restored -> committed -> cleaned" in runbook
    assert "identity-group-intent -> identity-group-ready" in runbook
    assert "`UnitFileState`, `LoadState`, and `ActiveState`" in runbook
    assert "gateway UID binds the exact system group, account, home, shell" in runbook
    assert "`failed`, `activating`, `static`, `masked`, `linked`, or `enabled-runtime`" in runbook
    assert "real, effective, saved, and filesystem UIDs" in runbook
    assert "after group cleanup immediately before identity advancement" in runbook
    assert "private producer/consumer stream enforces byte and row limits" in runbook
    assert "Before the transaction records `stopped`" in runbook
    assert "hard-queried again after disable" in runbook
    assert "it never kills a process" in runbook
    assert "published from a transaction-owned private stage" in runbook
    assert "foreign replacement is preserved and fails closed" in runbook
    assert "device/inode" in runbook
    assert "exactly one `LISTEN` `127.0.0.1:8080`" in runbook
    assert "left untouched and recovery fails closed" in runbook
    assert "They do not prove a live systemd/Docker deployment" in runbook
    assert "cannot establish application runtime acceptance" in runbook


def test_release_evidence_index_is_a_contract_not_a_status_snapshot():
    index = read(RELEASE_EVIDENCE_INDEX)

    assert "not a current-status" in index
    assert "docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json" in index
    assert "Do not commit generated Markdown" in index
    assert "runtime_subject_commit_sha" in index
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
