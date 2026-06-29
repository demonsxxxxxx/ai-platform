import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

const root = process.cwd();
const uiRoot = join(root, "src/librechat-ui");

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

function walkFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const absolute = join(dir, entry);
    if (statSync(absolute).isDirectory()) return walkFiles(absolute);
    return [absolute];
  });
}

test("librechat-ui is a pinned pure UI upstream module", () => {
  for (const path of [
    "src/librechat-ui/source.ts",
    "src/librechat-ui/adapter.ts",
    "src/librechat-ui/surface.ts",
    "src/librechat-ui/Shell.tsx",
    "src/librechat-ui/Rail.tsx",
    "src/librechat-ui/Panel.tsx",
    "src/librechat-ui/SidePanel.tsx",
    "src/librechat-ui/Composer.tsx",
    "src/librechat-ui/Selector.tsx",
    "src/librechat-ui/Chips.tsx",
    "src/librechat-ui/CommandMenu.tsx",
    "src/librechat-ui/StateSurface.tsx",
    "src/librechat-ui/index.ts",
    "src/librechat-ui/NOTICE.md",
  ]) {
    assert.equal(existsSync(join(root, path)), true, `${path} should exist`);
  }

  const source = read("src/librechat-ui/source.ts");
  assert.match(source, /https:\/\/github\.com\/danny-avila\/LibreChat/);
  assert.match(source, /9e74cc0e57b395926122bd4062c1fcedc48ed465/);
  assert.match(source, /MIT/);
  assert.match(source, /client\/src\/components\/UnifiedSidebar/);
  assert.match(source, /client\/src\/components\/Chat\/Input/);
  assert.match(source, /client\/src\/components\/SidePanel/);
});

test("librechat-ui exposes an ai-platform-owned adapter seam", () => {
  const adapter = read("src/librechat-ui/adapter.ts");

  assert.match(adapter, /export interface ChatWorkbenchAdapter/);
  assert.match(adapter, /sessions:\s*SessionSummary\[\]/);
  assert.match(adapter, /messages:\s*ChatMessage\[\]/);
  assert.match(adapter, /selectedSkillChips:\s*ComposerChip\[\]/);
  assert.match(adapter, /selectedMcpChips:\s*ComposerChip\[\]/);
  assert.match(adapter, /sendMessage\(input:\s*ComposerInput\):\s*Promise<void>/);
  assert.match(adapter, /subscribeRunEvents\(runId:\s*string\):\s*RunEventSubscription/);
  assert.match(adapter, /openArtifact\(artifactId:\s*string\):\s*void/);

  for (const forbidden of [
    "librechat-data-provider",
    "useRecoilState",
    "~/Providers",
    "~/store",
    "useChatHelpers",
    "useGetStartupConfig",
    "Mongo",
    "endpoint",
    "providerKey",
  ]) {
    assert.doesNotMatch(adapter, new RegExp(forbidden));
  }
});

test("active workbench consumes librechat-ui instead of legacy shell files", () => {
  const consumers = [
    "src/components/workbench/WorkbenchShell.tsx",
    "src/components/workbench/WorkbenchRightPanel.tsx",
    "src/components/workbench/workbenchSurface.ts",
    "src/components/panels/SessionSidebar.tsx",
    "src/components/panels/SidebarParts/SessionListContent.tsx",
    "src/components/panels/SidebarParts/SidebarRail.tsx",
  ];

  for (const path of consumers) {
    const source = read(path);
    assert.match(source, /librechat-ui/, `${path} should import librechat-ui`);
    assert.doesNotMatch(
      source,
      /librechatShell/,
      `${path} should not import legacy librechatShell`,
    );
  }
});

test("active composer and state surfaces consume librechat-ui primitives", () => {
  for (const [path, expected] of [
    ["src/components/chat/ChatInput.tsx", /LibreChatComposerFrame/],
    ["src/components/chat/ComposerChips.tsx", /LibreChatComposerChip/],
    ["src/components/chat/SlashCommandMenu.tsx", /LibreChatCommandMenu/],
    ["src/components/chat/ComposerUnavailablePanel.tsx", /LibreChatStateSurface/],
    ["src/components/workbench/WorkbenchStateSurface.tsx", /LibreChatStateSurface/],
  ] as const) {
    const source = read(path);
    assert.match(source, /librechat-ui/, `${path} should import librechat-ui`);
    assert.match(source, expected, `${path} should consume ${expected}`);
  }

  const selectors = read("src/components/chat/ChatInputSelectors.tsx");
  assert.match(selectors, /LibreChatSelectorLayer/);
  assert.match(selectors, /LibreChatSelectorModal/);
});

test("librechat-ui does not import LibreChat backend authority", () => {
  const files = walkFiles(uiRoot).filter((file) => /\.(ts|tsx)$/.test(file));
  const combinedImports = files
    .map((file) => ({
      file: relative(uiRoot, file),
      imports: readFileSync(file, "utf8")
        .split(/\r?\n/)
        .filter((line) => /^\s*(import|export)\s+.+\s+from\s+/.test(line))
        .join("\n"),
    }))
    .map(({ file, imports }) => `// ${file}\n${imports}`)
    .join("\n");

  for (const forbidden of [
    "librechat-data-provider",
    "useRecoilState",
    "~/Providers",
    "~/store",
    "useChatHelpers",
    "useGetStartupConfig",
    "@librechat",
  ]) {
    assert.doesNotMatch(
      combinedImports,
      new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    );
  }
});
