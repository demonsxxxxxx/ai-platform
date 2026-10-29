import assert from "node:assert/strict";
import test from "node:test";

import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import type { ToolState } from "../../../types";

const initialTools: ToolState[] = [
  {
    name: "alpha_search",
    description: "Find documents",
    category: "mcp",
    enabled: true,
    parameters: [
      { name: "query", type: "string", description: "Search text", required: true },
    ],
  },
  {
    name: "beta_read",
    description: "Read a document",
    category: "mcp",
    enabled: true,
    parameters: [],
  },
];

function changeInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(input),
    "value",
  )?.set;
  assert.ok(setter);
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

test("ToolSelector preserves its mounted modal across search and selection updates", async (t) => {
  const dom = installSelectTestDom();
  const container = document.createElement("div");
  container.id = "root";
  document.body.appendChild(container);
  try {
    // React DOM must observe the mounted document when it initializes its events.
    const [
      { act, createElement, useState },
      { createLobeTestRoot },
      { MemoryRouter },
      { ToolSelector },
      { ChatMcpCatalogContext },
    ] = await Promise.all([
      import("react"),
      import("../../common/__tests__/lobeUiTestRoot"),
      import("react-router-dom"),
      import("../ToolSelector"),
      import("../../../hooks/useTools"),
      import("../../../i18n"),
    ]);

    for (const controlled of [true, false]) {
      await t.test(controlled ? "controlled" : "uncontrolled", async () => {
        const container = document.getElementById("root");
        assert.ok(container);
        const root = await createLobeTestRoot(container);

        function Harness() {
          const [open, setOpen] = useState(true);
          const [tools, setTools] = useState(initialTools);
          return createElement(
            MemoryRouter,
            null,
            createElement(
              ChatMcpCatalogContext.Provider,
              { value: { catalogState: { status: "ready", unavailable: [] } } },
              controlled
                ? createElement("button", { onClick: () => setOpen(true) }, "Open")
                : null,
              createElement(ToolSelector, {
                tools,
                enabledCount: tools.filter((tool) => tool.enabled).length,
                totalCount: tools.length,
                onToggleTool: (name: string) => {
                  setTools((previous) => previous.map((tool) =>
                    tool.name === name ? { ...tool, enabled: !tool.enabled } : tool,
                  ));
                },
                onToggleCategory: () => {},
                ...(controlled ? { isOpen: open, onOpenChange: setOpen } : {}),
              }),
            ),
          );
        }

        try {
          await act(async () => { root.render(createElement(Harness)); });
          const trigger = container.querySelector("button");
          assert.ok(trigger);
          if (!controlled) {
            await act(async () => { trigger.click(); });
          }
          const modal = document.querySelector("[data-composer-mcp-selector]");
          const input = modal?.querySelector("input");
          assert.ok(modal);
          assert.ok(input);
          assert.ok(modal.querySelector('[data-tool-selector-header]')?.contains(input));
          const actions = [...modal.querySelectorAll('button')].map((button) => button.textContent?.trim());
          for (const label of ["全选", "取消全选", "添加"]) assert.equal(actions.includes(label), false);
          assert.equal(modal.querySelectorAll('[data-tool-selector-list]').length, 1);
          input.focus();
          for (const value of ["a", "al", "alp"]) {
            await act(async () => { changeInput(input, value); });
            assert.ok(modal.querySelector("input") === input, "search input identity must be stable");
            assert.ok(document.querySelector("[data-composer-mcp-selector]") === modal, "modal identity must be stable");
            assert.equal(input.isConnected, true);
            assert.ok(document.activeElement === input, "typing must retain search focus");
            assert.equal(input.value, value);
            assert.equal(input.selectionStart, value.length);
            assert.equal(input.selectionEnd, value.length);
          }
          assert.deepEqual(
            Array.from(modal.querySelectorAll("[data-composer-mcp-row]"),
              (row) => row.getAttribute("data-composer-mcp-row")),
            ["alpha_search"],
          );

          const scroller = modal.querySelector<HTMLElement>(".overflow-y-auto");
          const row = modal.querySelector<HTMLElement>('[data-composer-mcp-row="alpha_search"]');
          const expand = row?.querySelector("button");
          assert.ok(scroller);
          assert.ok(row);
          assert.ok(expand);
          scroller.scrollTop = 64;
          await act(async () => { expand.click(); });
          assert.ok(modal.querySelector(".overflow-y-auto") === scroller);
          assert.equal(scroller.scrollTop, 64);
          assert.match(modal.querySelector("table")?.textContent ?? "", /query/);
          await act(async () => { row.click(); });
          assert.equal(row.getAttribute("data-composer-mcp-state"), "disabled");
          assert.ok(document.querySelector("[data-composer-mcp-selector]") === modal, "modal identity must be stable");
          assert.ok(modal.querySelector("input") === input, "search input identity must be stable");
          assert.ok(document.activeElement === input);
          assert.equal(scroller.scrollTop, 64);

          const backdrop = document.querySelector<HTMLElement>("[data-composer-mcp-backdrop]");
          assert.ok(backdrop);
          await act(async () => {
            backdrop.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true, cancelable: true }));
            backdrop.click();
            await new Promise((resolve) => setTimeout(resolve, 300));
          });
          assert.equal(document.querySelector("[data-composer-mcp-selector]"), null);
          assert.equal(document.body.style.overflow, "");
          await act(async () => { trigger.click(); });
          const reopened = document.querySelector("[data-composer-mcp-selector] input");
          assert.ok(reopened);
          assert.ok(reopened !== input, "only closing should unmount the input");
          assert.equal((reopened as HTMLInputElement).value, "alp");
        } finally {
          await act(async () => { root.unmount(); });
        }
        assert.equal(document.body.style.overflow, "");
      });
    }
  } finally {
    dom.close();
  }
});

