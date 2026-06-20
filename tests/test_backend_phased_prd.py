from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PRD = ROOT / "docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md"


def read_backend_prd() -> str:
    return BACKEND_PRD.read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def test_backend_prd_records_authority_status_and_stage_boundaries():
    text = read_backend_prd()
    compact_text = compact(text)

    for required_section in (
        "## 0. Executive Decision",
        "## 1. Source Authority And Status Language",
        "## 2. Product Non-Goals",
        "## 3. Backend Stage Model",
        "## 4. Gate Register",
        "## 5. Acceptance Boundaries By Stage",
        "## 6. Universal Gate Closure Checklist",
        "## 7. Reference Code Projects",
        "## 8. Immediate Issue Chain",
        "## 9. Backend Product Beta Definition Of Done",
    ):
        assert required_section in text

    for status_label in (
        "`local partial`",
        "`PR ready`",
        "`reviewed`",
        "`merged`",
        "`211 verified`",
        "`gate closable`",
    ):
        assert status_label in text

    for stage in ("B0", "B1", "B2", "B3", "B4", "B5", "B6"):
        assert f"| {stage} |" in text

    for sequencing_rule in (
        "B0 runs first whenever source/runtime evidence is stale or ambiguous.",
        "After B0, the product order is B1 memory/context, B2 real sandbox, B3 worker/model-gateway capacity, then B4 Skills management.",
        "A runtime blocker can pause the sequence, but it must be recorded as a named issue instead of silently reordering the roadmap.",
    ):
        assert sequencing_rule in compact_text

    assert "| B3 | `source_contract`" not in text
    assert (
        "| B3 | `local partial`; source contract only for `b3_10x4_sdk_subagents`"
        in text
    )

    for stage_heading in (
        "### 5.2 B0 Latest-Main Backend Readiness Refresh",
        "### 5.3 B1 Memory And Context Usable",
        "### 5.4 B2 Real Sandbox Usable",
        "### 5.5 B3 Worker And Model-Gateway Capacity",
        "### 5.6 B4 Skills Management And Release Governance",
        "### 5.7 B5 Files, Artifacts, And Tool Permission Governance",
        "### 5.8 B6 Operations Beta And Department Workflow Readiness",
    ):
        assert stage_heading in text

    for boundary_phrase in (
        "Entry gate: when the team is allowed to start implementation.",
        "Local acceptance: what a PR must prove before review.",
        "Runtime acceptance: what 211 or another named Docker-capable target must prove",
        "Exit gate: what must be true before the stage is `gate closable`.",
        "No docs-only PR may create `211 verified` or `gate closable` status.",
        "If these documents disagree, stop feature work and repair source authority before making product claims.",
        "docs/superpowers/plans/2026-06-20-backend-productization-prd-gates.md",
        "All reports must use the narrowest true status",
    ):
        assert boundary_phrase in compact_text


def test_backend_prd_records_claim_ladder_and_stage_evidence_fields():
    text = read_backend_prd()
    compact_text = compact(text)

    assert "### 1.1 Claim Ladder" in text
    for claim_rule in (
        "planning source",
        "local contract",
        "runtime evidence",
        "stage closure",
        "beta readiness",
    ):
        assert claim_rule in text

    for phrase in (
        "A stage can advance only one claim level at a time.",
        "A `211 verified` runtime smoke does not by itself create `gate closable`.",
        "A `gate closable` backend bundle does not by itself create product beta.",
    ):
        assert phrase in compact_text

    assert "### 5.1 Required Evidence Shape" in text
    for field in (
        "`issue_or_decision`",
        "`source_subject`",
        "`local_verification`",
        "`runtime_verification`",
        "`review_disposition`",
        "`residual_caveats`",
        "`non_expansion_invariants`",
        "`rollback_or_disable_path`",
    ):
        assert field in text

    for invariant in (
        "production_concurrency_defaults_raised=false",
        "ordinary_user_platform_multi_run_orchestration_enabled=false",
        "docker_sandbox_hardening_claimed=false",
        "long_term_cross_session_memory_default_enabled=false",
        "department_rollout_allowed=false",
    ):
        assert invariant in text


