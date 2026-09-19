import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAdminUsersUrl,
  fetchAdminUserDiagnostics,
  fetchAdminUsers,
  type AdminUsersApiClient,
} from "../adminUsers";

test("admin Users list encodes bounded search and pagination", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminUsersApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return {
        schema_version: "ai-platform.admin-user-diagnostics.v1",
        users: [],
        total: 0,
        offset: 10,
        limit: 25,
      } as T;
    },
  };

  await fetchAdminUsers({ search: " 张 三 ", offset: 10, limit: 25 }, client);

  assert.equal(
    buildAdminUsersUrl({ search: " 张 三 ", offset: 10, limit: 25 }),
    "/api/ai/admin/users?offset=10&limit=25&search=%E5%BC%A0+%E4%B8%89",
  );
  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/users?offset=10&limit=25&search=%E5%BC%A0+%E4%B8%89",
      init: { method: "GET" },
    },
  ]);
});

test("admin User diagnostics encodes identity and stays read only", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminUsersApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return {} as T;
    },
  };

  await fetchAdminUserDiagnostics("user@example.com", client);

  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/users/user%40example.com/diagnostics",
      init: { method: "GET" },
    },
  ]);
});
