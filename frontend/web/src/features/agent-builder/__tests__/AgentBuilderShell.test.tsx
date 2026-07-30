import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentBuilderShell retains the primary application sidebar", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-builder/AgentBuilderShell.tsx"),
    "utf8",
  );

  assert.match(source, /<AppShell\s+activeTab="chat"/);
  assert.match(source, /<SessionSidebar/);
  assert.match(source, /authApi\.updateMetadata/);
  assert.match(source, /onToggleCollapsed/);
});
