import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import "../../../../i18n";
import type { ToolPermissionPart } from "../../../../types";
import { AuthProvider } from "../../../../hooks/useAuth.tsx";
import {
  canManageToolPermissions,
  getOrdinaryUserToolPermissionPresentation,
  submitToolPermissionDecision,
  syncToolPermissionCardState,
} from "../toolPermissionCardState.ts";
import {
  MessagePartRenderer,
  ToolPermissionCardItem,
} from "../MessagePartRenderer.tsx";

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

test("only the authoritative is_admin projection authorizes a chat decision", () => {
  assert.equal(canManageToolPermissions({ is_admin: true }), true);
  assert.equal(canManageToolPermissions({ is_admin: false }), false);
  assert.equal(canManageToolPermissions(null), false);
  const roleNamedAdmin = { is_admin: false, roles: ["ai-admin"] };
  assert.equal(canManageToolPermissions(roleNamedAdmin), false);
});

test("renders governed controls for administrators while keeping ordinary history read-only", () => {
  const adminPendingMarkup = renderToStaticMarkup(
    React.createElement(ToolPermissionCardItem, {
      part: pendingPart,
      canManageToolPermissions: true,
    }),
  );
  const ordinaryPendingMarkup = renderToStaticMarkup(
    React.createElement(ToolPermissionCardItem, {
      part: pendingPart,
      canManageToolPermissions: false,
    }),
  );
  const ordinaryDeniedMarkup = renderToStaticMarkup(
    React.createElement(ToolPermissionCardItem, {
      part: { ...pendingPart, status: "decided", decision: "deny" },
      canManageToolPermissions: false,
    }),
  );
  const adminDecidedMarkup = renderToStaticMarkup(
    React.createElement(ToolPermissionCardItem, {
      part: { ...pendingPart, status: "decided", decision: "deny" },
      canManageToolPermissions: true,
    }),
  );

  assert.match(adminPendingMarkup, /工具权限治理/);
  assert.match(adminPendingMarkup, /允许一次/);
  assert.match(adminPendingMarkup, /允许本次运行/);
  assert.match(adminPendingMarkup, /拒绝/);
  assert.match(adminPendingMarkup, /ragflow-knowledge-search/);
  assert.doesNotMatch(ordinaryPendingMarkup, /<button\b/i);
  assert.doesNotMatch(ordinaryPendingMarkup, /ragflow-knowledge-search|高风险|可写操作|允许一次|拒绝/);
  assert.match(ordinaryPendingMarkup, /正在等待管理员处理/);
  assert.match(ordinaryDeniedMarkup, /操作未获授权/);
  assert.doesNotMatch(adminDecidedMarkup, /<button\b/i);
  assert.match(adminDecidedMarkup, /已记录决策：拒绝/);
});

test("the shared chat renderer fails closed while no authoritative admin is present", () => {
  const markup = renderToStaticMarkup(
    React.createElement(
      AuthProvider,
      null,
      React.createElement(MessagePartRenderer, {
        part: pendingPart,
        isLast: true,
      }),
    ),
  );

  assert.doesNotMatch(markup, /<button\b/i);
  assert.doesNotMatch(markup, /ragflow-knowledge-search|高风险|可写操作/);
  assert.match(markup, /正在等待管理员处理/);
});

test("preserves a localized admin submission error until the recorded decision arrives", () => {
  assert.deepEqual(
    syncToolPermissionCardState(pendingPart, "提交权限决策失败，请稍后重试。"),
    {
      status: "pending",
      decision: undefined,
      error: "提交权限决策失败，请稍后重试。",
    },
  );
  assert.deepEqual(
    syncToolPermissionCardState(
      { ...pendingPart, status: "decided", decision: "allow_once" },
      "提交权限决策失败，请稍后重试。",
    ),
    {
      status: "decided",
      decision: "allow_once",
      error: null,
    },
  );
});

test("submits a decision only through the governed API seam", async () => {
  const calls: Array<[string, string, string]> = [];

  const response = await submitToolPermissionDecision(
    pendingPart,
    "allow_for_run",
    async (runId, requestId, decision) => {
      calls.push([runId, requestId, decision]);
      return {
        permission_request: {
          permission_request_id: requestId,
          run_id: runId,
          tool_id: pendingPart.tool_id,
          tool_call_id: pendingPart.tool_call_id,
          risk_level: pendingPart.risk_level,
          write_capable: pendingPart.write_capable,
          status: "decided",
          decision,
        },
      };
    },
  );

  assert.deepEqual(calls, [["run-a", "tpr-a", "allow_for_run"]]);
  assert.equal(response.permission_request.decision, "allow_for_run");
});
