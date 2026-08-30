import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("App registers Agent Builder as an admin-only authenticated route", () => {
  const source = readFileSync(join(process.cwd(), "src/App.tsx"), "utf8");

  assert.match(source, /features\/agent-builder\/AgentBuilderRoute/);
  assert.match(source, /path=\{APP_ROUTE_PATHS\.agentBuilder\}/);
  assert.match(source, /<ProtectedRoute requireAdmin redirectTo=\{APP_ROUTE_PATHS\.agentMarket\}>\s*<AgentBuilderRoute \/>\s*<\/ProtectedRoute>/);
});

test("App registers Run Monitor as an admin-only authenticated route", () => {
  const source = readFileSync(join(process.cwd(), "src/App.tsx"), "utf8");

  assert.match(source, /path=\{APP_ROUTE_PATHS\.runs\}/);
  assert.match(
    source,
    /<ProtectedRoute requireAdmin redirectTo=\{APP_ROUTE_PATHS\.agentMarket\}>\s*<RunsPage \/>\s*<\/ProtectedRoute>/,
  );
});

test("App registers Knowledge as an admin-only authenticated route", () => {
  const source = readFileSync(join(process.cwd(), "src/App.tsx"), "utf8");

  assert.match(source, /path=\{APP_ROUTE_PATHS\.knowledge\}/);
  assert.match(
    source,
    /<ProtectedRoute requireAdmin redirectTo=\{APP_ROUTE_PATHS\.agentMarket\}>\s*<KnowledgePage \/>\s*<\/ProtectedRoute>/,
  );
});
