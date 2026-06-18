from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PRD = ROOT / "docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md"


def read_backend_prd() -> str:
    return BACKEND_PRD.read_text(encoding="utf-8")


def test_backend_prd_records_stage_gate_and_acceptance_boundaries():
    text = read_backend_prd()
    compact_text = " ".join(text.split())

    for required_section in (
        "### 0.1 Live Status Contract And Last Calibration",
        "### 0.2 Status Transition Contract",
        "## 3. Backend Stage Model",
        "### 3.1 Stage Evidence Matrix",
        "### 3.2 Stage Entry And Exit Gates",
        "### 3.3 Universal Blocking Conditions",
        "### 3.5 Gate Closure Checklist",
        "### 3.6 Negative Acceptance Matrix",
        "### 3.7 Productization Gate Map",
        "## 4. Gate And Acceptance Boundaries",
        "### 5.0 Stage Requirement Format",
        "## 6. Reference Code Projects",
        "### 6.1 Reference Intake Levels",
    ):
        assert required_section in text

    for stage in ("B0", "B1", "B2", "B3", "B4", "B5", "B6"):
        assert f"| {stage} |" in text
        assert f"| {stage} entry |" in text
        assert f"| {stage} exit |" in text

    for status_label in (
        "`local partial`",
        "`PR ready`",
        "`reviewed`",
        "`merged`",
        "`211 verified`",
        "`gate closable`",
    ):
        assert status_label in text

    for boundary in (
        "Docs-only PRs may align the roadmap and acceptance wording, but they cannot",
        "Runtime-affecting backend work becomes `211 verified` only after 211 source",
        "`PR ready` becomes `reviewed` only after independent review is recorded",
        "`reviewed` becomes `merged` only after the PR is merged to main",
        "No stage can exit while its linked issue remains open without an evidence comment",
        "No capacity or SDK subagent fanout default can increase from configuration alone",
        "No sandbox claim can use `fake` provider evidence for production acceptance",
        "A happy path cannot close a stage unless the matching denial paths are also represented",
        "Any single passing smoke, any docs-only PR, or historical S1 baseline evidence.",
    ):
        assert boundary in compact_text

    assert "Live readiness output, gate status, release evidence records, and GitHub issue state are the current-state sources." in compact_text
    assert "Last calibration rows are informational and must not be used as gate closure evidence after source/runtime state changes." in compact_text
    assert "Current status must be refreshed before reporting `211 verified` or `gate closable`." in compact_text
    assert "Do not hard-code a single source commit as the permanent PRD truth." in compact_text
    assert "open gaps currently include `b1_issue_review_and_closure_evidence` and may include `b1_runtime_evidence_review_against_merged_source` whenever runtime-affecting source changes land after the reviewed smoke subject." in compact_text
    assert "closed gate-boundary gaps include `b1_runtime_evidence_review_against_merged_source`" not in compact_text
    assert "open gaps remain `b1_issue_review_and_closure_evidence` only" not in compact_text
    assert "stale or open merged-source runtime review" in compact_text
    assert "`python tools/b2_sandbox_readiness.py --format json`" in text
    assert "`status=local_contract_ready_runtime_smoke_required`" in text
    assert "`status_label=local partial`" in text
    assert "`b2_211_real_sandbox_smoke`" in text
    assert "`b2_reviewed_release_evidence`" in text
    assert "`b2_issue_review_and_closure_evidence`" in text
    assert "currently enforces `admin_or_allowlist_only` and `hardening.evidence_class`" in compact_text
    assert "still requires separate verifier/generator work before resource-limit, egress-policy, security-option, and rollback-assumption evidence can be treated as current verifier output" in compact_text
    assert "mirrors the existing 211 sandbox verifier boundary" not in text

    for checklist in (
        "| B0 | S2-0/latest-main source-authority issue links the target source",
        "| B1 | Memory/context issue names the selected workflow",
        "| B2 | Sandbox issue names provider, limits, egress, callback, cleanup, and rollback assumptions.",
        "| B3 | Capacity issue names the profile, starting with 10 sessions x peak 4 SDK subagents",
        "| B4 | Skill lifecycle issue names upload/version/release/rollback/dependency evidence",
        "| B5 | File/artifact/tool issue names workflow family, namespace, exact permission binding",
        "| B6 | Operations-beta issue names owner, workflow, SLO, cost budget, quality gate",
    ):
        assert checklist in text