test("group controls preserve disabled tools, mixed selection, and connection identity", async () => {
  const dom = installSelectTestDom();
  const container = document.createElement("div");
  document.body.appendChild(container);
  try {
    const [
      { act, createElement, useMemo, useState },
      { createLobeTestRoot },
      { MemoryRouter },
      { ToolSelector },
      { ChatMcpCatalogContext },
    ] = await Promise.all([
      import("react"),
      import("../../common/__tests__/lobeUiTestRoot"),
      import("react-router-dom"),
      import("../ToolSelector"),
      import("../../../hooks/useTools"),
    ]);
    const initialTools: ToolState[] = [
      { name: "a::enabled", description: "", category: "mcp", server: "gateway-a", upstream_server: "same-project", parameters: [], enabled: true },
      { name: "a::mixed", description: "", category: "mcp", server: "gateway-a", upstream_server: "same-project", parameters: [], enabled: false },
      { name: "a::disabled", description: "", category: "mcp", server: "gateway-a", upstream_server: "same-project", parameters: [], system_disabled: true, enabled: false },
      { name: "b::same-label", description: "", category: "mcp", server: "gateway-b", upstream_server: "same-project", parameters: [], enabled: false },
      { name: "a::unknown", description: "", category: "mcp", server: "gateway-a", upstream_server: null, parameters: [], enabled: false },
    ];
    const root = await createLobeTestRoot(container);
    function Harness() {
      const [tools, setTools] = useState(initialTools);
      const contextValue = useMemo(() => ({
        catalogState: { status: "ready" as const, unavailable: [] },
      }), []);
      return createElement(
        MemoryRouter,
        null,
        createElement(
          ChatMcpCatalogContext.Provider,
          { value: contextValue },
          createElement(ToolSelector, {
            tools,
            enabledCount: tools.filter((tool) => tool.enabled).length,
            totalCount: tools.length,
            isOpen: true,
            onOpenChange: () => {},
            onToggleTool: (name: string) => setTools((previous) => previous.map((tool) =>
              tool.name === name ? { ...tool, enabled: !tool.enabled } : tool,
            )),
            onToggleCategory: () => {},
          }),
        ),
      );
    }
    try {
      await act(async () => { root.render(createElement(Harness)); });
      const groups = Array.from(document.querySelectorAll<HTMLElement>("[data-composer-mcp-group]"));
      assert.equal(groups.length, 3);
      assert.equal(new Set(groups.map((group) => group.dataset.composerMcpGroup)).size, 3);
      const sameProjectGroup = groups.find((group) => group.textContent?.includes("same-project"));
      assert.ok(sameProjectGroup);
      const groupToggle = sameProjectGroup.querySelector<HTMLElement>(
        '[aria-label="切换工具来源 same-project"]',
      );
      assert.ok(groupToggle);
      await act(async () => { groupToggle.click(); });
      assert.equal(document.querySelector('[data-composer-mcp-row="a::enabled"]')?.getAttribute("data-composer-mcp-state"), "enabled");
      assert.equal(document.querySelector('[data-composer-mcp-row="a::mixed"]')?.getAttribute("data-composer-mcp-state"), "enabled");
      assert.equal(document.querySelector('[data-composer-mcp-row="a::disabled"]')?.getAttribute("data-composer-mcp-state"), "denied");
      assert.equal(document.querySelector('[data-composer-mcp-row="b::same-label"]')?.getAttribute("data-composer-mcp-state"), "disabled");

      const unknownGroup = groups.find((group) => group.textContent?.includes("未提供服务来源"));
      assert.ok(unknownGroup);
      const collapseUnknown = unknownGroup.querySelector<HTMLButtonElement>("button[aria-expanded]");
      assert.ok(collapseUnknown);
      await act(async () => { collapseUnknown.click(); });
      assert.equal(document.querySelector('[data-composer-mcp-row="a::unknown"]'), null);
      const input = document.querySelector<HTMLInputElement>("[data-composer-mcp-selector] input");
      assert.ok(input);
      await act(async () => { changeInput(input, "未提供服务来源"); });
      assert.ok(document.querySelector('[data-composer-mcp-row="a::unknown"]'));
    } finally {
      await act(async () => { root.unmount(); });
    }
  } finally {
    dom.close();
  }
});
