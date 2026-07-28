import assert from "node:assert/strict";
import test from "node:test";

import { APP_ROUTE_PATHS, resolveAppRoute } from "../appRouteManifest";

test("appRouteManifest separates the admin Builder from the ordinary-user Agent market", () => {
  assert.equal(APP_ROUTE_PATHS.agentBuilder, "/agent-builder");
  assert.equal(APP_ROUTE_PATHS.agentMarket, "/agent-market");
  assert.equal(resolveAppRoute("/agent-builder"), "agentBuilder");
  assert.equal(resolveAppRoute("/agent-market"), "agentMarket");
  assert.equal(resolveAppRoute("/agent-market/agt_support/4"), "notFound");
  assert.equal(resolveAppRoute("/chat"), "chat");
  assert.equal(resolveAppRoute("/agents"), "notFound");
});
