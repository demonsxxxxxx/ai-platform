import assert from "node:assert/strict";
import test from "node:test";

// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
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
  const dom = new JSDOM(
    "<!doctype html><html><body><div id='root'></div></body></html>",
    { url: "http://localhost/chat/session-a", pretendToBeVisual: true },
  );
  const globalValues: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    Node: dom.window.Node,
    Event: dom.window.Event,
    MouseEvent: dom.window.MouseEvent,
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previousDescriptors = new Map(
    Object.keys(globalValues).map((key) => [
      key,
      Object.getOwnPropertyDescriptor(globalThis, key),
    ]),
  );
  for (const [key, value] of Object.entries(globalValues)) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    });
  }

  try {
    // React DOM must observe the mounted document when it initializes its events.
    const [
      { act, createElement, useState },
      { createRoot },
      { MemoryRouter },
      { ToolSelector },
      { ChatMcpCatalogContext },
    ] = await Promise.all([
      import("react"),
      import("react-dom/client"),
      import("react-router-dom"),
      import("../ToolSelector"),
      import("../../../hooks/useTools"),
      import("../../../i18n"),
    ]);

    for (const controlled of [true, false]) {
      await t.test(controlled ? "controlled" : "uncontrolled", async () => {
        const container = document.getElementById("root");
        assert.ok(container);
        const root = createRoot(container);

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
                onToggleAll: () => {},
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

          const backdrop = document.querySelector<HTMLElement>("[data-yields-sidebar]");
          assert.ok(backdrop);
          await act(async () => { backdrop.click(); });
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
    dom.window.close();
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
});
