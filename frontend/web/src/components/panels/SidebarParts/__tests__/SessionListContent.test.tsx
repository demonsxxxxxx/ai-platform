import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("SessionListContent gives ordinary users a Chinese Agent Market entry and admins Agent management", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );

  assert.match(source, /key: "agentBuilder"/);
  assert.match(source, /agentNavigationLabel = isAiAdmin \? "智能体管理" : "智能体市场"/);
  assert.match(source, /agentNavigationPath = isAiAdmin \? "\/agent-builder" : "\/agent-market"/);

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
  assert.match(railSource, /agentNavigationLabel = isAiAdmin \? "智能体管理" : "智能体市场"/);
  assert.equal(
    (sidebarSource.match(/onOpenAgentBuilder=\{\(\) => navigateWorkbenchItem\("agentBuilder"\)\}/g) ?? [])
      .length,
    2,
  );
});
