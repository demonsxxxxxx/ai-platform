import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("SessionSidebar uses role-safe Agent entry navigation in both rail layouts", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );

  assert.equal(
    (source.match(/onOpenAgentBuilder=\{\(\) => navigateWorkbenchItem\("agentBuilder"\)\}/g) ?? [])
      .length,
    2,
  );
});
