import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("workbench shell is owned by the local LibreChat shell layer", () => {
  const libreShell = read("src/components/librechatShell/LibreChatShell.tsx");
  const workbenchShell = read("src/components/workbench/WorkbenchShell.tsx");
  const chatApp = read("src/components/layout/AppContent/ChatAppContent.tsx");

  assert.match(libreShell, /data-librechat-shell="phase1"/);
  assert.match(libreShell, /data-workbench-region="thread"/);
  assert.match(libreShell, /data-workbench-region="composer"/);
  assert.match(libreShell, /data-workbench-region="context"/);
  assert.match(workbenchShell, /LibreChatShell/);
  assert.match(chatApp, /WorkbenchShell/);
});

test("surface tokens expose one neutral chat canvas", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const baseCss = read("src/styles/base.css");

  assert.match(surface, /libreChatSurface/);
  assert.match(baseCss, /--theme-workbench-canvas:\s*#e5e8ed;/);
  assert.match(baseCss, /--theme-workbench-panel:\s*#f3f4f6;/);
  assert.match(baseCss, /--theme-bg-card:\s*#f8fafc;/);
  assert.doesNotMatch(baseCss, /--theme-bg-card:\s*#ffffff;/);
});

test("sidebar transplants LibreChat rail geometry and mobile close behavior", () => {
  const sessionSidebar = read("src/components/panels/SessionSidebar.tsx");
  const list = read(
    "src/components/panels/SidebarParts/SessionListContent.tsx",
  );
  const rail = read("src/components/panels/SidebarParts/SidebarRail.tsx");
  const surface = read("src/components/librechatShell/libreChatSurface.ts");

  assert.match(sessionSidebar, /LIBRECHAT_SHELL_GEOMETRY/);
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
  assert.match(list, /data-librechat-expanded-panel/);
  assert.match(rail, /LibreChatRailButton/);
  assert.match(rail, /data-librechat-rail/);
  assert.match(surface, /expandedMinWidthPx:\s*360/);
});
