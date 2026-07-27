import assert from "node:assert/strict";
import test from "node:test";

import { APP_ROUTE_PATHS, resolveAppRoute } from "../appRouteManifest";

test("appRouteManifest resolves Agent Builder without reviving legacy agents routes", () => {
  assert.equal(APP_ROUTE_PATHS.agentBuilder, "/agent-builder");
  assert.equal(resolveAppRoute("/agent-builder"), "agentBuilder");
  assert.equal(resolveAppRoute("/agents"), "notFound");
});
