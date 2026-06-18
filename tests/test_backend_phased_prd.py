from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PRD = ROOT / "docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md"


def read_backend_prd() -> str:
    return BACKEND_PRD.read_text(encoding="utf-8")


def test_backend_prd_records_stage_gate_and_acceptance_boundaries():
    text = read_backend_prd()

    for required_section in (
        "## 3. Backend Stage Model",
        "### 3.1 Stage Evidence Matrix",
        "### 3.2 Stage Entry And Exit Gates",
        "### 3.3 Universal Blocking Conditions",
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
    ):
        assert boundary in text


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
    ):
        assert project in text

    for authority_boundary in (
        "External code projects are references, not product authority.",
        "Any imported code requires source pinning, license/provenance review, and an adaptation plan.",
        "Do not import a reference project's tenant, RBAC, memory, sandbox, or release authority wholesale.",
        "Backend authority remains ai-platform.",
        "No reference project is a dependency decision until an issue names the source, license, imported files, adaptation boundary, and verification plan.",
    ):
        assert authority_boundary in compact_text

    for category in (
        "| B1 memory/context | LangGraph, Mem0, Zep | Memory/checkpoint model, memory UX, provenance, delete/update semantics. |",
        "| B2 sandbox | OpenHands, E2B, Daytona | Sandbox lifecycle, workspace isolation, command execution, artifact return, cancellation ergonomics. |",
        "| B3 capacity/model gateway | Temporal, Celery, LiteLLM, Portkey | Durable retry vocabulary, worker scaling, provider limits, budgets, spend tracking, fallback/backpressure. |",
        "| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM | Catalog, release workflow, skill/app marketplace UX, slash/tool discovery patterns. |",
    ):
        assert category in text

    assert "DeerFlow" not in text
    assert "AgentScope" not in text
    assert "new-api" not in text
    assert "C:\\Users" not in text
