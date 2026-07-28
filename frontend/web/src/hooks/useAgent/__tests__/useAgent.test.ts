import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("useAgent keeps Agent Market binding on canonical Chat only", () => {
  const source = readFileSync(join(process.cwd(), "src/hooks/useAgent.ts"), "utf8");

  assert.match(source, /consumePendingAgentMarketSelection/);
  assert.match(source, /selectedAgentProfileForRequest/);
  assert.match(source, /sessionApi\.submitChat\([\s\S]*selectedAgentProfileForRequest/);
  assert.match(source, /pathname !== "\/chat"/);
});
