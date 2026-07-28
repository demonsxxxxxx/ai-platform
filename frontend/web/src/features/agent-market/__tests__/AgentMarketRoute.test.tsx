import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("market reads published cards and starts a Chat submission with only the exact profile selector", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentMarketRoute.tsx"),
    "utf8",
  );

  assert.match(source, /agentProfileApi\s*\.\s*listPublished\(\)/);
  assert.match(source, /marketProfileRequest\(profile\)/);
  assert.match(source, /chat\.sendMessage\([\s\S]*marketProfileRequest\(profile\)/);
  assert.doesNotMatch(source, /selected_skill/);
  assert.doesNotMatch(source, /mcp_tool_ids/);
});
