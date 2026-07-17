import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("workbench shell is owned by the local LibreChat shell layer", () => {
  const libreShell = read("src/librechat-ui/Shell.tsx");
  const legacyShell = read("src/components/librechatShell/LibreChatShell.tsx");
  const workbenchShell = read("src/components/workbench/WorkbenchShell.tsx");
  const chatApp = read("src/components/layout/AppContent/ChatAppContent.tsx");

  assert.match(libreShell, /data-librechat-shell="phase1"/);
  assert.match(libreShell, /data-workbench-region="thread"/);
  assert.match(libreShell, /data-workbench-region="composer"/);
  assert.match(libreShell, /data-workbench-region="context"/);
  assert.match(legacyShell, /\.\.\/\.\.\/librechat-ui/);
  assert.match(workbenchShell, /LibreChatShell/);
  assert.match(workbenchShell, /librechat-ui\/Shell/);
  assert.match(chatApp, /WorkbenchShell/);
});

test("surface tokens expose one neutral chat canvas", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const baseCss = read("src/styles/base.css");

  assert.match(surface, /libreChatSurface/);
  assert.match(baseCss, /--theme-workbench-canvas:\s*#ffffff;/);
  assert.match(baseCss, /--theme-workbench-panel:\s*#ffffff;/);
  assert.match(baseCss, /--theme-bg-card:\s*#ffffff;/);
  assert.match(baseCss, /--theme-bg-sidebar:\s*#f7f7f8;/);
  assert.match(baseCss, /--theme-sidebar-panel:\s*#f7f7f8;/);
  assert.match(baseCss, /--theme-sidebar-rail:\s*#f7f7f8;/);
  assert.doesNotMatch(baseCss, /--theme-workbench-canvas:\s*#e5e8ed;/);
  assert.doesNotMatch(baseCss, /--theme-workbench-canvas:\s*#e5e8ed;/);
});

test("sidebar transplants LibreChat rail geometry and mobile close behavior", () => {
  const sessionSidebar = read("src/components/panels/SessionSidebar.tsx");
  const list = read(
    "src/components/panels/SidebarParts/SessionListContent.tsx",
  );
  const rail = read("src/components/panels/SidebarParts/SidebarRail.tsx");
  const surface = read("src/librechat-ui/surface.ts");

  assert.match(sessionSidebar, /LIBRECHAT_SHELL_GEOMETRY/);
  assert.match(sessionSidebar, /librechat-ui\/surface/);
  assert.match(
    sessionSidebar,
    /--sidebar-rail-width":\s*`\$\{LIBRECHAT_SHELL_GEOMETRY\.railWidthPx\}px`/,
  );
  assert.match(
    sessionSidebar,
    /--sidebar-width":\s*`\$\{LIBRECHAT_SHELL_GEOMETRY\.expandedMinWidthPx\}px`/,
  );
  assert.match(sessionSidebar, /keydown/);
  assert.match(sessionSidebar, /Escape/);
  assert.match(sessionSidebar, /data-librechat-mobile-sidebar/);
  assert.match(list, /LibreChatPanelSection/);
  assert.match(list, /librechat-ui\/Panel/);
  assert.match(list, /data-librechat-expanded-panel/);
  assert.match(rail, /LibreChatRailButton/);
  assert.match(rail, /librechat-ui\/Rail/);
  assert.match(rail, /data-librechat-rail/);
  assert.match(surface, /expandedMinWidthPx:\s*288/);
});

test("composer and right panel expose LibreChat-style regions without backend authority imports", () => {
  const sidePanel = read("src/librechat-ui/SidePanel.tsx");
  const legacySidePanel = read(
    "src/components/librechatShell/LibreChatSidePanel.tsx",
  );
  const rightPanel = read("src/components/workbench/WorkbenchRightPanel.tsx");
  const chatInput = read("src/components/chat/ChatInput.tsx");
  const composer = read("src/librechat-ui/Composer.tsx");
  const chatCss = read("src/styles/chat.css");

  assert.match(sidePanel, /data-librechat-side-panel/);
  assert.match(sidePanel, /data-librechat-context-overview/);
  assert.match(
    sidePanel,
    /<section\b[^>]*aria-labelledby="librechat-context-overview-label"[^>]*>[\s\S]*?<h2\b[^>]*id="librechat-context-overview-label"[^>]*>[\s\S]*?workbench\.workspaceContext/,
  );
  assert.match(sidePanel, /section="run"/);
  assert.doesNotMatch(sidePanel, /<button\b/);
  assert.doesNotMatch(sidePanel, /data-librechat-side-tab=/);
  assert.doesNotMatch(sidePanel, /role="tablist"|role="tab"/);
  assert.doesNotMatch(sidePanel, /activeTab|setActiveTab/);
  assert.match(legacySidePanel, /\.\.\/\.\.\/librechat-ui/);
  assert.match(rightPanel, /LibreChatSidePanel/);
  assert.match(rightPanel, /librechat-ui\/SidePanel/);
  assert.match(chatInput, /LibreChatComposerFrame/);
  assert.match(chatInput, /LibreChatComposerRegion region="chips"/);
  assert.match(chatInput, /LibreChatComposerRegion region="textarea"/);
  assert.match(chatInput, /LibreChatComposerRegion region="toolbar"/);
  assert.match(composer, /data-librechat-composer="phase1"/);
  assert.match(composer, /data-librechat-composer-region=\{region\}/);
  assert.match(chatCss, /\.librechat-composer-shell/);
  assert.doesNotMatch(
    sidePanel + rightPanel + chatInput,
    /librechat-data-provider|useRecoilState|~\/Providers|~\/store/,
  );
});
