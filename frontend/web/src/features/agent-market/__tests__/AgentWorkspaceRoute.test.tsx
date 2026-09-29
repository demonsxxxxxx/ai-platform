import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentWorkspaceRoute restores current and historical Agent Conversations by Agent id", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentWorkspaceRoute.tsx"),
    "utf8",
  );

  assert.match(source, /useParams<\{[\s\S]*agentId\?: string;[\s\S]*sessionId\?: string;[\s\S]*\}>\(\)/);
  assert.doesNotMatch(source, /revision/);
  assert.match(source, /agentProfileApi\.getPublished\(agentId\)/);
  assert.match(source, /historyScopeAuthorized/);
  assert.match(source, /useAgentConversationList\([\s\S]*historyScopeAuthorized \? agentId : undefined/);
  assert.match(source, /sessionApi\.getAuthoritative\(routeSessionId\)/);
  assert.match(source, /selectPublishedMarketProfile/);
  assert.match(source, /profile: historicalProfile\(identity\)/);
  assert.match(source, /phase === "ready"/);
  assert.match(source, /loadedWorkspace !== null/);
  assert.match(source, /loadedWorkspace\.agentId === agentId/);
  assert.match(source, /loadedWorkspace\.agentId === agentId/);
  assert.match(source, /agentWorkspaceSessionSource=\{conversationList\}/);
  assert.match(
    source,
    /agentWorkspaceStartProfile=\{resolvedWorkspace\.startProfile \?\? undefined\}/,
  );
  assert.match(source, /agentWorkspaceReadOnly=\{resolvedWorkspace\.readOnly\}/);
  assert.match(
    source,
    /readOnly: currentProfile === null/,
  );
  assert.match(source, /<ChatAppContent[\s\S]*agentWorkspace=\{resolvedWorkspace\.profile\}/);
  assert.match(source, /<AppShell/);
  assert.match(source, /navigate\("\/agent-market", \{ replace: true \}\)/);
  assert.doesNotMatch(source, /<AppContent/);
});
