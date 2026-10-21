import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { adminUsersApi, type AdminUserSummary } from "../../../services/api/adminUsers.ts";
import {
  modelAdminApi,
  type AdminModelEntry,
  type AdminModelState,
} from "../../../services/api/modelAdmin.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";

const dom = installSelectTestDom();
after(() => dom.close());
function changeInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function text(node: unknown): string {
  const value = node as { data?: unknown; textContent?: unknown; childNodes?: unknown[] };
  if (typeof value.data === "string") return value.data;
  return typeof value.textContent === "string" && value.textContent
    ? value.textContent
    : Array.from(value.childNodes ?? []).map(text).join("");
}

function user(userId: string, displayName: string): AdminUserSummary {
  return {
    user_id: userId,
    display_name: displayName,
    status: "active",
    session_count: 0,
    run_count: 0,
    queued_run_count: 0,
    running_run_count: 0,
    succeeded_run_count: 0,
    failed_run_count: 0,
    cancelled_run_count: 0,
  };
}

function model(overrides: Partial<AdminModelEntry> = {}): AdminModelEntry {
  return {
    id: "mdl_gpt", value: "mock/gpt", label: "合成模型", provider: "compatible",
    enabled: true, available: true, is_default: true, order: 1,
    access_mode: "all", allowed_user_ids: [],
    last_seen_revision: 4, last_seen_at: "2026-10-09T00:00:00Z",
    max_input_tokens: 32000, max_output_tokens: 2048,
    ...overrides,
  };
}

function state(entry: AdminModelEntry): AdminModelState {
  return {
    connection: {
      configured: true, revision: 4,
      base_url: "https://gateway-a.example", key_fingerprint: "synthetic-marker",
    },
    models: [entry],
  };
}

async function waitFor(
  React: typeof import("react"),
  predicate: () => boolean,
  label: string,
): Promise<void> {
  for (let attempt = 0; attempt < 60 && !predicate(); attempt += 1) {
    await React.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  assert.ok(predicate(), label);
}

interface DirectoryCall {
  search?: string;
  offset?: number;
  limit?: number;
  signal?: AbortSignal;
}

async function mount(
  entry: AdminModelEntry | AdminModelEntry[],
  options: {
    publish?: typeof modelAdminApi.publish;
    directory?: (call: DirectoryCall) => Promise<{
      schema_version: "ai-platform.admin-user-diagnostics.v1";
      users: AdminUserSummary[];
      total: number;
      offset: number;
      limit: number;
    }>;
  } = {},
) {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");

  const originalModelAdmin = { ...modelAdminApi };
  const originalList = adminUsersApi.list;
  const saved: AdminModelState = Array.isArray(entry)
    ? { ...state(entry[0]), models: entry }
    : state(entry);
  const published: Array<{ models: AdminModelEntry[] }> = [];
  const directoryCalls: DirectoryCall[] = [];

  modelAdminApi.get = async () => saved;
  modelAdminApi.publish = options.publish ?? (async (_url, _credential, _revision, models) => {
    published.push({ models });
    return saved;
  });
  adminUsersApi.list = async (call = {}) => {
    directoryCalls.push(call);
    if (!options.directory) {
      return {
        schema_version: "ai-platform.admin-user-diagnostics.v1" as const,
        users: [user("user-a", "用户 A")],
        total: 1, offset: 0, limit: 100,
      };
    }
    return options.directory(call);
  };

  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const render = async (canManage: boolean) => {
    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, { canManage }));
    });
  };
  await render(true);
  await waitFor(
    React,
    () => container.querySelectorAll("[data-model-admin-control]").length === 1,
    "admin control should mount",
  );

  const find = (selector: string, attribute: string, value?: string) => {
    const match = Array.from(container.querySelectorAll(selector)).find((node) => {
      if (node.getAttribute(attribute) === null) return false;
      return value === undefined || node.getAttribute(attribute) === value;
    });
    assert.ok(match, `expected ${selector}[${attribute}=${value ?? "*"}]`);
    return match as unknown as Element;
  };

  return {
    React,
    container,
    published,
    directoryCalls,
    render,
    // Native click is cancelable: Base UI must prevent the label's second toggle.
    click: async (element: Element) => {
      await React.act(async () => {
        (element as HTMLElement).click();
        await Promise.resolve();
      });
    },
    clickBy: async (selector: string, attribute: string, value?: string) => {
      await React.act(async () => {
        const match = Array.from(container.querySelectorAll(selector)).find((node) => {
          if (node.getAttribute(attribute) === null) return false;
          return value === undefined || node.getAttribute(attribute) === value;
        });
        assert.ok(match, `expected ${selector}[${attribute}=${value ?? "*"}]`);
        (match as HTMLElement).click();
        await Promise.resolve();
      });
    },
    find,
    /** All user-access controls, in table row order. */
    rows: () => container.querySelectorAll("[data-model-user-access]") as unknown as Element[],
    /** Clicks a control inside one row, so two models never get mixed up. */
    clickInRow: async (rowIndex: number, selector: string, attribute: string, value?: string) => {
      await React.act(async () => {
        const rows = container.querySelectorAll("[data-model-user-access]");
        assert.ok(rows[rowIndex], `expected row ${rowIndex}`);
        const match = Array.from((rows[rowIndex] as unknown as {
          querySelectorAll(selector: string): Element[];
        }).querySelectorAll(selector)).find((node) => {
          if (node.getAttribute(attribute) === null) return false;
          return value === undefined || node.getAttribute(attribute) === value;
        });
        assert.ok(match, `expected row ${rowIndex} ${selector}[${attribute}=${value ?? "*"}]`);
        (match as HTMLElement).click();
        await Promise.resolve();
      });
    },
    /** Types into one row's directory search through its real input. */
    searchInRow: async (rowIndex: number, value: string) => {
      await React.act(async () => {
        const rows = container.querySelectorAll("[data-model-user-access]");
        const input = Array.from((rows[rowIndex] as unknown as {
          querySelectorAll(selector: string): object[];
        }).querySelectorAll("input"))
          .find((node) => (node as unknown as Element).getAttribute("aria-label") === "搜索用户");
        assert.ok(input, `expected a search input in row ${rowIndex}`);
        changeInput(input as HTMLInputElement, value);
        await Promise.resolve();
      });
    },
    filterModels: async (value: string) => {
      const input = Array.from(container.querySelectorAll("input"))
        .find((node) => node.getAttribute("aria-label") === "搜索模型");
      assert.ok(input);
      await React.act(async () => { changeInput(input, value); });
    },
    rowText: (rowIndex: number) => text(container.querySelectorAll("[data-model-user-access]")[rowIndex]),
    unmount: async () => {
      await React.act(async () => root.unmount());
      Object.assign(modelAdminApi, originalModelAdmin);
      adminUsersApi.list = originalList;
    },
  };
}

