"""Fail-closed and executable-entry contracts for the bounded browser fixture."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend" / "web"


def test_session_preview_command_typechecks_fixture_before_browser_execution():
    scripts = json.loads((WEB / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts["smoke:session-preview"] == (
        "tsc -p scripts/tsconfig.session-preview.json && "
        "node scripts/session-preview-browser-smoke.mjs"
    )
    config = json.loads((WEB / "scripts/tsconfig.session-preview.json").read_text())
    assert config["extends"] == "../tsconfig.json"
    assert "fixtures/session-preview.tsx" in config["include"]


def test_session_preview_fixture_uses_real_components_without_network_fallback():
    fixture = (WEB / "scripts/fixtures/session-preview.tsx").read_text(encoding="utf-8")
    for owner in (
        "components/documents/previews/ExcalidrawPreview",
        "components/panels/SessionSidebar",
        "components/skeletons/SidebarSkeleton",
        "hooks/useSidebarPreference",
        "contexts/LobeUiProvider",
    ):
        assert owner in fixture
    assert "window.fetch = async" in fixture
    assert "unstubbed_fetch:" in fixture
    assert "originalFetch" not in fixture
    assert "sessionApi.listAuthoritative = async () =>" in fixture
    assert "unexpected_default_session_list" in fixture
    assert "new Response(null, { status: 204 })" in fixture
    assert "Synthetic deletion failure" in fixture
    assert "/scripts/fixtures/session-preview" not in (WEB / "src/App.tsx").read_text()


def test_session_preview_runner_requires_rendered_matrix_and_retains_failures():
    runner = (WEB / "scripts/session-preview-browser-smoke.mjs").read_text(encoding="utf-8")
    assert 'from "./browser-smoke-harness.mjs"' in runner
    assert 'for (const theme of ["light", "dark"])' in runner
    assert 'width: 1440, height: 900' in runner
    assert 'width: 390, height: 844' in runner
    assert 'Input.dispatchMouseEvent' in runner
    assert 'Input.dispatchKeyEvent' in runner
    assert 'captureScreenshot' in runner
    assert 'Fetch.enable' in runner
    assert 'Fetch.failRequest' in runner
    assert 'config.server.proxy = undefined' in runner
    assert '!url.pathname.startsWith("/api")' in runner
    assert 'dirty, sourceHashes' in runner
    assert 'if (failures.length) process.exitCode = 1' in runner
    assert 'await server.close()' in runner
    assert 'await browser.close()' in runner
    assert '--no-sandbox' not in runner
    assert 'git", ["rev-parse", "HEAD"]' in runner
