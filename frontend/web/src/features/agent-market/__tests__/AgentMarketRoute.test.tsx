import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("market keeps one, two, and three cards responsive while resolving durable detail URLs", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentMarketRoute.tsx"),
    "utf8",
  );

  assert.match(source, /agentProfileApi\s*\.\s*listPublished\(\)/);
  assert.doesNotMatch(source, /listPublished\(\{\s*query\s*,\s*category\s*\}\)/);
  assert.match(source, /activeTab === "favorites"/);
  assert.match(source, /我的收藏/);
  assert.match(source, /agentProfileApi\s*\.\s*getPublished\(agentId\)/);
  assert.doesNotMatch(source, /agentProfileApi\s*\.\s*createConversation\(/);
  assert.match(source, /navigate\(buildAgentMarketWorkspacePath\(profile\)\)/);
  assert.match(source, /navigate\(catalogReturnPath, \{ replace: true \}\)/);
  assert.match(source, /AppShell/);
  assert.match(source, /SessionSidebar/);
  assert.match(source, /mobileSidebarOpen/);
  assert.match(source, /useParams/);
  assert.match(source, /data-agent-market-search/);
  assert.match(source, /data-agent-market-filter/);
  assert.match(source, /data-agent-market-card/);
  assert.match(source, /MARKET_PAGE_SIZE = 9/);
  assert.match(source, /paginatedProfiles\.map/);
  assert.match(source, /<Pagination/);
  assert.match(source, /bg-amber-100/);
  assert.doesNotMatch(source, /企业专家目录/);
  assert.doesNotMatch(source, /选择一位企业专家/);
  assert.doesNotMatch(source, /找到 \{visibleProfiles\.length\} 位专家/);
  assert.match(source, /data-agent-market-detail/);
  assert.match(source, /data-agent-market-start-chat/);
  assert.match(source, /企业已发布/);
  assert.match(source, /输入与输出/);
  assert.match(source, /权限与数据访问/);
  assert.match(source, /selectPublishedMarketProfile/);
  assert.match(source, /buildAgentMarketDetailPath/);
  assert.match(source, /buildAgentMarketWorkspacePath/);
  assert.match(source, /grid-cols-\[repeat\(auto-fill,minmax\(min\(100%,22rem\),1fr\)\)\]/);
  assert.doesNotMatch(source, /xl:grid-cols-3/);
  assert.doesNotMatch(source, /grid-cols-1[\s\S]*md:grid-cols-2[\s\S]*xl:grid-cols-3/);
  assert.match(source, /MARKET_CATALOG_LOAD_ERROR/);
  assert.doesNotMatch(source, /<textarea/);
  assert.doesNotMatch(
    source,
    /setPendingAgentMarketSelection|consumePendingAgentMarketSelection|pendingAgentMarketSelection|buildAgentMarketChatPath/,
  );
  assert.doesNotMatch(source, /model_id|instructions|mcp_tool_ids|selected_skill/);
  assert.doesNotMatch(source, /CANONICAL_CHAT_PATH/);
  assert.match(source, /AgentIdentityAvatar/);
  assert.match(source, /Skill Set/);
  assert.match(source, /附件可选，不由专家限定格式/);
});

test("Agent product surfaces do not expose attachment type configuration", () => {
  const marketSource = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentMarketRoute.tsx"),
    "utf8",
  );
  const builderSource = readFileSync(
    join(process.cwd(), "src/features/agent-builder/AgentBuilderEnterpriseFields.tsx"),
    "utf8",
  );

  assert.match(marketSource, /附件可选，不由专家限定格式/);
  assert.doesNotMatch(marketSource, /supported_file_types/);
  assert.doesNotMatch(builderSource, /data-agent-builder-input-settings/);
  assert.doesNotMatch(builderSource, /常见附件类型提示/);
});
