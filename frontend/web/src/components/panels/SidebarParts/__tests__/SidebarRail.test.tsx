import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("SidebarRail exposes distinct Chinese Agent Market and Agent management entries", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );

  assert.match(source, /onClick=\{onOpenAgentMarket\}/);
  assert.match(source, /title="智能体市场"/);
  assert.match(source, /onClick=\{onOpenAgentBuilder\}/);
  assert.match(source, /title="智能体管理"/);
  assert.match(source, /itemKey="agentMarket"/);
  assert.match(source, /itemKey="agentBuilder"/);
  assert.match(source, /isRailItemActive\("agentBuilder"\)/);
});
