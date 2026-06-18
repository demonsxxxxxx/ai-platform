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
    ):
        assert authority_boundary in compact_text

    assert "DeerFlow" not in text
    assert "AgentScope" not in text
    assert "new-api" not in text
    assert "C:\\Users" not in text
