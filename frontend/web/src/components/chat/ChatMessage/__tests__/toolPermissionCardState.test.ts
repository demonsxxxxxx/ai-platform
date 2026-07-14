import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import type { ToolPermissionPart } from "../../../../types";
import {
  getOrdinaryUserToolPermissionPresentation,
  syncToolPermissionCardState,
} from "../toolPermissionCardState.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

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

test("projects each ordinary-user permission history state without approval controls", () => {
  assert.deepEqual(getOrdinaryUserToolPermissionPresentation(pendingPart), {
    titleKey: "chat.toolPermission.pending.title",
    messageKey: "chat.toolPermission.pending.message",
  });
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({
      ...pendingPart,
      status: "decided",
      decision: "allow_once",
    }),
    {
      titleKey: "chat.toolPermission.allowedOnce.title",
      messageKey: "chat.toolPermission.allowedOnce.message",
    },
  );
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({
      ...pendingPart,
      status: "decided",
      decision: "allow_for_run",
    }),
    {
      titleKey: "chat.toolPermission.allowedForRun.title",
      messageKey: "chat.toolPermission.allowedForRun.message",
    },
  );
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({
      ...pendingPart,
      status: "decided",
      decision: "deny",
    }),
    {
      titleKey: "chat.toolPermission.denied.title",
      messageKey: "chat.toolPermission.denied.message",
    },
  );
});

test("uses a decided fallback when the history has no known decision", () => {
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({
      ...pendingPart,
      status: "decided",
    }),
    {
      titleKey: "chat.toolPermission.decided.title",
      messageKey: "chat.toolPermission.decided.message",
    },
  );
});

test("keeps the control decision separate from ordinary-user presentation", () => {
  const presentation = getOrdinaryUserToolPermissionPresentation(pendingPart);

  assert.deepEqual(Object.keys(presentation).sort(), [
    "messageKey",
    "titleKey",
  ]);
});

test("requires an explicit admin capability before rendering permission controls", () => {
  const rendererSource = readFileSync(
    resolve(__dirname, "../MessagePartRenderer.tsx"),
    "utf8",
  );
  const chatMessageSource = readFileSync(resolve(__dirname, "../index.tsx"), "utf8");

  assert.match(rendererSource, /canManageToolPermissions = false/);
  assert.match(rendererSource, /canManageToolPermissions &&\s*\n?\s*\(isDecided/);
  assert.match(rendererSource, /<PermissionDecisionButton/);
  assert.match(chatMessageSource, /user\?\.is_admin === true/);
  assert.match(chatMessageSource, /canManageToolPermissions=\{canManageToolPermissions\}/);
});
