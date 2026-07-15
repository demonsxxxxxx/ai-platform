import assert from "node:assert/strict";
import test from "node:test";

import {
  decideToolPermissionInbox,
  listToolPermissionInbox,
} from "../toolPermission.ts";

test("uses only the tenant administrator inbox endpoints for inbox list and decisions", async () => {
  const calls: Array<{
    url: string;
    method?: string;
    body?: unknown;
    signal?: AbortSignal | null;
  }> = [];
  const controller = new AbortController();
  const request = async <T>(url: string, init?: RequestInit): Promise<T> => {
    calls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      signal: init?.signal,
    });
    if (init?.method === "POST") {
      return {
        permission_request: {
          request_id: "tpr-inbox",
          run_id: "run-owner",
          tool_id: "customer-write",
          tool_display: "customer-write",
          risk_level: "high",
          write_capable: true,
          status: "decided",
          allowed_decisions: ["allow_once", "deny"],
        },
      } as T;
    }
    return {
      permission_requests: [],
      total: 0,
      status: "pending",
      limit: 25,
    } as T;
  };

  const inbox = await listToolPermissionInbox("pending", {
    limit: 25,
    request,
    signal: controller.signal,
  });
  const decisionController = new AbortController();
  const decision = await decideToolPermissionInbox("tpr-inbox", "deny", undefined, {
    request,
    signal: decisionController.signal,
  });

  assert.equal(inbox.total, 0);
  assert.equal(decision.permission_request.request_id, "tpr-inbox");
  assert.deepEqual(calls, [
    {
      url: "/api/ai/tool-permissions/inbox?status=pending&limit=25",
      method: undefined,
      body: undefined,
      signal: controller.signal,
    },
    {
      url: "/api/ai/tool-permissions/inbox/tpr-inbox/decision",
      method: "POST",
      body: { decision: "deny" },
      signal: decisionController.signal,
    },
  ]);
  assert.equal(calls.some(({ url }) => url.includes("/runs/")), false);
});
