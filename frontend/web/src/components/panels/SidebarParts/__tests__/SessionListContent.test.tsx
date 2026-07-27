import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("SessionListContent exposes the Agent Builder navigation entry", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );

  assert.match(source, /key: "agentBuilder"/);
  assert.match(source, /navigate\("\/agent-builder"\)/);

  const railSource = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );
  const sidebarSource = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );
  assert.match(railSource, /itemKey="agentBuilder"/);
  assert.match(railSource, /isRailItemActive\("agentBuilder"\)/);
  assert.equal(
    (sidebarSource.match(/onOpenAgentBuilder=\{\(\) => navigate\("\/agent-builder"\)\}/g) ?? [])
      .length,
    2,
  );
});
