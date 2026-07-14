import assert from "node:assert/strict";
import test from "node:test";

import {
  decideToolPermission,
  decideToolPermissionInbox,
  listToolPermissionInbox,
  type ToolPermissionDecision,
} from "../toolPermission.ts";

test("posts tool permission decisions to the ai-platform permission API", async () => {
  const calls: Array<{
    url: string;
    method?: string;
    body?: unknown;
  }> = [];

  const response = await decideToolPermission(
    "run-a",
    "tpr-a",
    "allow_for_run",
    "approved for this run",
    {
      request: async <T>(url: string, init?: RequestInit): Promise<T> => {
        calls.push({
          url,
          method: init?.method,
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        return {
          permission_request: {
            permission_request_id: "tpr-a",
            run_id: "run-a",
            tool_id: "ragflow-knowledge-search",
            tool_call_id: "call-a",
            risk_level: "high",
            write_capable: true,
            status: "decided",
            decision: "allow_for_run" satisfies ToolPermissionDecision,
          },
        } as T;
      },
    },
  );

  assert.deepEqual(calls, [
    {
      url: "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
      method: "POST",
      body: {
        decision: "allow_for_run",
        reason: "approved for this run",
      },
    },
  ]);
  assert.equal(response.permission_request.permission_request_id, "tpr-a");
  assert.equal(response.permission_request.decision, "allow_for_run");
});

test("uses only the tenant administrator inbox endpoints for inbox list and decisions", async () => {
  const calls: Array<{
    url: string;
    method?: string;
    body?: unknown;
  }> = [];
  const request = async <T>(url: string, init?: RequestInit): Promise<T> => {
    calls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (init?.method === "POST") {
      return {
        permission_request: {
          permission_request_id: "tpr-inbox",
          run_id: "run-owner",
          tool_id: "customer-write",
          tool_call_id: "call-owner",
          risk_level: "high",
          write_capable: true,
          status: "decided",
          decision: "deny",
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

  const inbox = await listToolPermissionInbox("pending", { limit: 25, request });
  const decision = await decideToolPermissionInbox("tpr-inbox", "deny", undefined, {
    request,
  });

  assert.equal(inbox.total, 0);
  assert.equal(decision.permission_request.decision, "deny");
  assert.deepEqual(calls, [
    {
      url: "/api/ai/tool-permissions/inbox?status=pending&limit=25",
      method: undefined,
      body: undefined,
    },
    {
      url: "/api/ai/tool-permissions/inbox/tpr-inbox/decision",
      method: "POST",
      body: { decision: "deny" },
    },
  ]);
  assert.equal(calls.some(({ url }) => url.includes("/runs/")), false);
});
