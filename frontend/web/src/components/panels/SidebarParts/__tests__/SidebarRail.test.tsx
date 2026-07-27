import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("SidebarRail exposes an active Agent Builder navigation control", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );

  assert.match(source, /onClick=\{onOpenAgentBuilder\}/);
  assert.match(source, /itemKey="agentBuilder"/);
  assert.match(source, /isRailItemActive\("agentBuilder"\)/);
});
