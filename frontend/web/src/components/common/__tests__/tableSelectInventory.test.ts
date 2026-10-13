import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      return ["__tests__", "generated"].includes(entry.name) ? [] : sourceFiles(path);
    }
    return path.endsWith(".tsx") ? [path] : [];
  });
}

test("production tables and ordinary selects use the shared components; retired Mermaid stays absent", () => {
  const root = join(process.cwd(), "src");
  const tableOwner = join(root, "components/common/Table.tsx");
  for (const path of sourceFiles(root)) {
    if (path === tableOwner) continue;
    const source = readFileSync(path, "utf8");
    assert.doesNotMatch(source, /<(?:table|thead|tbody|tr|th|td|select|option)\b/, path);
  }
  assert.equal(existsSync(join(root, "components/documents/previews/MermaidDiagram.tsx")), false);
  assert.match(readFileSync(join(root, "components/documents/previews/MarkdownRenderer.tsx"), "utf8"), /MarkdownContent/);
});
