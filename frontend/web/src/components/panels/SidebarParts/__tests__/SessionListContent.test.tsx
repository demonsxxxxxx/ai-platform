import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import type { BackendSession } from "../../../../services/api.ts";
import { groupSessionsByAgent } from "../../sessionHelpers.ts";

test("global session history groups by Agent and sorts newest sessions first", () => {
  const session = (
    id: string,
    agentId: string,
    updatedAt: string,
    name = id,
  ): BackendSession => ({
    id,
    agent_id: agentId,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: updatedAt,
    is_active: true,
    name,
    metadata: {},
    agent_conversation: {
      agent_id: agentId,
      revision: 3,
      name: agentId === "agt_support" ? "支持助手" : "研究助手",
      description: "",
      starter_prompts: [],
      avatar_ref: "builtin:assistant",
      avatar_seed: agentId,
      published_at: null,
    },
  });

  const groups = groupSessionsByAgent([
    session("support-old", "agt_support", "2026-08-01T01:00:00Z"),
    session("research-new", "agt_research", "2026-08-01T04:00:00Z"),
    session("support-new", "agt_support", "2026-08-01T03:00:00Z"),
  ]);

  assert.deepEqual(groups.map(({ key, name }) => [key, name]), [
    ["agt_research", "研究助手"],
    ["agt_support", "支持助手"],
  ]);
  assert.deepEqual(
    groups.find(({ key }) => key === "agt_support")?.sessions.map(({ id }) => id),
    ["support-new", "support-old"],
  );
});

test("SessionListContent gives ordinary users a Chinese Agent Market entry and admins Agent management", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );

  assert.match(source, /key: "agentMarket"/);
  assert.match(source, /label: "专家市场"/);
  assert.match(source, /key: "agentBuilder"/);
  assert.match(source, /label: "专家管理"/);

  const railSource = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );
  const sidebarSource = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );
  assert.match(railSource, /itemKey="agentMarket"/);
  assert.match(railSource, /itemKey="agentBuilder"/);
  assert.match(railSource, /isRailItemActive\("agentBuilder"\)/);
  assert.match(railSource, /title="专家市场"/);
  assert.match(railSource, /title="专家管理"/);
  assert.equal(
    (sidebarSource.match(/onOpenAgentBuilder=\{\(\) => navigateWorkbenchItem\("agentBuilder"\)\}/g) ?? [])
      .length,
    2,
  );
  assert.match(source, /groupSessionsByAgent/);
  assert.match(source, /data-agent-history-group/);
  assert.match(source, /aria-expanded=\{isExpanded\}/);
});