test("the first selection works from a cold page: open, load, pick and save", async () => {
  const view = await mount(model());
  try {
    // Cold state: the picker must not be the disabled-only gate to loading.
    await view.clickBy("button", "data-model-access-mode", "selected");
    await waitFor(
      view.React,
      () => view.directoryCalls.length === 1,
      "switching to a specific subset loads the directory",
    );

    await view.clickBy("button", "data-model-access-picker");
    await waitFor(
      view.React,
      () => text(view.container).includes("用户 A"),
      "the directory list renders after loading",
    );

    const option = Array.from(view.container
      .querySelectorAll<HTMLElement>('[role="checkbox"]'))
      .find((node) => node.getAttribute("role") === "checkbox");
    assert.ok(option, "expected a selectable user option");
    assert.equal(option.tabIndex, 0, "the visible checkbox must participate in keyboard navigation");
    await view.click(option as unknown as Element);
    await view.clickBy("button", "data-model-admin-publish");
    await waitFor(
      view.React,
      () => view.published.length === 1,
      "saving after selecting one user publishes",
    );
    assert.equal(view.published[0].models[0].access_mode, "selected");
    assert.deepEqual(view.published[0].models[0].allowed_user_ids, ["user-a"]);
  } finally {
    await view.unmount();
  }
});

