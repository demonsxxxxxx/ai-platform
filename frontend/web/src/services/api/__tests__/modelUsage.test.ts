import assert from "node:assert/strict";
import test from "node:test";
import { modelUsageApi } from "../modelUsage";
import { ApiRequestError } from "../fetch";

test("usage uses cookie GET, literal search, exact dates/pagination and encoded model identity", async (t) => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  t.mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ data: [], total: 0 }), { status: 200 });
  });
  const signal = new AbortController().signal;
  const dates = { start_date: "2026-09-21", end_date: "2026-10-20" };
  await modelUsageApi.users({ ...dates, q: "工号%_ 姓名", limit: 50, offset: 50 }, signal);
  await modelUsageApi.models("user/a?b", dates, signal);
  const usersUrl = new URL(calls[0].url, "http://localhost");
  assert.equal(usersUrl.pathname, "/api/ai/admin/usage/users");
  assert.equal(usersUrl.searchParams.get("q"), "工号%_ 姓名");
  assert.equal(usersUrl.searchParams.get("offset"), "50");
  assert.equal(usersUrl.searchParams.get("limit"), "50");
  assert.equal(usersUrl.searchParams.get("start_date"), dates.start_date);
  assert.equal(usersUrl.searchParams.get("end_date"), dates.end_date);
  assert.equal(calls[1].url, "/api/ai/admin/usage/users/user%2Fa%3Fb/models?start_date=2026-09-21&end_date=2026-10-20");
  for (const { init } of calls) {
    assert.equal(init?.method, "GET");
    assert.equal(init?.credentials, "include");
    assert.equal(init?.signal, signal);
    assert.equal(new Headers(init?.headers).has("Authorization"), false);
  }
});

test("usage propagates typed permission/auth errors and cancellation", async (t) => {
  let status = 403;
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ detail: "private detail" }), { status }));
  const query = { start_date: "2026-10-20", end_date: "2026-10-20", q: "", limit: 50, offset: 0 };
  for (status of [403, 401]) {
    await assert.rejects(modelUsageApi.users(query, new AbortController().signal), (error) => error instanceof ApiRequestError && error.status === status);
  }
  const abort = new DOMException("Aborted", "AbortError");
  t.mock.method(globalThis, "fetch", async () => { throw abort; });
  await assert.rejects(modelUsageApi.models("user", query, new AbortController().signal), (error) => error === abort);
});
