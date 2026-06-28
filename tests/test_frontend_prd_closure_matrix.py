from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/frontend/prd-frontend-closure-matrix.md"
ABSORPTION_PRD = (
    ROOT / "docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md"
)
CHAT_PARITY_PRD = (
    ROOT / "docs/superpowers/specs/2026-06-19-ai-platform-chat-experience-parity-prd.md"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_frontend_prd_closure_matrix_records_single_pr_evidence_boundary():
    text = read(MATRIX)
    compact = " ".join(text.split())

    assert "Single active closure PR" in text
    assert "Refs #81" in text
    assert "must not use `Closes #81`" in text
    assert "PR #262" in text
    assert "c9866a8919bbe0dddce40320538717a691c79375" in text
    assert "merged-main 211 verified" in text
    assert "not a full-program `gate closable` claim" in text
    assert "Formal GitHub review metadata is still absent" in text
    assert "/home/xinlin.jiang/ai-platform-frontend-releases/evidence/pr-262-c9866a8919bb/smoke-summary.json" in text
    assert "/home/xinlin.jiang/ai-platform-frontend-releases/evidence/pr-262-c9866a8919bb/screenshots.zip" in text
    assert "502fefa62e4ce310a9c359afebbacaad573cdc64a3df31269150280ea5762855" in text
    assert "8b4ac17101cd7f33130fd1dc8139845f744130cc3b92fc4148165f3259902bb6" in text
    assert "Credentials are read only from gitignored environment files" in text
    assert "AI_PLATFORM_LOGIN_PASSWORD" in text
    assert "redacted" in text

    for phase in ("Phase 1A", "Phase 1B", "Phase 1C"):
        assert phase in text
    assert "Phase 2 backend-backed expansion" in text
    assert "not a frontend-only closure item" in text
    assert "department/group Skill marketplace policy writes" in compact
    assert "MCP lifecycle and policy assignment" in compact
    assert "users/roles/departments, model admin, settings, and notifications" in compact


def test_frontend_prd_closure_matrix_maps_prd_done_items_without_local_paths_or_secrets():
    text = read(MATRIX)
    absorption_prd = read(ABSORPTION_PRD)
    chat_prd = read(CHAT_PARITY_PRD)

    for required in (
        "Projection audit",
        "frontend/web/scripts/prd-closure-browser-smoke.mjs",
        "pnpm run ci:verify",
        "pnpm run smoke:prd-closure",
        "python -m compileall -q app tools scripts",
        "git diff --check",
        "ordinary workflow",
        "admin workflow",
        "company-account browser login",
        "slash command menu",
        "$ Skills selector",
        "selected Skill chip",
        "MCP selector evidence",
        "file upload affordance",
        "forbidden shared route",
        "/chat",
        "/apps",
        "/skills",
        "/marketplace",
        "/roles",
        "/mcp",
        "/persona",
        "/files",
        "/channels",
        "/settings",
        "/shared/smoke-denied",
    ):
        assert required in text

    assert "Phase 1 ordinary users can login" in absorption_prd
    assert "Phase 1B is done only when" in chat_prd
    assert "Phase 1C is done only when" in chat_prd
    assert "The full program is done only when Phase 2 backend contracts" in absorption_prd
    assert "The frontend experience is `gate closable` only after Phase 1B, Phase 1C" in chat_prd

    forbidden_fragments = (
        "C:\\",
        "C:/",
        "zxsw",
        "raw password",
        "\nCloses #81",
        "\nFixes #81",
        "full program is gate closable",
    )
    for fragment in forbidden_fragments:
        assert fragment not in text
