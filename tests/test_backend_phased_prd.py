from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PRD = ROOT / "docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md"


def read_backend_prd() -> str:
    return BACKEND_PRD.read_text(encoding="utf-8")


def test_backend_prd_records_stage_gate_and_acceptance_boundaries():
    text = read_backend_prd()
    compact_text = " ".join(text.split())

    for required_section in (
        "### 0.1 Current Evidence Snapshot",
        "### 0.2 Status Transition Contract",
        "## 3. Backend Stage Model",
        "### 3.1 Stage Evidence Matrix",
        "### 3.2 Stage Entry And Exit Gates",
        "### 3.3 Universal Blocking Conditions",
        "### 3.5 Gate Closure Checklist",
        "### 3.6 Negative Acceptance Matrix",
        "## 4. Gate And Acceptance Boundaries",
        "## 6. Reference Code Projects",
    ):
        assert required_section in text

    for stage in ("B0", "B1", "B2", "B3", "B4", "B5", "B6"):
        assert f"| {stage} |" in text
        assert f"| {stage} entry |" in text
        assert f"| {stage} exit |" in text

    for status_label in (
        "`local partial`",
        "`PR ready`",
        "`merged`",
        "`211 verified`",
        "`gate closable`",
    ):
        assert status_label in text

    for boundary in (
        "Docs-only PRs may align the roadmap and acceptance wording, but they cannot",
        "Runtime-affecting backend work becomes `211 verified` only after 211 source",
        "No stage can exit while its linked issue remains open without an evidence comment",
        "No capacity or SDK subagent fanout default can increase from configuration alone",
        "No sandbox claim can use `fake` provider evidence for production acceptance",
        "A happy path cannot close a stage unless the matching denial paths are also represented",
        "Any single passing smoke, any docs-only PR, or historical S1 baseline evidence.",
    ):
        assert boundary in compact_text

    assert "open gaps remain `b1_issue_review_and_closure_evidence` only" in compact_text
    assert "closed gate-boundary gaps include `b1_runtime_evidence_review_against_merged_source`" in compact_text
    assert "open gaps remain `b1_issue_review_and_closure_evidence` and `b1_runtime_evidence_review_against_merged_source`" not in compact_text
    assert "stale or open merged-source runtime review" in compact_text

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
    ):
        assert phrase in compact_text

    for deliverable in (
        "| B1 | Memory policy and context-pack contracts; memory workflow verifier; reviewed 211 smoke evidence; rollback/export notes. |",
        "| B2 | Real sandbox provider profile; lease/callback/egress/cleanup tests; 211 sandbox smoke evidence. |",
        "| B3 | Capacity profile definition; bounded-load harness; seven-gate 211 evidence; Admin Runtime backpressure projection. |",
        "| B4 | Skill upload/version/release/rollback contracts; dependency evidence contract; reviewed skill-run smoke. |",
    ):
        assert deliverable in text


def test_backend_prd_records_reference_projects_without_delegating_authority():
    text = read_backend_prd()
    compact_text = " ".join(text.split())

    for project in (
        "LangGraph",
        "Mem0",
        "Zep",
        "OpenHands",
        "E2B",
        "Daytona",
        "Temporal Python SDK",
        "Celery",
        "LiteLLM",
        "Portkey",
        "OpenFGA",
        "Open Policy Agent",
        "Langfuse",
        "Phoenix",
        "OpenTelemetry Collector",
        "Backstage",
        "Dify",
        "Open WebUI",
        "Dramatiq",
        "Taskiq",
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
    ):
        assert authority_boundary in compact_text

    for category in (
        "| B1 memory/context | LangGraph, Mem0, Zep | Memory/checkpoint model, memory UX, provenance, delete/update semantics. |",
        "| B2 sandbox | OpenHands, E2B, Daytona | Sandbox lifecycle, workspace isolation, command execution, artifact return, cancellation ergonomics. |",
        "| B3 capacity/model gateway | Temporal, Celery, Dramatiq, Taskiq, LiteLLM, Portkey | Durable retry vocabulary, worker scaling, provider limits, budgets, spend tracking, fallback/backpressure. |",
        "| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM | Catalog, release workflow, skill/app marketplace UX, slash/tool discovery patterns. |",
    ):
        assert category in text

    assert "DeerFlow" not in text
    assert "AgentScope" not in text
    assert "new-api" not in text
    assert "C:\\Users" not in text
