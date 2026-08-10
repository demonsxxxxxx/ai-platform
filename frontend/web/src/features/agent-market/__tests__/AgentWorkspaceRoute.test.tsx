import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentWorkspaceRoute restores current or immutable historical Agent revisions", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-market/AgentWorkspaceRoute.tsx"),
    "utf8",
  );

  assert.match(source, /useParams<\{[\s\S]*agentId\?: string;[\s\S]*revision\?: string;[\s\S]*\}>\(\)/);
  assert.match(source, /agentProfileApi\.getPublished\(agentId\)/);
  assert.match(source, /historyScopeAuthorized/);
  assert.match(source, /useAgentConversationList\([\s\S]*historyScopeAuthorized \? agentId : undefined/);
  assert.match(source, /sessionApi\.getAuthoritative\(routeSessionId\)/);
  assert.match(source, /selectPublishedMarketProfile/);
  assert.match(source, /profile: historicalProfile\(identity\)/);
  assert.match(source, /loadedWorkspace !== null/);
  assert.match(source, /loadedWorkspace\.agentId === agentId/);
  assert.match(source, /loadedWorkspace\.revision === revision/);
  assert.match(source, /agentWorkspaceSessionSource=\{conversationList\}/);
  assert.match(
    source,
    /agentWorkspaceStartProfile=\{resolvedWorkspace\.startProfile \?\? undefined\}/,
  );
  assert.match(source, /agentWorkspaceReadOnly=\{resolvedWorkspace\.readOnly\}/);
  assert.match(
    source,
    /readOnly:[\s\S]*currentProfile === null \|\|[\s\S]*currentProfile\.expected_revision !== validRevision/,
  );
  assert.match(source, /<ChatAppContent[\s\S]*agentWorkspace=\{resolvedWorkspace\.profile\}/);
  assert.match(source, /<AppShell/);
  assert.match(source, /navigate\("\/agent-market", \{ replace: true \}\)/);
  assert.doesNotMatch(source, /<AppContent/);
});
