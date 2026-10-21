import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../components/common/__tests__/lobeUiTestRoot";
import { usageDateRange, isUsageDateRangeValid } from "../usageDateRange";
import type { UsageReport, UsageUser, UsageModel } from "../../../services/api/modelUsage";

const fixture = installSelectTestDom();
test.after(() => fixture.close());
const { act, createElement } = await import("react");
const { UsagePage } = await import("../UsagePage");
const { modelUsageApi } = await import("../../../services/api/modelUsage");
const { ApiRequestError } = await import("../../../services/api/fetch");
const counts = { input_tokens: 1, cache_read_input_tokens: 2, cache_creation_input_tokens: 3, output_tokens: 4, input_total: 6, total_tokens: 10 };
const user = (id: string | null): UsageUser => ({ ...counts, user_id: id, work_id: id, display_name: id ? `姓名${id}` : null });
function report<T>(data: T[], status: "reported_final" | "partial" | "unavailable" = "reported_final"): UsageReport<T> {
  return { data, total: data.length, limit: 50, offset: 0, meta: { start_date: "2026-10-01", end_date: "2026-10-20", timezone: "Asia/Shanghai", collection_started_at: "2026-10-01T00:00:00Z", coverage: { status, reason: status === "partial" ? "partial_report" : status === "unavailable" ? "not_collected" : null, scopes: 2, partial_scopes: status === "partial" ? 1 : 0 } } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
async function mount() {
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  await act(async () => root.render(createElement(UsagePage)));
  return {
    container,
    async click(label: string) {
      const button = [...container.querySelectorAll("button")].find((item) => item.textContent === label || item.getAttribute("aria-label") === label);
      assert.ok(button, label);
      await act(async () => button.click());
    },
    async input(selector: string, value: string) {
      const input = container.querySelector<HTMLInputElement>(selector)!;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
    },
    async close() { await act(async () => root.unmount()); },
  };
}

test("Beijing calendar dates handle midnight, leap years, invalid and reversed dates", () => {
  assert.deepEqual(usageDateRange(30, new Date("2026-10-19T16:00:00Z")), { start: "2026-09-21", end: "2026-10-20" });
  assert.deepEqual(usageDateRange(1, new Date("2026-10-19T15:59:59Z")), { start: "2026-10-19", end: "2026-10-19" });
  assert.deepEqual(usageDateRange(7, new Date("2024-02-29T16:00:00Z")), { start: "2024-02-24", end: "2024-03-01" });
  for (const start of ["", "2026-02-29", "2026-13-01", "0000-01-01", "2026-11-01"]) assert.equal(isUsageDateRangeValid({ start, end: "2026-10-20" }), false);
  assert.equal(isUsageDateRangeValid({ start: "2024-02-29", end: "2024-02-29" }), true);
});

test("server ranking, null identities, actual model columns and recorded zero remain distinct from coverage", async (t) => {
  const rows = [user("second"), { ...user("first"), total_tokens: 999 }, { ...user(null), ...Object.fromEntries(Object.keys(counts).map((key) => [key, 0])) }];
  t.mock.method(modelUsageApi, "users", async () => report(rows, "partial"));
  const models = t.mock.method(modelUsageApi, "models", async () => report<UsageModel>([{ ...counts, model: "actual-model-A" }, { ...counts, model: "actual-model-B" }]));
  const view = await mount();
  try {
    const tableRows = [...view.container.querySelectorAll("tr[data-row-key]")];
    assert.deepEqual(tableRows.map((row) => row.getAttribute("data-row-key")), ["user:second", "user:first", "unassociated"]);
    assert.match(tableRows[2].textContent!, /未提供工号.*未关联用户.*0/);
    assert.equal(tableRows[2].querySelector("button"), null);
    assert.match(view.container.textContent!, /采集不完整/);
    assert.equal(view.container.querySelector('[aria-pressed="true"]')?.textContent, "近30天");
    await view.click("展开second模型用量");
    assert.equal(models.mock.callCount(), 1);
    const expectedDates = usageDateRange(30);
    assert.deepEqual(models.mock.calls[0].arguments[1], { start_date: expectedDates.start, end_date: expectedDates.end });
    for (const label of ["actual-model-A", "actual-model-B", "缓存读取", "缓存创建", "输入合计", "输出"]) assert.ok(view.container.textContent!.includes(label));
    // JSDOM verifies the layout contract; desktop/mobile geometry is browser-owned.
    const userTable = tableRows[0].closest("table")!;
    assert.equal(userTable.style.tableLayout, "fixed");
    assert.equal(userTable.style.width, "700px");
    const modelScroll = view.container.querySelector<HTMLElement>('[aria-label="模型明细横向滚动区域"]')!;
    assert.equal(modelScroll.tabIndex, 0);
    assert.ok(modelScroll.classList.contains("min-w-0"));
    assert.ok(modelScroll.classList.contains("max-w-full"));
    assert.ok(modelScroll.classList.contains("overflow-x-auto"));
    const modelTable = modelScroll.querySelector("table")!;
    assert.equal(modelTable.style.tableLayout, "fixed");
    assert.equal(modelTable.style.width, "1080px");
    await view.click("收起second模型用量");
    assert.equal(view.container.querySelector('[aria-label="实际模型用量"]'), null);
  } finally { await view.close(); }
});

test("query changes abort users and expanded models; ignored aborts cannot replace current results", async (t) => {
  const staleUsers = deferred<UsageReport<UsageUser>>();
  const staleModels = deferred<UsageReport<UsageModel>>();
  let calls = 0;
  const users = t.mock.method(modelUsageApi, "users", async () => {
    calls += 1;
    if (calls === 1) return report([user("original")]);
    if (calls === 2) return staleUsers.promise;
    return report([user("current")]);
  });
  const models = t.mock.method(modelUsageApi, "models", () => staleModels.promise);
  const view = await mount();
  try {
    await view.click("展开original模型用量");
    await view.click("今天");
    assert.equal(models.mock.calls[0].arguments[2]!.aborted, true);
    assert.match(view.container.textContent!, /正在加载用量/);
    await view.input("#usage-search", "current");
    assert.equal(users.mock.calls[1].arguments[1]!.aborted, true);
    assert.equal(users.mock.calls[2].arguments[0]!.q, "current");
    assert.equal(users.mock.calls[2].arguments[0]!.start_date, users.mock.calls[2].arguments[0]!.end_date);
    await act(async () => {
      staleUsers.resolve(report([user("stale-user")]));
      staleModels.resolve(report([{ ...counts, model: "stale-model" }]));
    });
    assert.match(view.container.textContent!, /姓名current/);
    assert.doesNotMatch(view.container.textContent!, /stale-user|stale-model/);
    await view.click("自定义");
    await view.input("#usage-start", "");
    assert.match(view.container.querySelector('[role="alert"]')!.textContent!, /有效且完整/);
    assert.equal(users.mock.calls.at(-1)!.arguments[1]!.aborted, true);
    assert.equal(view.container.querySelector('[aria-label="用户用量排名"]'), null);
    await view.input("#usage-start", "2026-01-01");
    await view.input("#usage-end", "2026-01-02");
    assert.equal(users.mock.calls.at(-1)!.arguments[0]!.start_date, "2026-01-01");
    assert.equal(users.mock.calls.at(-1)!.arguments[0]!.end_date, "2026-01-02");
    await view.input("#usage-start", "2026-01-03");
    assert.match(view.container.querySelector('[role="alert"]')!.textContent!, /结束日期不能早于/);
  } finally { await view.close(); }
});

test("pagination requests server offsets and search resets the page", async (t) => {
  const users = t.mock.method(modelUsageApi, "users", async () => ({ ...report([user("page")]), total: 101 }));
  const view = await mount();
  try {
    await view.click("2");
    assert.equal(users.mock.calls.at(-1)!.arguments[0]!.offset, 50);
    await view.input("#usage-search", "姓名");
    assert.equal(users.mock.calls.at(-1)!.arguments[0]!.offset, 0);
  } finally { await view.close(); }
});

test("missing history is not zero, failures retry and auth errors do not offer retries", async (t) => {
  let outcome: UsageReport<UsageUser> | Error = new Error("private upstream detail");
  t.mock.method(modelUsageApi, "users", async () => { if (outcome instanceof Error) throw outcome; return outcome; });
  const view = await mount();
  try {
    assert.match(view.container.textContent!, /加载失败/);
    assert.doesNotMatch(view.container.textContent!, /private upstream/);
    outcome = report([], "unavailable");
    await view.click("重试");
    assert.match(view.container.textContent!, /未采集不代表零用量/);
    assert.match(view.container.textContent!, /尚未采集的历史区间/);
    outcome = new ApiRequestError("forbidden", 403);
    await view.click("今天");
    assert.match(view.container.textContent!, /仅管理员可访问/);
    assert.ok(![...view.container.querySelectorAll("button")].some((button) => button.textContent === "重试"));
    outcome = new ApiRequestError("expired", 401);
    await view.click("近7天");
    assert.match(view.container.textContent!, /重新登录/);
  } finally { await view.close(); }
});

test("collapsed model request is aborted, re-expansion retries with a fresh owner", async (t) => {
  t.mock.method(modelUsageApi, "users", async () => report([user("owner")]));
  const stale = deferred<UsageReport<UsageModel>>();
  let calls = 0;
  const models = t.mock.method(modelUsageApi, "models", async () => {
    calls += 1;
    if (calls === 1) return stale.promise;
    if (calls === 2) throw new Error("failed");
    return report([{ ...counts, model: "fresh-model" }]);
  });
  const view = await mount();
  try {
    await view.click("展开owner模型用量");
    await view.click("收起owner模型用量");
    assert.equal(models.mock.calls[0].arguments[2]!.aborted, true);
    await view.click("展开owner模型用量");
    assert.match(view.container.textContent!, /加载失败/);
    await view.click("重试");
    await act(async () => stale.resolve(report([{ ...counts, model: "old-model" }])));
    assert.match(view.container.textContent!, /fresh-model/);
    assert.doesNotMatch(view.container.textContent!, /old-model/);
  } finally { await view.close(); }
});

test("a stale list rejection cannot replace a newer successful empty result", async (t) => {
  const stale = deferred<UsageReport<UsageUser>>();
  let calls = 0;
  t.mock.method(modelUsageApi, "users", async () => ++calls === 1 ? stale.promise : report([]));
  const view = await mount();
  try {
    await view.click("近7天");
    await act(async () => stale.reject(new Error("old failure")));
    assert.match(view.container.textContent!, /所选范围没有已记录用量/);
    assert.doesNotMatch(view.container.textContent!, /加载失败/);
  } finally { await view.close(); }
});
