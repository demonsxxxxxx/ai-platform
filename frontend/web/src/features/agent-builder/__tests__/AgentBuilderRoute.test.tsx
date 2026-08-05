import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentBuilderRoute supplies admin authority and public catalogs without owning profile or Chat state", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-builder/AgentBuilderRoute.tsx"),
    "utf8",
  );

  assert.match(source, /useSkills\(\{ allAuthorizedCatalog: true \}\)/);
  assert.match(source, /useTools\(\{ enabled: true \}\)/);
  assert.match(source, /mapAuthorizedBuilderSkills/);
  assert.match(source, /mapSafeBuilderMcpTools/);
  assert.match(source, /modelsResolved: !modelsLoading && modelsError === null/);
  assert.match(source, /BUILDER_CATALOG_LOAD_ERROR/);
  assert.match(source, /canManageProfiles=\{user\?\.is_admin === true\}/);
  assert.match(source, /AgentBuilderShell/);
  assert.doesNotMatch(source, /error instanceof Error \? error\.message/);
  assert.doesNotMatch(source, /useAgent/);
  assert.doesNotMatch(source, /useNavigate/);
  assert.doesNotMatch(source, /onHandoffReady/);
  assert.doesNotMatch(source, /listAdmin/);
  assert.doesNotMatch(source, /selectedMcpToolIdsRef/);
});
