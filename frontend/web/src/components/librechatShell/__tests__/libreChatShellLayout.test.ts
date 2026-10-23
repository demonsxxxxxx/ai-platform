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
  assert.match(surface, /expandedMinWidthPx:\s*272/);
});

test("composer and right panel expose LibreChat-style regions without backend authority imports", () => {
  const sidePanel = read("src/librechat-ui/SidePanel.tsx");
  const legacySidePanel = read(
    "src/components/librechatShell/LibreChatSidePanel.tsx",
  );
  const rightPanel = read("src/components/workbench/WorkbenchRightPanel.tsx");
  const driveBrowser = read(
    "src/components/workbench/ProfileDriveWorkspaceBrowser.tsx",
  );
  const chatInput = read("src/components/chat/ChatInput.tsx");
  const composer = read("src/components/chat/assistant-ui/elements/composer.tsx");
  const chatCss = read("src/styles/chat.css");

  assert.match(sidePanel, /data-librechat-side-panel/);
  assert.match(sidePanel, /data-librechat-context-overview/);
  assert.doesNotMatch(sidePanel, /workbench\.workspaceContext|librechat-context-overview-label/);
  assert.doesNotMatch(sidePanel, /data-librechat-context-section="files"/);
  assert.doesNotMatch(sidePanel, /section="run"/);
  assert.doesNotMatch(sidePanel, /data-librechat-side-tab=/);
  assert.doesNotMatch(sidePanel, /role="tablist"|role="tab"/);
  assert.doesNotMatch(sidePanel, /activeTab|setActiveTab/);
  assert.doesNotMatch(sidePanel, /filesStatus|SessionFilesList/);
  assert.match(driveBrowser, /data-librechat-context-section="files"/);
  assert.match(driveBrowser, /role="tablist"/);
  assert.match(driveBrowser, /role="tab"/);
  assert.match(driveBrowser, /activeSource|setActiveSource/);
  assert.match(legacySidePanel, /\.\.\/\.\.\/librechat-ui/);
  assert.match(rightPanel, /LibreChatSidePanel/);
  assert.match(rightPanel, /librechat-ui\/SidePanel/);
  assert.match(chatInput, /<ComposerBar/);
  assert.match(chatInput, /data-slot="composer-chips"/);
  assert.match(chatInput, /data-slot="composer-input"/);
  assert.match(chatInput, /data-slot="composer-toolbar-region"/);
  assert.match(composer, /ComposerPrimitive.Root/);
  assert.match(composer, /data-slot="composer-bar"/);
  assert.match(chatCss, /\.chat-input-shell/);
  assert.doesNotMatch(
    sidePanel + rightPanel + chatInput,
    /librechat-data-provider|useRecoilState|~\/Providers|~\/store/,
  );
});

test("chat keeps aligned gutters and a compact composer without changing its controls", () => {
  const surface = read("src/librechat-ui/surface.ts");
  const chatInput = read("src/components/chat/ChatInput.tsx");
  const composer = read("src/components/chat/assistant-ui/elements/composer.tsx");
  const chatCss = read("src/styles/chat.css");

  assert.match(surface, /threadBody: "[^"]*px-2[^\n]*sm:px-4/);
  assert.match(surface, /composer: "[^"]*px-2[^\n]*sm:px-4/);
  assert.doesNotMatch(chatInput, /chat-input-shell sm:px-2/);
  assert.match(chatInput, /className="flex flex-col gap-0"[\s\S]{0,150}data-slot="composer-ordinary-controls"/);
  assert.match(composer, /min-h-\[48px\]/);
  assert.match(chatCss, /\.chat-input-container\s*\{[^}]*min-height:\s*104px/);
  assert.match(chatCss, /max-height:\s*min\(52dvh, 420px\)/);
  assert.match(chatCss, /env\(safe-area-inset-bottom\)/);
});

test("empty chat keeps the first task prompt reachable and context uses one quiet divider", () => {
  const welcome = read("src/components/chat/WelcomePage.tsx");
  const welcomeCss = read("src/styles/welcome.css");
  const surface = read("src/librechat-ui/surface.ts");
  const sidePanel = read("src/librechat-ui/SidePanel.tsx");

  assert.match(welcome, /welcome-chat-start/);
  assert.match(welcome, /data-chat-start-surface/);
  assert.match(welcomeCss, /\.welcome-root\.welcome-chat-start\s*\{[\s\S]*?justify-content:\s*flex-start/);
  assert.match(welcomeCss, /padding-block-start:\s*clamp\(2rem, 14vh, 9rem\)/);
  assert.match(welcomeCss, /@media \(max-width: 639px\)[\s\S]*?padding-block-start:\s*clamp\(1\.25rem, 7vh, 2\.5rem\)/);
  assert.match(welcomeCss, /html\[data-mobile-keyboard="true"\] \.welcome-root\.welcome-chat-start[\s\S]*?justify-content:\s*flex-start/);
  assert.doesNotMatch(welcomeCss, /\.welcome-root\.welcome-chat-start[\s\S]{0,300}h-screen|\.welcome-root\.welcome-chat-start[\s\S]{0,300}min-height:\s*100vh/);

  const shell = read("src/librechat-ui/Shell.tsx");
  assert.match(surface, /context:[\s\S]*?min-h-0 min-w-0[\s\S]*?border-l/);
  assert.doesNotMatch(surface, /context:[\s\S]*?fixed inset-y-0/);
  assert.match(shell, /<DrawerPopup placement="right" width="min\(22rem, calc\(100vw - 2rem\)\)"/);
  assert.match(shell, /rightPanel && contextOpen && desktop/);
  assert.match(surface, /workspaceWithContext:[\s\S]*?minmax\(18rem,20rem\)/);
  assert.doesNotMatch(sidePanel, /workbenchSurface\.secondaryPanel/);
  assert.match(sidePanel, /className="min-h-0 flex-1 overflow-y-auto pr-1"/);
});
