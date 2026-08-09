from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
DOCS_INDEX = ROOT / "docs/README.md"
GUARDRAILS = ROOT / "docs/agent-rules/ai-platform-guardrails.md"
MULTI_AGENT_WORKFLOW = ROOT / "docs/agent-rules/multi-agent-context-workflow.md"
GITHUB_WORKFLOW = ROOT / "docs/agent-rules/github-issue-pr-workflow.md"
RUNBOOK = ROOT / "docs/operations/211-release-operations-runbook.md"
S72_RUNBOOK = ROOT / "docs/operations/s72-opensandbox-gateway-runbook.md"
RELEASE_EVIDENCE_INDEX = ROOT / "docs/release-evidence/README.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_documentation_index_names_the_only_durable_authority_surfaces():
    index = read(DOCS_INDEX)

    assert "not a project status report" in index
    assert "sole executable 211" in index
    assert "Reviewed, redacted evidence" in index
    for relative_path in (
        "agent-rules/ai-platform-guardrails.md",
        "agent-rules/multi-agent-context-workflow.md",
        "agent-rules/github-issue-pr-workflow.md",
        "operations/211-release-operations-runbook.md",
        "operations/s72-opensandbox-gateway-runbook.md",
        "release-evidence/README.md",
    ):
        assert relative_path in index


def test_governance_rules_keep_status_and_release_authority_out_of_history_docs():
    agents = read(AGENTS)
    guardrails = read(GUARDRAILS)
    github = read(GITHUB_WORKFLOW)
    workflow = read(MULTI_AGENT_WORKFLOW)

    assert "Historical runtime observations" in agents
    assert "Current status" in guardrails
    assert "repository status pages" in github
    assert "controller checkpoint" in workflow
    assert "one mutation lease" in workflow


def test_release_runbook_remains_the_only_211_executable_authority():
    runbook = read(RUNBOOK)

    assert "Canonical Exact-Main Command" in runbook
    assert "deploy-main-commit" in runbook
    assert '--docker-cmd "sudo -n docker"' in runbook
    assert "final source/runtime parity" in runbook
    assert "same release authority" in runbook
    assert "s72 gateway runbook" not in runbook


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
    assert "cannot establish a `211 verified` claim" in runbook


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