def test_backend_prd_records_explicit_gate_register():
    text = read_backend_prd()

    assert "## 4. Gate Register" in text
    assert "Every backend issue must name the smallest gate it is moving." in text

    for gate in (
        "B0-G1",
        "B0-G2",
        "B0-G3",
        "B0-G4",
        "B0-G5",
        "B1-G1",
        "B1-G2",
        "B1-G3",
        "B1-G4",
        "B1-G5",
        "B2-G1",
        "B2-G2",
        "B2-G3",
        "B2-G4",
        "B2-G5",
        "B2-G6",
        "B3-G1",
        "B3-G2",
        "B3-G3",
        "B3-G4",
        "B3-G5",
        "B4-G1",
        "B4-G2",
        "B4-G3",
        "B4-G4",
        "B4-G5",
        "B4-G6",
        "B5-G1",
        "B5-G2",
        "B5-G3",
        "B5-G4",
        "B5-G5",
        "B6-G1",
        "B6-G2",
        "B6-G3",
        "B6-G4",
        "B6-G5",
    ):
        assert gate in text

    for gate_boundary in (
        "Smallest gate",
        "Required closure evidence",
        "Hardening policy",
        "Bounded failure projection",
        "Runtime load evidence",
        "No default raise by implication",
        "Pinned run snapshot",
        "Exact tool permission",
        "Owner signoff and rollback drill",
    ):
        assert gate_boundary in text


def test_backend_prd_preserves_productization_priorities_and_negative_claims():
    text = read_backend_prd()
    compact_text = compact(text)

    assert "The four P0 backend capabilities are:" in text
    for priority in (
        "| P0-1 | Memory/context usable",
        "| P0-2 | Real sandbox usable",
        "| P0-3 | Worker/model-gateway capacity",
        "| P0-4 | Skills management",
    ):
        assert priority in text

    for claim_boundary in (
        "A historical S1 baseline does not prove latest-main readiness.",
        "Session memory smoke does not prove long-term memory.",
        "A successful SDK task in the worker process is not sandbox proof.",
        "One fast run does not prove SDK subagent fanout pressure.",
        "Copying a Skill directory into an image is not Skills management.",
        'Broad "latest allow" decisions do not satisfy exact tool approval.',
        "A generated final document is workflow success, not operations beta.",
        "A verifier-owned Docker-only probe does not prove platform-issued sandbox cleanup or over-limit behavior.",
    ):
        assert claim_boundary in compact_text

    for blocker in (
        "Stale source/runtime labels.",
        "Unreviewed release evidence.",
        "`fake` sandbox evidence used for production sandbox claims.",
        "Capacity default increases without B3 evidence.",
        "SDK subagent fanout outside queue/admission/cost/event/artifact governance.",
        "Long-term memory enabled by default without B1 full acceptance.",
        "Reference code copied without repository, commit/tag, license, provenance, tests, review, and runtime evidence where applicable.",
    ):
        assert blocker in compact_text

    assert "10 concurrent sessions" in compact_text
    assert "Peak 4 Claude Agent SDK subagents per session" in compact_text
    assert "selected internal workflows" in compact_text
    assert "not product beta completion" in compact_text