test("a saved restriction echoes its users and keeps them across paging and search", async () => {
  const paged = Array.from({ length: 100 }, (_, index) =>
    user(`user-${String(index + 1).padStart(3, "0")}`, `用户 ${index + 1}`),
  );
  const view = await mount(
    model({ access_mode: "selected", allowed_user_ids: ["user-001", "user-101"] }),
    {
      directory: async (call) => ({
        schema_version: "ai-platform.admin-user-diagnostics.v1" as const,
        users: call.offset ? [user("user-101", "用户 101")] : paged,
        total: 101,
        offset: call.offset ?? 0,
        limit: 100,
      }),
    },
  );
  try {
    // Before any load the saved users are echoed by their raw IDs.
    assert.equal(
      view.container.querySelectorAll("[data-model-access-user='user-101']").length,
      1,
    );

    await view.clickBy("button", "data-model-access-mode", "selected");
    await waitFor(view.React, () => view.directoryCalls.length === 1, "first page loads");
    await view.clickBy("button", "data-model-access-picker");
    await waitFor(
      view.React,
      () => view.container.querySelectorAll("[data-model-access-directory]").length === 1,
      "opening the picker renders the directory",
    );

    // The 101st user is reachable by paging, not dropped by a fixed cap.
    await view.clickBy("button", "data-model-access-load-more");
    await waitFor(
      view.React,
      () => view.directoryCalls.length === 2,
      "loading more requests the next page",
    );
    assert.equal(view.directoryCalls[1].offset, 100);
    await waitFor(
      view.React,
      () => text(view.container).includes("用户 101"),
      "the second page merges into the directory",
    );

    // Selecting an additional user from the loaded page persists.
    const option = Array.from(view.container
      .querySelectorAll<HTMLElement>('[role="checkbox"]'))
      .find((node) => node.getAttribute("aria-checked") === "false");
    assert.ok(option, "an unselected user must be selectable");
    await view.click(option as unknown as Element);
    await waitFor(
      view.React,
      () => view.container.querySelectorAll("[data-model-access-user]").length === 3,
      "adding a user keeps the saved two",
    );

    // Selection survives a server-side search that replaces the loaded page.
    const search = Array.from(view.container
      .querySelectorAll("input"))
      .find((node) => node.getAttribute("aria-label") === "搜索用户");
    assert.ok(search, "expected the directory search input");
    await view.React.act(async () => { changeInput(search, "不存在"); });
    await waitFor(
      view.React,
      () => view.directoryCalls.length === 3,
      "searching re-queries the server instead of filtering one page",
    );
    assert.equal(view.directoryCalls[2].search, "不存在");
    assert.equal(view.directoryCalls[2].offset, 0, "a search starts from the first page");
    assert.equal(
      view.container.querySelectorAll("[data-model-access-user='user-101']").length,
      1,
      "searching must not drop a selected user",
    );

    // The saved chip resolves to the directory display name once loaded.
    await waitFor(
      view.React,
      () => {
        const chip = view.container
          .querySelectorAll("[data-model-access-user='user-101']")[0];
        return Boolean(chip) && text(chip).includes("用户 101");
      },
      "a loaded saved user shows its directory name",
    );
  } finally {
    await view.unmount();
  }
});

test("a failed directory load stays retryable on the same page", async () => {
  let attempts = 0;
  const view = await mount(model(), {
    directory: async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new ApiRequestError("目录暂不可用", 503);
      }
      return {
        schema_version: "ai-platform.admin-user-diagnostics.v1" as const,
        users: [user("user-a", "用户 A")],
        total: 1, offset: 0, limit: 100,
      };
    },
  });
  try {
    await view.clickBy("button", "data-model-access-mode", "selected");
    await waitFor(
      view.React,
      () => text(view.container).includes("模型服务暂不可用"),
      "the failure is reported",
    );

    await view.clickBy("button", "data-model-access-retry");
    await waitFor(
      view.React,
      () => attempts === 2,
      "retry issues a new request instead of reusing a cached empty directory",
    );
    assert.ok(
      !text(view.container).includes("用户目录为空"),
      "a failure must not be cached as a successful empty directory",
    );
  } finally {
    await view.unmount();
  }
});

test("losing management permission or unmounting discards a late directory reply", async () => {
  let release: ((value: {
    schema_version: "ai-platform.admin-user-diagnostics.v1";
    users: AdminUserSummary[];
    total: number;
    offset: number;
    limit: number;
  }) => void) | null = null;
  const view = await mount(model(), {
    directory: () => new Promise((resolve) => {
      release = resolve as typeof release;
    }),
  });
  try {
    await view.clickBy("button", "data-model-access-mode", "selected");
    await waitFor(view.React, () => view.directoryCalls.length === 1, "directory request starts");
    const signal = view.directoryCalls[0].signal;
    assert.ok(signal, "the directory request must be cancellable");

    await view.render(false);
    assert.equal(signal.aborted, true, "losing management must abort the request");

    (release as ((value: unknown) => void) | null)?.({
      schema_version: "ai-platform.admin-user-diagnostics.v1",
      users: [user("user-z", "泄漏用户")],
      total: 1, offset: 0, limit: 100,
    });
    await view.React.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.ok(
      !text(view.container).includes("泄漏用户"),
      "a reply after permission loss must not be written back",
    );
  } finally {
    await view.unmount();
  }
});

