import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentBuilderWorkbench snapshots revalidated MCP identities into one useAgent submission", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/agent-builder/AgentBuilderWorkbench.tsx"),
    "utf8",
  );

  assert.match(source, /preparedMcpToolIdsRef/);
  assert.match(source, /preparedMcpToolIdsRef\.current = \[\.\.\.selectedMcpToolIds\]/);
  assert.match(source, /useAgent\(/);
  assert.match(source, /currentCatalog, chat/);
  assert.doesNotMatch(source, /onMcpToolSelectionChange/);
  assert.doesNotMatch(source, /sessionApi/);
});

test("production and harness wrappers forward admin profile authority while persistence remains guarded", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/agent-builder/AgentBuilderWorkbench.tsx"),
    "utf8",
  );

  assert.match(source, /export function AgentBuilderWorkbench\(\{[\s\S]*canManageProfiles/);
  assert.match(source, /export function AgentBuilderWorkbenchHarness\(\{[\s\S]*canManageProfiles/);
  assert.match(source, /canManageProfiles=\{canManageProfiles\}/);
  assert.match(source, /if \(!canManageProfiles\) return;/);
  assert.match(source, /expected_draft_revision: draft\.draftRevision \?\? 0/);
});
