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
    assert "PR #264" in text
    assert "94f0b20fcf441fdcbde730a1edafb2c1dbdcbf59" in text
    assert "merged-main 211 verified" in text
    assert "not a full-program `gate closable` claim" in text
    assert "Formal GitHub review metadata is still absent" in text
    assert "/home/xinlin.jiang/frontend-releases/20260628-94f0b20/ai-platform-frontend-94f0b20-dist.tar.gz" in text
    assert "/home/xinlin.jiang/frontend-releases/evidence/pr264-94f0b20-211-browser-smoke/211-smoke-94f0b20.json" in text
    assert "/home/xinlin.jiang/frontend-releases/evidence/pr264-94f0b20-211-browser-smoke-evidence.tar.gz" in text
    assert "e71185e112f7fc92b89fba262e9f1ba5bdc0c170c2357c7f2d28c8af0122134b" in text
    assert "158353a2ed6879c5fd7a062c445e1ca23f227cf8febe61064b89b37def6f050d" in text
    assert "loading for skills" in text
    assert "Right context panel" in text
    assert "shareChannelFailClosedSource.test.ts" in text
    assert "governed channel import without fake import success" in text
    assert "governancePhase1Closure.test.ts" in text
    assert "fail-closed group availability toggle UI" in text
    assert "MCP lifecycle governance without raw controls" in text
    assert "frontendPhase1ClosureContract.test.ts" in text
    assert "/shared/:shareId" in text
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
        "raw password",
        "AI_PLATFORM_LOGIN_PASSWORD=",
        "password:",
        "\nCloses #81",
        "\nFixes #81",
        "full program is gate closable",
    )
    for fragment in forbidden_fragments:
        assert fragment not in text