test("two models keep independent searches: no cross-row results and no mixed paging", async () => {
  const alice = Array.from({ length: 101 }, (_, index) =>
    user(`alice-${index + 1}`, index === 0 ? "Alice Zhang" : `Alice ${index + 1}`),
  );
  const bob = Array.from({ length: 101 }, (_, index) =>
    user(`bob-${index + 1}`, index === 0 ? "Bob Li" : `Bob ${index + 1}`),
  );
  const view = await mount(
    [
      model({ id: "mdl_a", value: "mock/a", label: "模型 A", access_mode: "selected", allowed_user_ids: [] }),
      model({ id: "mdl_b", value: "mock/b", label: "模型 B", is_default: false, access_mode: "selected", allowed_user_ids: [] }),
    ],
    {
      directory: async (call) => {
        const pool = call.search === "Alice" ? alice : bob;
        const offset = call.offset ?? 0;
        return {
          schema_version: "ai-platform.admin-user-diagnostics.v1" as const,
          users: pool.slice(offset, offset + 100),
          total: pool.length,
          offset,
          limit: 100,
        };
      },
    },
  );
  try {
    // Row A searches Alice; row B stays on the default directory.
    await view.clickInRow(0, "button", "data-model-access-picker");
    await view.searchInRow(0, "Alice");
    await waitFor(
      view.React,
      () => view.directoryCalls.some((call) => call.search === "Alice"),
      "row A searches the server",
    );
    assert.match(view.rowText(0), /Alice Zhang/, "row A shows its own results");

    await view.clickInRow(1, "button", "data-model-access-picker");
    // B must not display A's results.
    assert.doesNotMatch(
      view.rowText(1),
      /Alice Zhang/,
      "row B must not render row A's search results",
    );

    // B loads its own first page, then appends from its own query.
    await view.searchInRow(1, "");
    await waitFor(
      view.React,
      () => view.directoryCalls.length >= 2,
      "row B issues its own default query",
    );
    assert.match(view.rowText(1), /Bob Li/, "row B shows the default directory");

    await view.clickInRow(1, "button", "data-model-access-load-more");
    await waitFor(
      view.React,
      () => view.directoryCalls.filter((call) => !call.search).length >= 2,
      "row B appends from its own query",
    );
    const bPages = view.directoryCalls.filter((call) => !call.search);
    // Each default-query request restarts at 0; only the append advances to 100.
    assert.equal(
      bPages[bPages.length - 1].offset,
      100,
      "row B's last default request appends from its own page count",
    );
    assert.ok(
      bPages.slice(0, -1).every((call) => call.offset === 0),
      "a fresh default query always restarts at the first page",
    );

    // Row A's Alice search is untouched by B's paging.
    assert.match(view.rowText(0), /Alice Zhang/);
    assert.doesNotMatch(view.rowText(0), /Bob Li/, "row A keeps its own results");

    // Filtering unmounts the row, but its query must stay with the saved page.
    await view.filterModels("模型 B");
    await view.filterModels("");
    await view.clickInRow(0, "button", "data-model-access-picker");
    const search = (view.rows()[0] as unknown as {
      querySelectorAll(selector: string): Array<{ value?: string }>;
    }).querySelectorAll("input")[0];
    assert.equal(search.value, "Alice", "remount restores the query alongside its results");
    await view.clickInRow(0, "button", "data-model-access-load-more");
    assert.equal(view.directoryCalls.at(-1)?.search, "Alice");
    assert.equal(view.directoryCalls.at(-1)?.offset, 100);
  } finally {
    await view.unmount();
  }
});

