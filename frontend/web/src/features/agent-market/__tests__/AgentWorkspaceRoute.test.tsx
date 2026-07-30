import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentWorkspaceRoute restores only the published Agent revision from its deep link", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentWorkspaceRoute.tsx"),
    "utf8",
  );

  assert.match(source, /useParams<\{[\s\S]*agentId\?: string;[\s\S]*revision\?: string;[\s\S]*\}>\(\)/);
  assert.match(source, /agentProfileApi\.getPublished\(agentId\)/);
  assert.match(source, /agentProfileApi\.listConversations\(\)/);
  assert.match(source, /selectPublishedMarketProfile/);
  assert.match(source, /agentWorkspaceSessionIds=\{sessionIds\}/);
  assert.match(source, /<ChatAppContent[\s\S]*agentWorkspace=\{profile\}/);
  assert.match(source, /navigate\("\/agent-market", \{ replace: true \}\)/);
  assert.doesNotMatch(source, /<AppContent/);
});
