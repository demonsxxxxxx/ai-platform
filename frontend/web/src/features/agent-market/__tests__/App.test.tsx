import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("App protects the Builder for administrators and exposes the ordinary-user market", () => {
  const source = readFileSync(join(process.cwd(), "src/App.tsx"), "utf8");

  assert.match(source, /path=\{APP_ROUTE_PATHS\.agentBuilder\}[\s\S]*requireAdmin/);
  assert.match(source, /path=\{APP_ROUTE_PATHS\.agentMarket\}/);
  assert.doesNotMatch(source, /agentMarketChat/);
  assert.match(source, /path=\{APP_ROUTE_PATHS\.chat\}[\s\S]*<ChatPage \/>/);
});
