import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import "../../../../i18n";
import type { ToolPermissionPart } from "../../../../types";
import {
  getOrdinaryUserToolPermissionPresentation,
} from "../toolPermissionCardState.ts";
import { MessagePartRenderer } from "../MessagePartRenderer.tsx";

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

test("renders permission history without controls for every chat viewer", () => {
  const rendererSource = readFileSync(
    resolve(__dirname, "../MessagePartRenderer.tsx"),
    "utf8",
  );
  const chatMessageSource = readFileSync(resolve(__dirname, "../index.tsx"), "utf8");
  const pendingMarkup = renderToStaticMarkup(
    React.createElement(MessagePartRenderer, {
      part: pendingPart,
      isLast: true,
    }),
  );
  const deniedMarkup = renderToStaticMarkup(
    React.createElement(MessagePartRenderer, {
      part: { ...pendingPart, status: "decided", decision: "deny" },
      isLast: true,
    }),
  );

  assert.doesNotMatch(rendererSource, /canManageToolPermissions|decideToolPermission|PermissionDecisionButton/);
  assert.doesNotMatch(chatMessageSource, /canManageToolPermissions|user\?\.is_admin/);
  assert.doesNotMatch(pendingMarkup, /<button\b/i);
  assert.doesNotMatch(deniedMarkup, /<button\b/i);
  assert.match(pendingMarkup, /正在等待管理员处理/);
  assert.match(deniedMarkup, /操作未获授权/);
});
