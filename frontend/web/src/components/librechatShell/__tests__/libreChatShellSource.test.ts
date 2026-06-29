import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("librechat shell records pinned source provenance", () => {
  const source = read("src/librechat-ui/source.ts");
  const legacySurface = read("src/components/librechatShell/libreChatSurface.ts");

  assert.match(source, /9e74cc0e57b395926122bd4062c1fcedc48ed465/);
  assert.match(source, /license:\s*"MIT"/);
  assert.match(
    source,
    /client\/src\/components\/UnifiedSidebar\/UnifiedSidebar\.tsx/,
  );
  assert.match(source, /client\/src\/components\/Chat\/Input\/ChatForm\.tsx/);
  assert.match(source, /client\/src\/components\/SidePanel\/Nav\.tsx/);
  assert.match(source, /forbiddenScope/);
  assert.match(legacySurface, /\.\.\/\.\.\/librechat-ui/);
});

test("active librechat-ui layer forbids LibreChat backend authority imports", () => {
  const files = [
    "src/librechat-ui/source.ts",
    "src/librechat-ui/adapter.ts",
    "src/librechat-ui/surface.ts",
    "src/librechat-ui/Shell.tsx",
    "src/librechat-ui/Rail.tsx",
    "src/librechat-ui/Panel.tsx",
    "src/librechat-ui/SidePanel.tsx",
  ];
  const combinedImports = files
    .filter((file) => existsSync(join(root, file)))
    .map((file) => read(file))
    .join("\n")
    .split(/\r?\n/)
    .filter((line) => /^\s*(import|export)\s+.+\s+from\s+/.test(line))
    .join("\n");

  for (const forbidden of [
    "librechat-data-provider",
    "useRecoilState",
    "~/Providers",
    "~/store",
    "useChatHelpers",
    "useGetStartupConfig",
  ]) {
    assert.doesNotMatch(
      combinedImports,
      new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    );
  }
});

test("librechat shell geometry keeps the approved rail and panel widths", () => {
  const source = read("src/librechat-ui/surface.ts");

  assert.match(source, /railWidthPx:\s*52/);
  assert.match(source, /expandedMinWidthPx:\s*288/);
  assert.match(source, /mobileMaxWidth:\s*"min\(85vw, 380px\)"/);
});
