import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentBuilderRoute refreshes public catalogs without owning Chat or MCP selection state", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/agent-builder/AgentBuilderRoute.tsx"),
    "utf8",
  );

  assert.match(source, /useSkills\(\{ allAuthorizedCatalog: true \}\)/);
  assert.match(source, /useTools\(\{ enabled: true \}\)/);
  assert.match(source, /mapAuthorizedBuilderSkills/);
  assert.match(source, /mapSafeBuilderMcpTools/);
  assert.match(source, /modelsResolved: !modelsLoading && modelsError === null/);
  assert.doesNotMatch(source, /useAgent/);
  assert.doesNotMatch(source, /selectedMcpToolIdsRef/);
});