def test_backend_prd_records_reference_projects_without_delegating_authority():
    text = read_backend_prd()
    compact_text = compact(text)

    for project in (
        "LangGraph",
        "Mem0",
        "Zep",
        "Graphiti",
        "OpenHands",
        "E2B",
        "Daytona",
        "gVisor",
        "Kata Containers",
        "Firecracker",
        "Anthropic Sandbox Runtime",
        "Temporal Python SDK",
        "Celery",
        "Dramatiq",
        "Taskiq",
        "LiteLLM",
        "Portkey",
        "OpenFGA",
        "SpiceDB",
        "Casbin",
        "Open Policy Agent",
        "ContextForge MCP Gateway",
        "MCP Gateway Registry",
        "labspace MCP Gateway",
        "mcp-supergateway",
        "Keycloak",
        "Authentik",
        "Ory Kratos",
        "Backstage",
        "Dify",
        "Open WebUI",
        "LibreChat",
        "AnythingLLM",
        "Langfuse",
        "Phoenix",
        "OpenTelemetry Collector",
        "promptfoo",
        "Ragas",
        "Giskard",
    ):
        assert project in text

    for repo in (
        "langchain-ai/langgraph",
        "mem0ai/mem0",
        "getzep/zep",
        "getzep/graphiti",
        "OpenHands/OpenHands",
        "e2b-dev/E2B",
        "daytonaio/daytona",
        "google/gvisor",
        "kata-containers/kata-containers",
        "firecracker-microvm/firecracker",
        "anthropic-experimental/sandbox-runtime",
        "temporalio/sdk-python",
        "celery/celery",
        "Bogdanp/dramatiq",
        "taskiq-python/taskiq",
        "BerriAI/litellm",
        "Portkey-AI/gateway",
        "backstage/backstage",
        "langgenius/dify",
        "open-webui/open-webui",
        "danny-avila/LibreChat",
        "Mintplex-Labs/anything-llm",
        "openfga/openfga",
        "authzed/spicedb",
        "apache/casbin",
        "open-policy-agent/opa",
        "IBM/mcp-context-forge",
        "agentic-community/mcp-gateway-registry",
        "dockersamples/labspace-mcp-gateway",
        "goodatlas/mcp-supergateway",
        "langfuse/langfuse",
        "Arize-ai/phoenix",
        "open-telemetry/opentelemetry-collector",
        "promptfoo/promptfoo",
        "vibrantlabsai/ragas",
        "Giskard-AI/giskard-oss",
    ):
        assert repo in text

    for authority_boundary in (
        "Reference projects can shape implementation choices, tests, and UI vocabulary.",
        "They do not define ai-platform authority.",
        "Repository names in this inventory were rechecked with `gh repo view` on 2026-06-20",
        "it does not pin commits, approve licenses, approve dependencies, or change any code-adaptation intake state.",
        "Reading a project does not authorize copying code, adding dependencies, or changing runtime architecture.",
        "Any code adaptation or runtime dependency must go through issue, license/provenance review, tests, PR review, and runtime evidence",
        "Unconfirmed project names stay concept-only until a repository, commit or tag, license, and intake level are recorded.",
    ):
        assert authority_boundary in compact_text

    for intake_level in (
        "Concept-only reference",
        "Code adaptation candidate",
        "Runtime dependency proposal",
        "Confirmed repository reference",
        "Unconfirmed concept reference",
    ):
        assert intake_level in text

    assert "### 7.3 Repository Reference Inventory" in text
    assert "### 7.3 Confirmed Repository References" not in text

    for provenance_boundary in (
        "license posture reported by GitHub",
        "Repositories with GitHub license posture `Other`, AGPL/LGPL/copyleft terms, or unknown license posture are concept-only references by default.",
        "not code copying, vendoring, dependency addition, or runtime service introduction without a separate issue",
        "Reference projects that handle credentials, model keys, runtime execution, container launch, filesystem access, network egress, or package installation require elevated supply-chain intake.",
        "Pin a reviewed commit or release tag before copying code, adding a dependency, or deploying an image.",
        "LiteLLM is a useful model-gateway reference, but it is security-sensitive because LiteLLM's [2026-03-24 security update](https://docs.litellm.ai/blog/security-update-march-2026) identified compromised PyPI versions `1.82.7` and `1.82.8`.",
        "this inventory is not a `Confirmed repository reference` intake decision for every row.",
    ):
        assert provenance_boundary in compact_text

    for stage_reference in (
        "| B0 source/auth baseline | Keycloak, Authentik, Ory Kratos |",
        "| B1 memory/context | LangGraph, Mem0, Zep, Graphiti |",
        "| B2 sandbox | OpenHands, E2B, Daytona, gVisor, Kata Containers, Firecracker, Anthropic Sandbox Runtime |",
        "| B3 worker/model gateway | Temporal Python SDK, Celery, Dramatiq, Taskiq, LiteLLM, Portkey |",
        "| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM |",
        "| B5 files/tools/authz | OpenFGA, SpiceDB, Casbin, Open Policy Agent, ContextForge MCP Gateway, MCP Gateway Registry, labspace MCP Gateway, mcp-supergateway |",
        "| B6 observability/quality | Langfuse, Phoenix, OpenTelemetry Collector, promptfoo, Ragas, Giskard |",
    ):
        assert stage_reference in text

    assert "DeerFlow" not in text
    assert "AgentScope" not in text
    assert "new-api" not in text
    assert "C:\\Users" not in text
