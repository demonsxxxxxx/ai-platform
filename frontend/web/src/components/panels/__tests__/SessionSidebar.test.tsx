import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("Agent workspaces load global history separately and open sessions in their original context", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );

  assert.match(source, /const globalHistoryList = globalHistorySource \?\? sessionList/);
  assert.doesNotMatch(source, /separateGlobalSessionList/);
  assert.match(source, /globalSessions=\{globalHistoryList\.sessions\}/);
  assert.match(source, /onSelectGlobalHistorySession\(session\)/);
  assert.match(source, /onSelectSession\(session\.id\)/);
});

test("SessionSidebar uses role-safe Agent entry navigation in both rail layouts", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );

  assert.equal(
    (source.match(/onOpenAgentMarket=\{\(\) => navigate\("\/agent-market"\)\}/g) ?? [])
      .length,
    2,
  );
  assert.equal(
    (source.match(/onOpenAgentBuilder=\{\(\) => navigateWorkbenchItem\("agentBuilder"\)\}/g) ?? [])
      .length,
    2,
  );
});
