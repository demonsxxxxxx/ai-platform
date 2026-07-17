import assert from "node:assert/strict";
import test from "node:test";

import { listToolPermissionHistory } from "../toolPermission.ts";

test("exposes redacted historical tool permission reads without a decision client", async () => {
  const calls: Array<{ url: string; method?: string }> = [];
  const result = await listToolPermissionHistory("all", {
    limit: 25,
    request: async <T>(url: string, init?: RequestInit): Promise<T> => {
      calls.push({ url, method: init?.method });
      return { permission_requests: [], total: 0, status: "all", limit: 25 } as T;
    },
  });

  assert.equal(result.total, 0);
  assert.deepEqual(calls, [{ url: "/api/ai/tool-permissions/inbox?status=all&limit=25", method: undefined }]);
});
