import assert from "node:assert/strict";
import test from "node:test";

import { APP_ROUTE_PATHS, resolveAppRoute } from "../appRouteManifest";

test("appRouteManifest separates the admin Builder from the ordinary-user Agent market", () => {
  assert.equal(APP_ROUTE_PATHS.agentBuilder, "/agent-builder");
  assert.equal(APP_ROUTE_PATHS.agentMarket, "/agent-market");
  assert.equal(
    APP_ROUTE_PATHS.agentMarketDetail,
    "/agent-market/:agentId/:revision",
  );
  assert.equal(
    APP_ROUTE_PATHS.agentMarketWorkspace,
    "/agent-market/:agentId/:revision/chat/:sessionId?",
  );
  assert.equal(resolveAppRoute("/agent-builder"), "agentBuilder");
  assert.equal(resolveAppRoute("/agent-market"), "agentMarket");
  assert.equal(resolveAppRoute("/agent-market/agt_support/4"), "agentMarketDetail");
  assert.equal(
    resolveAppRoute("/agent-market/agt_support/4/chat"),
    "agentMarketWorkspace",
  );
  assert.equal(
    resolveAppRoute("/agent-market/agt_support/4/chat/session-1"),
    "agentMarketWorkspace",
  );
  assert.equal("files" in APP_ROUTE_PATHS, false);
  assert.equal(resolveAppRoute("/chat"), "chat");
  assert.equal(resolveAppRoute("/agents"), "notFound");
});