test("a late search reply cannot replace the same model's newer query", async () => {
  let release!: (page: Awaited<ReturnType<typeof adminUsersApi.list>>) => void;
  const view = await mount(model({ access_mode: "selected", allowed_user_ids: ["user-a"] }), {
    directory: async (call) => {
      if (call.search === "old") return new Promise((resolve) => { release = resolve; });
      return {
        schema_version: "ai-platform.admin-user-diagnostics.v1", users: [user("user-new", "新结果")],
        total: 1, offset: 0, limit: 100,
      };
    },
  });
  try {
    await view.clickBy("button", "data-model-access-picker");
    await view.searchInRow(0, "old");
    const oldSignal = view.directoryCalls.at(-1)?.signal;
    await view.searchInRow(0, "new");
    assert.equal(oldSignal?.aborted, true);
    await view.React.act(async () => {
      release({
        schema_version: "ai-platform.admin-user-diagnostics.v1", users: [user("user-old", "迟到结果")],
        total: 1, offset: 0, limit: 100,
      });
    });
    assert.match(view.rowText(0), /新结果/);
    assert.doesNotMatch(view.rowText(0), /迟到结果/);
    assert.equal(view.container.querySelector<HTMLInputElement>('[aria-label="搜索用户"]')?.value, "new");
    assert.equal(view.container.querySelectorAll('[data-model-access-user="user-a"]').length, 1);
  } finally {
    await view.unmount();
  }
});

test("an empty subset on a disabled available row blocks a real save of a valid draft", async () => {
  // A valid, publishable default model plus a second empty-selected row.
  const view = await mount(
    [
      model({ id: "mdl_a", value: "mock/a", label: "模型 A" }),
      model({
        id: "mdl_b", value: "mock/b", label: "模型 B", is_default: false, enabled: false,
        access_mode: "selected", allowed_user_ids: [],
      }),
    ],
    {},
  );
  try {
    // Make a real, saveable draft change on the valid default model.
    const fields = Array.from(view.container.querySelectorAll("input"));
    const tokenField = fields.find(
      (node) => node.getAttribute("aria-label") === "mock/a 最大输入 Token",
    );
    assert.ok(tokenField, "expected a token field for the default model");
    await view.React.act(async () => { changeInput(tokenField, "64000"); });
    assert.match(text(view.container), /有未保存的修改/, "the draft is genuinely dirty");

    await view.clickBy("button", "data-model-admin-publish");
    await waitFor(
      view.React,
      () => text(view.container).includes("请为 模型 B 至少选择一名用户"),
      "publication must produce its own validation error, not just the existing inline hint",
    );
    assert.equal(
      view.published.length,
      0,
      "a dirty valid draft must still not publish while a row has an empty subset",
    );
  } finally {
    await view.unmount();
  }
});


test("a saving lock disables the open picker so a selection cannot be dropped", async () => {
  let release: ((value: AdminModelState) => void) | null = null;
  const view = await mount(model({ access_mode: "selected", allowed_user_ids: [] }), {
    publish: () => new Promise<AdminModelState>((resolve) => {
      release = resolve as typeof release;
    }),
  });
  try {
    // Load the directory and open the picker before saving starts.
    await view.clickBy("button", "data-model-access-picker");
    await waitFor(
      view.React,
      () => text(view.container).includes("用户 A"),
      "directory loads",
    );
    const option = Array.from(view.container
      .querySelectorAll<HTMLElement>('[role="checkbox"]'))
      .find((node) => node.getAttribute("role") === "checkbox");
    assert.ok(option);
    await view.click(option as unknown as Element);

    const callsBeforeSave = view.directoryCalls.length;
    await view.clickBy("button", "data-model-admin-publish");
    await waitFor(view.React, () => view.published.length === 0 && release !== null, "publish starts");

    // While the request is in flight the picker is closed and its controls are gone,
    // so no click can be silently discarded by the parent's request guard.
    assert.equal(
      view.container.querySelectorAll("[data-model-access-directory]").length,
      0,
      "an open picker must close while saving",
    );
    const picker = Array.from(view.container
      .querySelectorAll("button"))
      .find((node) => node.getAttribute("data-model-access-picker") !== null);
    assert.ok(picker);
    assert.equal(picker.disabled, true, "the picker is disabled while saving");
    assert.equal(view.container.querySelector<HTMLInputElement>('[aria-label="搜索模型"]')?.disabled, true);
    assert.equal(
      view.directoryCalls.length,
      callsBeforeSave,
      "a locked control must not start new directory requests",
    );
  } finally {
    (release as ((value: AdminModelState) => void) | null)?.(state(model({ access_mode: "selected", allowed_user_ids: ["user-a"] })));
    await view.unmount();
  }
});
