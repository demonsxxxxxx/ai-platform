import assert from "node:assert/strict";
import test from "node:test";

import type { ToolPermissionPart } from "../../../../types";
import { syncToolPermissionCardState } from "../toolPermissionCardState.ts";

const pendingPart: ToolPermissionPart = {
  type: "tool_permission",
  event_id: "evt-permission-requested",
  run_id: "run-a",
  permission_request_id: "tpr-a",
  tool_id: "ragflow-knowledge-search",
  tool_call_id: "call-a",
  risk_level: "high",
  write_capable: true,
  status: "pending",
};

test("keeps a local submit error while the permission request is still pending", () => {
  const result = syncToolPermissionCardState(pendingPart, "network failed");

  assert.equal(result.status, "pending");
  assert.equal(result.decision, undefined);
  assert.equal(result.error, "network failed");
});

test("clears a stale local submit error when replay marks the permission decided", () => {
  const result = syncToolPermissionCardState(
    {
      ...pendingPart,
      status: "decided",
      decision: "allow_once",
    },
    "network failed",
  );

  assert.equal(result.status, "decided");
  assert.equal(result.decision, "allow_once");
  assert.equal(result.error, null);
});
