import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("useAgent has no hidden Agent Market handoff state", () => {
  const source = readFileSync(join(process.cwd(), "src/hooks/useAgent.ts"), "utf8");

  assert.match(source, /selectedAgentProfileForRequest/);
  assert.match(source, /sessionApi\.submitChat\([\s\S]*selectedAgentProfileForRequest/);
  assert.match(source, /selectedAgentProfile \?\? null/);
  assert.doesNotMatch(
    source,
    /consumePendingAgentMarketSelection|pendingAgentMarketSelection|getSelectedAgentProfile/,
  );
});

test("useAgent rechecks submission ownership after MCP context creation", () => {
  const source = readFileSync(join(process.cwd(), "src/hooks/useAgent.ts"), "utf8");

  assert.match(
    source,
    /await prepareMcpRuntimeContext\([\s\S]*submissionTokenRef\.current !== submissionToken[\s\S]*submissionAuthIncarnationFenceRef\.current !== null[\s\S]*authScopeRef\.current !== submissionOwner[\s\S]*!isCurrentRequestSession\(\)[\s\S]*sessionApi\.submitChat/,
  );
  assert.doesNotMatch(source, /error\.code === "mcp_context_expired"/);
});
