import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import "../../../../i18n";
import type { ToolPermissionPart } from "../../../../types";
import { AuthProvider } from "../../../../hooks/useAuth.tsx";
import { getOrdinaryUserToolPermissionPresentation } from "../toolPermissionCardState.ts";
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
    titleKey: "chat.toolPermission.invalidated.title",
    messageKey: "chat.toolPermission.invalidated.message",
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

test("projects terminal permission lifecycle states as read-only history", () => {
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({ ...pendingPart, status: "expired" }),
    {
      titleKey: "chat.toolPermission.expired.title",
      messageKey: "chat.toolPermission.expired.message",
    },
  );
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({ ...pendingPart, status: "cancelled" }),
    {
      titleKey: "chat.toolPermission.cancelled.title",
      messageKey: "chat.toolPermission.cancelled.message",
    },
  );
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({ ...pendingPart, status: "failed" }),
    {
      titleKey: "chat.toolPermission.terminalFailed.title",
      messageKey: "chat.toolPermission.terminalFailed.message",
    },
  );
  assert.deepEqual(
    getOrdinaryUserToolPermissionPresentation({ ...pendingPart, status: "invalidated" }),
    {
      titleKey: "chat.toolPermission.invalidated.title",
      messageKey: "chat.toolPermission.invalidated.message",
    },
  );
});

test("renders every chat permission history card read-only even when legacy callers claim admin governance", () => {
  const ReadOnlyCard = ToolPermissionCardItem as React.ComponentType<
    Record<string, unknown>
  >;
  const adminPendingMarkup = renderToStaticMarkup(
    React.createElement(ReadOnlyCard, {
      part: pendingPart,
      canManageToolPermissions: true,
    }),
  );
  const ordinaryPendingMarkup = renderToStaticMarkup(
    React.createElement(ReadOnlyCard, {
      part: pendingPart,
      canManageToolPermissions: false,
    }),
  );
  const ordinaryDeniedMarkup = renderToStaticMarkup(
    React.createElement(ReadOnlyCard, {
      part: { ...pendingPart, status: "decided", decision: "deny" },
      canManageToolPermissions: false,
    }),
  );
  const adminAllowedMarkup = renderToStaticMarkup(
    React.createElement(ReadOnlyCard, {
      part: { ...pendingPart, status: "decided", decision: "allow_for_run" },
      canManageToolPermissions: true,
    }),
  );

  for (const markup of [adminPendingMarkup, ordinaryPendingMarkup, ordinaryDeniedMarkup, adminAllowedMarkup]) {
    assert.doesNotMatch(markup, /<button\b/i);
    assert.doesNotMatch(
      markup,
      /ragflow-knowledge-search|高风险|可写操作|允许一次|允许本次运行/,
    );
  }
  assert.match(adminPendingMarkup, /工具权限已关闭/);
  assert.match(ordinaryPendingMarkup, /工具权限已关闭/);
  assert.match(ordinaryDeniedMarkup, /操作未获授权/);
  assert.match(adminAllowedMarkup, /本次运行已获授权/);
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
  assert.match(markup, /工具权限已关闭/);
});