def test_backend_prd_records_post_poc_productization_priorities_and_deliverables():
    text = read_backend_prd()
    compact_text = " ".join(text.split())

    for phrase in (
        "The project is past the POC proving phase.",
        "Productization means turning the accepted Foundation Alpha baseline into repeatable, supportable backend capabilities.",
        "P0-1 Memory/context usable",
        "P0-2 Real sandbox usable",
        "P0-3 Worker/model-gateway capacity evidence",
        "P0-4 Skills management and release governance",
        "### 3.4 Stage Deliverables",
        "The next backend issue chain should stay narrow and evidence-first:",
        "The initial backend capacity target is deliberately small:",
        "Peak 4 Claude Agent SDK subagents per session for selected workflows.",
        "The backend becomes product-beta ready only when it supports a named internal workflow",
        "B2 can now be tracked through `tools/b2_sandbox_readiness.py`, but that rollup is source-level only.",
        "The B2 source contract is `local partial` until 211 Docker/equivalent evidence is generated",
        "B3 is a measurement stage before it is a configuration stage.",
        "B4 is accepted only when a Skill run can be explained from release decision to immutable run snapshot to used-skill evidence.",
        "B6 is an operations beta gate, not a dashboard-only gate.",
    ):
        assert phrase in compact_text

    for deliverable in (
        "| B1 | Memory policy and context-pack contracts; memory workflow verifier; reviewed current-source 211 smoke evidence; rollback/export notes. |",
        "| B2 | Real sandbox provider profile; lease/callback/egress/cleanup tests; 211 sandbox smoke evidence. |",
        "| B3 | Capacity profile definition; bounded-load harness; seven-gate 211 evidence; Admin Runtime backpressure projection. |",
        "| B4 | Skill upload/version/release/rollback contracts; dependency evidence contract; reviewed skill-run smoke. |",
    ):
        assert deliverable in text

    for gate_map in (
        "| Productization lane | Backend stage | Primary blocking question | First accepted proof |",
        "| Memory usable | B1 | Can a selected workflow use memory/context without private leakage or uncontrolled long-term recall? | Reviewed 211 memory-enabled document workflow smoke for merged source plus export/delete/redaction boundaries. |",
        "| Real sandbox usable | B2 | Can governed SDK skill execution run in a real isolated provider instead of `fake`? | Reviewed 211 Docker/equivalent smoke with lease, callback, artifact, cancel, cleanup, orphan, and redaction evidence. |",
        "| Capacity usable | B3 | Can 10 sessions x peak 4 SDK subagents run without queue/model/sandbox/cost collapse? | Operator-reviewed bounded-load evidence before any default increase. |",
        "| Skills usable | B4 | Can Skills be uploaded, versioned, reviewed, released, pinned, run, audited, and rolled back? | Reviewed Skill lifecycle evidence and 211 run with used-skill artifacts. |",
    ):
        assert gate_map in text


def test_backend_prd_records_reference_projects_without_delegating_authority():
    text = read_backend_prd()
    compact_text = " ".join(text.split())

    for project in (
        "LangGraph",
        "Mem0",
        "Zep",
        "Graphiti",
        "OpenHands",
        "E2B",
        "Daytona",
        "Temporal Python SDK",
        "Celery",
        "LiteLLM",
        "Portkey",
        "OpenFGA",
        "SpiceDB",
        "Open Policy Agent",
        "Keycloak",
        "Authentik",
        "Ory Kratos",
        "Langfuse",
        "Phoenix",
        "OpenTelemetry Collector",
        "promptfoo",
        "Ragas",
        "Giskard",
        "MCP Gateway",
        "supergateway",
        "Backstage",
        "Dify",
        "Open WebUI",
        "Dramatiq",
        "Taskiq",
        "Casbin",
    ):
        assert project in text

    for authority_boundary in (
        "External code projects are references, not product authority.",
        "Any imported code requires source pinning, license/provenance review, and an adaptation plan.",
        "Do not import a reference project's tenant, RBAC, memory, sandbox, or release authority wholesale.",
        "Backend authority remains ai-platform.",
        "No reference project is a dependency decision until an issue names the source, license, imported files, adaptation boundary, and verification plan.",
        "Reference use must follow this intake gate:",
        "Do not add a runtime dependency, side service, or hosted SaaS call without a separate architecture issue, security review, deployment plan, and rollback boundary.",
        "Concept-only reference",
        "Code adaptation candidate",
        "Runtime dependency proposal",
        "References can explain implementation choices, but they cannot close ai-platform gates.",
    ):
        assert authority_boundary in compact_text

    for category in (
        "| B0 source/auth baseline | Keycloak, Authentik, Ory Kratos | OIDC/session, group/role mapping, admin login, and enterprise identity integration patterns. |",
        "| B1 memory/context | LangGraph, Mem0, Zep, Graphiti | Memory/checkpoint model, memory UX, provenance, temporal memory, delete/update semantics. |",
        "| B2 sandbox | OpenHands, E2B, Daytona | Sandbox lifecycle, workspace isolation, command execution, artifact return, cancellation ergonomics. |",
        "| B3 capacity/model gateway | Temporal, Celery, Dramatiq, Taskiq, LiteLLM, Portkey | Durable retry vocabulary, worker scaling, provider limits, budgets, spend tracking, fallback/backpressure. |",
        "| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM | Catalog, release workflow, skill/app marketplace UX, slash/tool discovery patterns. |",
        "| B5 authorization/files/tools | OpenFGA, SpiceDB, Casbin, Open Policy Agent, MCP Gateway, supergateway | Relationship-based ACLs, role/policy enforcement, policy bundles, gateway/tool catalog routing, decision logs, deny-path test matrices. |",
        "| B6 observability/ops | Langfuse, Phoenix, OpenTelemetry Collector, promptfoo, Ragas, Giskard | Trace vocabulary, eval runs, token/cost views, metrics/traces/log export, quality regression, and redaction patterns. |",
    ):
        assert category in text

    for shortlist in (
        "| Priority | Stage | Reference code to inspect first | Why it is relevant now |",
        "| 1 | B2 | OpenHands sandbox runtime, E2B Code Interpreter execution API, Daytona workspace lifecycle |",
        "| 2 | B3 | LiteLLM proxy budgets/rate limits, Temporal/Celery worker semantics |",
        "| 3 | B4 | Backstage catalog metadata, Dify/Open WebUI tool or app management, LibreChat tool UI contracts |",
    ):
        assert shortlist in text

    assert "The highest-priority B2 risk is real sandbox evidence" in text
    assert "Current open backend issue is real sandbox evidence" not in text

    assert "DeerFlow" not in text
    assert "AgentScope" not in text
    assert "new-api" not in text
    assert "C:\\Users" not in text
