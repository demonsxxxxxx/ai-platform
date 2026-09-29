import assert from "node:assert/strict";
import test from "node:test";

import { APP_ROUTE_PATHS, resolveAppRoute } from "../appRouteManifest";

test("appRouteManifest separates the admin Builder from the ordinary-user Agent market", () => {
  assert.equal(APP_ROUTE_PATHS.agentBuilder, "/agent-builder");
  assert.equal(APP_ROUTE_PATHS.agentMarket, "/agent-market");
  assert.equal(APP_ROUTE_PATHS.pluginMarket, "/plugins");
  assert.equal(
    APP_ROUTE_PATHS.agentMarketDetail,
    "/agent-market/:agentId",
  );
  assert.equal(
    APP_ROUTE_PATHS.agentMarketWorkspace,
    "/agent-market/:agentId/chat/:sessionId?",
  );
  assert.equal(resolveAppRoute("/agent-builder"), "agentBuilder");
  assert.equal(resolveAppRoute("/agent-market"), "agentMarket");
  assert.equal(resolveAppRoute("/plugins"), "pluginMarket");
  assert.equal(resolveAppRoute("/agent-market/agt_support"), "agentMarketDetail");
  assert.equal(
    resolveAppRoute("/agent-market/agt_support/chat"),
    "agentMarketWorkspace",
  );
  assert.equal(
    resolveAppRoute("/agent-market/agt_support/chat/session-1"),
    "agentMarketWorkspace",
  );
  assert.equal(resolveAppRoute("/agent-market/agt_support/4"), "notFound");
  assert.equal(APP_ROUTE_PATHS.runs, "/runs");
  assert.equal(resolveAppRoute("/runs"), "runs");
  assert.equal("files" in APP_ROUTE_PATHS, false);
  assert.equal(resolveAppRoute("/chat"), "chat");
  assert.equal(resolveAppRoute("/agents"), "notFound");
});
