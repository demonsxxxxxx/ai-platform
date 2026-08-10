import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("market stays in the production shell and resolves durable detail URLs", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentMarketRoute.tsx"),
    "utf8",
  );

  assert.match(source, /agentProfileApi\s*\.\s*listPublished\(\{\s*query,\s*category\s*\}\)/);
  assert.match(source, /agentProfileApi\s*\.\s*getPublished\(agentId\)/);
  assert.doesNotMatch(source, /agentProfileApi\s*\.\s*createConversation\(/);
  assert.match(source, /navigate\(buildAgentMarketWorkspacePath\(profile\)\)/);
  assert.match(source, /AppShell/);
  assert.match(source, /SessionSidebar/);
  assert.match(source, /mobileSidebarOpen/);
  assert.match(source, /useParams/);
  assert.match(source, /data-agent-market-search/);
  assert.match(source, /data-agent-market-filter/);
  assert.match(source, /data-agent-market-card/);
  assert.match(source, /data-agent-market-detail/);
  assert.match(source, /data-agent-market-start-chat/);
  assert.match(source, /企业已发布/);
  assert.match(source, /输入与输出/);
  assert.match(source, /权限与数据访问/);
  assert.match(source, /selectPublishedMarketProfile/);
  assert.match(source, /buildAgentMarketDetailPath/);
  assert.match(source, /buildAgentMarketWorkspacePath/);
  assert.match(source, /MARKET_CATALOG_LOAD_ERROR/);
  assert.doesNotMatch(source, /<textarea/);
  assert.doesNotMatch(
    source,
    /setPendingAgentMarketSelection|consumePendingAgentMarketSelection|pendingAgentMarketSelection|buildAgentMarketChatPath/,
  );
  assert.doesNotMatch(source, /model_id|instructions|mcp_tool_ids|selected_skill/);
  assert.doesNotMatch(source, /CANONICAL_CHAT_PATH/);
});
