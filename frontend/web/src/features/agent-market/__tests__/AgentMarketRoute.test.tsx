import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("market stays in the production shell and hands cards to canonical Chat", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentMarketRoute.tsx"),
    "utf8",
  );

  assert.match(source, /agentProfileApi\s*\.\s*listPublished\(\)/);
  assert.match(source, /AppShell/);
  assert.match(source, /SessionSidebar/);
  assert.match(source, /mobileSidebarOpen/);
  assert.match(source, /marketProfileRequest\(profile\)/);
  assert.match(source, /setPendingAgentMarketSelection\(marketProfileRequest\(profile\)\)/);
  assert.match(source, /CANONICAL_CHAT_PATH/);
  assert.match(source, /MARKET_CATALOG_LOAD_ERROR/);
  assert.doesNotMatch(source, /AgentMarketChat/);
  assert.doesNotMatch(source, /<textarea/);
  assert.doesNotMatch(source, /agentMarketChat/);
});
