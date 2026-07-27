import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("SessionSidebar wires Agent Builder navigation into both rail layouts", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );

  assert.equal(
    (source.match(/onOpenAgentBuilder=\{\(\) => navigate\("\/agent-builder"\)\}/g) ?? [])
      .length,
    2,
  );
});
