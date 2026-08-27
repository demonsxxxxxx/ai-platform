import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import type { Message, MessagePart } from "../../../../types";
import { normalizeMessageTextLogicalIds } from "../../../../hooks/useAgent/eventProcessor";
import {
  createMessagePartRenderKeys,
  MessagePartRenderer,
} from "../MessagePartRenderer";

function renderTextParts(
  root: ReturnType<typeof createRoot>,
  ownerId: string,
  parts: MessagePart[],
) {
  const keys = createMessagePartRenderKeys(ownerId, parts);
  root.render(
    createElement(
      "div",
      null,
      parts.map((part, index) =>
        createElement(
          "div",
          { key: keys[index], "data-part-key": keys[index] },
          createElement(MessagePartRenderer, {
            part,
            messageId: ownerId,
            partIndex: index,
            isLast: index === parts.length - 1,
          }),
        ),
      ),
    ),
  );
  return keys;
}

test("recursive subagent rendering preserves hierarchy and node identity", () => {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  for (const key of ["window", "document", "navigator", "HTMLElement", "Node"] as const) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: dom.window[key],
    });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
    configurable: true,
    value: true,
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);
  const tree = (terminal: boolean): MessagePart => ({
    type: "subagent",
    agent_id: "agent-root",
    public_operation_id: "agent-root",
    agent_name: "Root task",
    input: "",
    isPending: !terminal,
    status: terminal ? "complete" : "running",
    depth: 1,
    parts: [
      {
        type: "subagent",
        agent_id: "agent-child",
        public_operation_id: "agent-child",
        parent_agent_id: "agent-root",
        agent_name: "Child task",
        input: "",
        isPending: !terminal,
        status: terminal ? "complete" : "running",
        depth: 2,
        parts: [
          {
            type: "subagent",
            agent_id: "agent-grandchild",
            public_operation_id: "agent-grandchild",
            parent_agent_id: "agent-child",
            agent_name: "Grandchild task",
            input: "",
            isPending: !terminal,
            status: terminal ? "complete" : "running",
            depth: 3,
            parts: [],
          },
        ],
      },
    ],
  });

  try {
    act(() => {
      root.render(
        createElement(MessagePartRenderer, {
          part: tree(false),
          messageId: "reducer-message",
          partIndex: 0,
          isLast: true,
        }),
      );
    });
    const rootNode = container.querySelector('[data-subagent-id="agent-root"]');
    const childNode = container.querySelector('[data-subagent-id="agent-child"]');
    const grandchildNode = container.querySelector(
      '[data-subagent-id="agent-grandchild"]',
    );
    assert.ok(rootNode);
    assert.ok(childNode);
    assert.ok(grandchildNode);
    assert.equal(rootNode.contains(childNode), true);
    assert.equal(childNode.contains(grandchildNode), true);
    assert.equal(childNode.getAttribute("data-parent-agent-id"), "agent-root");
    assert.equal(
      grandchildNode.getAttribute("data-parent-agent-id"),
      "agent-child",
    );

    act(() => {
      root.render(
        createElement(MessagePartRenderer, {
          part: tree(true),
          messageId: "reducer-message",
          partIndex: 0,
          isLast: true,
        }),
      );
    });
    assert.equal(
      rootNode.isSameNode(
        container.querySelector('[data-subagent-id="agent-root"]'),
      ),
      true,
    );
    assert.equal(
      childNode.isSameNode(
        container.querySelector('[data-subagent-id="agent-child"]'),
      ),
      true,
    );
    assert.equal(
      grandchildNode.isSameNode(
        container.querySelector('[data-subagent-id="agent-grandchild"]'),
      ),
      true,
    );
  } finally {
    act(() => root.unmount());
    dom.window.close();
  }
});

test("text replacement and replay preserve deterministic render identities", () => {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  for (const key of ["window", "document", "navigator", "HTMLElement", "Node"] as const) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: dom.window[key],
    });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
    configurable: true,
    value: true,
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);

  try {
    const first: MessagePart[] = [
      { type: "text", content: "Hel", logical_id: "protocol-message:text:0:0:root" },
    ];
    let firstKeys: string[] = [];
    act(() => {
      firstKeys = renderTextParts(root, "reducer-message", first);
    });
    const firstNode = container.querySelector(`[data-part-key="${firstKeys[0]}"]`);
    assert.ok(firstNode);

    const deltaReplacement: MessagePart[] = [
      { type: "text", content: "Hello", logical_id: "protocol-message:text:0:0:root" },
    ];
    act(() => {
      renderTextParts(root, "reducer-message", deltaReplacement);
    });
    const deltaNode = container.querySelector(`[data-part-key="${firstKeys[0]}"]`);
    assert.ok(deltaNode);
    assert.equal(firstNode.isSameNode(deltaNode), true);

    const finalReplacement: MessagePart[] = [
      { type: "text", content: "Hello, world!", logical_id: "protocol-message:text:0:0:root" },
    ];
    act(() => {
      renderTextParts(root, "reducer-message", finalReplacement);
    });
    const finalNode = container.querySelector(`[data-part-key="${firstKeys[0]}"]`);
    assert.ok(finalNode);
    assert.equal(firstNode.isSameNode(finalNode), true);

    const hydratedMessage = normalizeMessageTextLogicalIds({
      id: "persisted-message",
      role: "assistant",
      content: "",
      timestamp: new Date("2026-01-01T00:00:00Z"),
      parts: [
        { type: "text", content: "first hydrated segment" },
        { type: "text", content: "second hydrated segment" },
      ],
    } satisfies Message);
    const replayedMessage = normalizeMessageTextLogicalIds({
      ...hydratedMessage,
      parts: hydratedMessage.parts?.map((part) => ({ ...part })),
    });
    const hydratedKeys = createMessagePartRenderKeys(
      hydratedMessage.id,
      hydratedMessage.parts ?? [],
    );
    const replayedKeys = createMessagePartRenderKeys(
      replayedMessage.id,
      replayedMessage.parts ?? [],
    );
    assert.deepEqual(replayedKeys, hydratedKeys);
    assert.equal(new Set(hydratedKeys).size, 2);
    assert.doesNotMatch(hydratedKeys.join("|"), /hydrated segment/);
  } finally {
    act(() => root.unmount());
    dom.window.close();
  }
});
