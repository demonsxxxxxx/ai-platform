import test from "node:test";
import assert from "node:assert/strict";
// jsdom 26 ships no declarations; the harness uses only its runtime constructor.
// @ts-expect-error jsdom is the sole pinned test runtime dependency.
import { JSDOM } from "jsdom";
import { act, createElement, useEffect, useState, type ReactNode } from "react";
import { adaptPublicRunStreamEventV4 } from "../publicEventAdapter";
import { acceptV4TerminalFence, handlePublicRunStreamFrameV4Result, type EventHandlerContext } from "../../../../hooks/useAgent/eventHandlers";
import { processMessageEvent } from "../../../../hooks/useAgent/eventProcessor";
import { createRoot, type Root } from "react-dom/client";
import { ThreadPrimitive } from "@assistant-ui/react";
import { AssistantUiProjection } from "../AssistantUiProjection";
import { AssistantUiMessageFrame } from "../MessageFrame";
import { MessagePartRenderer } from "../../ChatMessage/MessagePartRenderer";
import { closePersistentToolPanel, getPersistentToolPanelState } from "../../ChatMessage/items/persistentToolPanelState";
import type { Message, MessagePart } from "../../../../types";

const handlePublicRunStreamFrameV4 = (
  args: Parameters<typeof handlePublicRunStreamFrameV4Result>[0],
) => {
  const result = handlePublicRunStreamFrameV4Result(args);
  return result.kind === "applied" || result.kind === "deferred";
};

function setupDom(): { container: HTMLDivElement; root: Root; cleanup: () => void } {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  const windowKeys = ["window", "document", "navigator", "HTMLElement", "Node", "Event", "KeyboardEvent"] as const;
  for (const key of windowKeys) {
    Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", { configurable: true, value: true, writable: true });
  class TestResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: TestResizeObserver });
  Object.defineProperty(globalThis, "MutationObserver", { configurable: true, value: dom.window.MutationObserver });
  Object.defineProperty(dom.window.HTMLElement.prototype, "scrollTo", { configurable: true, value: () => undefined });
  Object.defineProperty(globalThis, "cancelAnimationFrame", { configurable: true, value: () => undefined });
  Object.defineProperty(globalThis, "requestAnimationFrame", {
    configurable: true,
    value: (callback: FrameRequestCallback) => { callback(Date.now()); return 0; },
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);
  return {
    container,
    root,
    cleanup: () => {
      act(() => { root.unmount(); });
      dom.window.close();
    },
  };
}

function renderProjection(
  root: Root,
  message: Message,
  actions: { sendMessage: (content: string) => Promise<void>; cancel: () => Promise<void>; reconnect: () => Promise<void>; loadHistory: () => Promise<void> },
): void {
  const renderPart = (part: MessagePart, partIndex: number): ReactNode => {
    if (part.type === "artifact") {
      return createElement(MessagePartRenderer, {
        key: `${message.id}:artifact:${part.artifact_id}`,
        part,
        messageId: message.id,
        partIndex,
        isLast: partIndex === (message.parts?.length ?? 1) - 1,
      });
    }
    if (part.type === "subagent") {
      return createElement(MessagePartRenderer, {
        key: `${message.id}:subagent:${part.public_operation_id || part.agent_id}`,
        part,
        messageId: message.id,
        partIndex,
        isLast: partIndex === (message.parts?.length ?? 1) - 1,
      });
    }
    if (part.type === "tool") {
      return createElement(MessagePartRenderer, {
        key: `${message.id}:tool:${part.public_operation_id || part.id}`,
        part,
        messageId: message.id,
        partIndex,
        isLast: partIndex === (message.parts?.length ?? 1) - 1,
      });
    }
    return null;
  };

  act(() => {
    root.render(
      createElement(
        AssistantUiProjection,
        {
          messages: [message],
          isRunning: Boolean(message.isStreaming),
          actions,
          children: createElement(
            ThreadPrimitive.Viewport,
            null,
            createElement(
              ThreadPrimitive.Unstable_MessageById,
              {
                messageId: message.id,
                components: {
                  Message: () => createElement(
                    AssistantUiMessageFrame,
                    null,
                    createElement("p", { "data-message-content": true }, message.content),
                    ...(message.parts || []).map(renderPart),
                  ),
                },
              },
            ),
          ),
        },
      ),
    );
  });
}

function message(parts: MessagePart[]): Message {
  return {
    id: "message-1",
    runId: "run-1",
    role: "assistant",
    content: "visible answer",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts,
  };
}

test("mounted projection uses the production canonical tool lifecycle", () => {
  const dom = setupDom();
  const actions = {
    sendMessage: async () => undefined,
    cancel: async () => undefined,
    reconnect: async () => undefined,
    loadHistory: async () => undefined,
  };
  try {
    renderProjection(dom.root, message([{
      type: "tool",
      name: "Bash: cat /workspace/private --token secret",
      args: { command: "cat /workspace/private --token secret" },
      result: "private result",
      status: "completed",
      public_operation_id: "operation-read-1",
      public_category: "read",
      isPending: false,
    }]), actions);
    const projection = dom.container.querySelector("[data-assistant-ui-projection]");
    const frame = dom.container.querySelector("[data-assistant-ui-message]");
    const tool = [...dom.container.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("读取：Read"),
    );
    assert.ok(projection);
    assert.ok(frame);
    assert.equal(projection?.getAttribute("role"), "log");
    assert.equal(projection?.getAttribute("aria-live"), "polite");
    for (const requiredClass of [
      "min-h-0",
      "flex",
      "flex-1",
      "flex-col",
      "overflow-hidden",
    ]) {
      assert.equal(
        projection?.classList.contains(requiredClass),
        true,
        `projection must retain ${requiredClass} so the transcript has usable height`,
      );
    }
    assert.equal(frame?.getAttribute("role"), "group");
    assert.equal(frame?.getAttribute("tabindex"), "0");
    assert.ok(tool);
    assert.equal(tool.getAttribute("aria-expanded"), null);
    assert.doesNotMatch(
      dom.container.innerHTML,
      /Bash:|workspace|private result|command/,
    );
    tool.focus();
    assert.equal(dom.container.ownerDocument.activeElement, tool);
  } finally {
    dom.cleanup();
  }
});

test("mounted artifact card exposes only its safe accessible label", () => {
  const dom = setupDom();
  const actions = { sendMessage: async () => undefined, cancel: async () => undefined, reconnect: async () => undefined, loadHistory: async () => undefined };
  try {
    renderProjection(dom.root, message([{
      type: "artifact",
      artifact_id: "artifact-public-1",
      artifact_type: "document",
      label: "Artifact",
      content_type: "application/octet-stream",
      size_bytes: 128,
      download_url: undefined,
    }]), actions);
    const artifact = dom.container.querySelector(
      '[role="group"][aria-label="Artifact"]',
    );
    assert.ok(artifact);
    assert.equal(artifact?.getAttribute("aria-label"), "Artifact");
    assert.doesNotMatch(dom.container.textContent || "", /artifact-public-1|private|secret|C:\\\\Users/iu);
  } finally {
    dom.cleanup();
  }
});

test("mounted production fence owner accepts its matching end once and rejects a foreign end", async () => {
  const dom = setupDom();
  const terminalEvent = adaptPublicRunStreamEventV4({
    eventHeader: "run.succeeded",
    transportCursor: "run-1:1:1-0",
    generation: 3,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: "terminal-1",
      run_id: "run-1",
      message_id: "message-1",
      seq: 1,
      event_type: "run.succeeded",
      stream_incarnation: 1,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { terminal_event_id: "terminal-1", hydrate_required: true },
    },
  }, { runId: "run-1", streamIncarnation: 1, generation: 3 });
  assert.ok(terminalEvent);
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 3 },
    v4TerminalFenceRef: { current: null },
  } as unknown as EventHandlerContext;
  function ProductionFinalizationOwner() {
    const [accepted, setAccepted] = useState(0);
    useEffect(() => {
      acceptV4TerminalFence(terminalEvent!, ctx, "terminal-1", 3, () =>
        setAccepted((value) => value + 1),
      );
    }, []);
    return createElement("div", { "data-terminal-state": String(accepted) }, "terminal");
  }
  try {
    await act(async () => { dom.root.render(createElement(ProductionFinalizationOwner)); });
    const endFrame = {
      frame: {
        eventHeader: "stream.end",
        transportCursor: "run-1:1:2-0",
        generation: 3,
        value: {
          schema: "ai-platform.public-run-stream-control.v4",
          event_id: "end-1",
          run_id: "run-1",
          message_id: null,
          seq: null,
          event_type: "stream.end",
          stream_incarnation: 1,
          replayable: true,
          trace_ref: null,
          causation_event_id: null,
          emitted_at: "2026-01-01T00:00:01Z",
          payload: { terminal_event_id: "terminal-1" },
        },
      },
      adapterBinding: { runId: "run-1", streamIncarnation: 1, generation: 3 },
      messageId: "message-1",
      ctx,
      binding: { sessionId: "session-1", runId: "run-1", streamVersion: 3, streamIncarnation: 1, generation: 3 },
      currentGeneration: 3,
    };
    const adaptedEnd = adaptPublicRunStreamEventV4(endFrame.frame, endFrame.adapterBinding);
    assert.equal(adaptedEnd?.runId, "run-1");
    assert.equal(adaptedEnd?.eventType, "stream.end");
    assert.equal((adaptedEnd?.event.payload as Record<string, unknown>).terminal_event_id, "terminal-1");
    assert.equal(ctx.v4TerminalFenceRef?.current?.terminalEventId, "terminal-1");
    const staleEnd = {
      ...endFrame,
      binding: { sessionId: "session-1", runId: "run-1", streamVersion: 2, streamIncarnation: 1, generation: 3 },
    };
    assert.equal(handlePublicRunStreamFrameV4(staleEnd as never), false);
    assert.equal(handlePublicRunStreamFrameV4(endFrame as never), true);
    assert.equal(handlePublicRunStreamFrameV4(endFrame as never), false);
    const foreignEnd = { ...endFrame, frame: { ...endFrame.frame, value: { ...endFrame.frame.value, run_id: "run-2" } }, adapterBinding: { runId: "run-2", streamIncarnation: 1, generation: 3 } };
    assert.equal(handlePublicRunStreamFrameV4(foreignEnd as never), false);
  } finally {
    dom.cleanup();
  }
});

test("mounted adapter-to-reducer artifact failure exposes a safe accessible label", () => {
  const dom = setupDom();
  const adapted = adaptPublicRunStreamEventV4({
    eventHeader: "artifact.failed",
    transportCursor: "run-1:1:3-0",
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: "artifact-failed-1",
      run_id: "run-1",
      message_id: "message-1",
      seq: 3,
      event_type: "artifact.failed",
      stream_incarnation: 1,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { artifact_id: "artifact-opaque-1", status: "failed", failure_category: "artifact_failed" },
    },
  }, { runId: "run-1", streamIncarnation: 1 });
  assert.ok(adapted);
  const reduced = processMessageEvent(adapted, undefined, [], "", [], 0, [], false, "message-1");
  try {
    renderProjection(dom.root, message(reduced.parts), { sendMessage: async () => undefined, cancel: async () => undefined, reconnect: async () => undefined, loadHistory: async () => undefined });
    const artifact = dom.container.querySelector(
      '[role="group"][aria-label="Artifact unavailable"]',
    );
    assert.ok(artifact);
    assert.equal(artifact?.getAttribute("aria-label"), "Artifact unavailable");
    assert.doesNotMatch(dom.container.textContent || "", /artifact-opaque-1|private|secret|token/i);
  } finally {
    dom.cleanup();
  }
});

test("mounted projection renders the production subagent lifecycle with hierarchy, metadata, and keyboard state", () => {
  const dom = setupDom();
  const actions = { sendMessage: async () => undefined, cancel: async () => undefined, reconnect: async () => undefined, loadHistory: async () => undefined };
  try {
    const parent = adaptPublicRunStreamEventV4({
      eventHeader: "subagent.started",
      transportCursor: "run-1:1:1-0",
      generation: 3,
      value: {
        schema: "ai-platform.public-run-stream-event.v4",
        event_id: "parent-event",
        run_id: "run-1",
        message_id: "message-1",
        seq: 1,
        event_type: "subagent.started",
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-01-01T00:00:00Z",
        payload: {
          subagent_id: "parent-agent-1",
          display_name: "Parent worker",
        },
      },
    }, { runId: "run-1", streamIncarnation: 1, generation: 3 });
    const child = adaptPublicRunStreamEventV4({
      eventHeader: "subagent.started",
      transportCursor: "run-1:1:2-0",
      generation: 3,
      value: {
        schema: "ai-platform.public-run-stream-event.v4",
        event_id: "child-event",
        run_id: "run-1",
        message_id: "message-1",
        seq: 2,
        event_type: "subagent.started",
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: "parent-event",
        emitted_at: "2026-01-01T00:00:01Z",
        payload: {
          subagent_id: "subagent-1",
          display_name: "Research worker",
        },
      },
    }, { runId: "run-1", streamIncarnation: 1, generation: 3 });
    assert.ok(parent);
    assert.ok(child);
    let reduced = processMessageEvent(parent, undefined, [], "", [], 0, [], false, "message-1");
    reduced = processMessageEvent(child, undefined, reduced.parts, reduced.content, reduced.toolCalls, 0, [], false, "message-1");
    const unknownChild = processMessageEvent(
      "run_event",
      {
        event_type: "public_subagent_activity",
        event_id: "unknown-child-event",
        causation_event_id: "unaccepted-parent-event",
        subagent_id: "un-grouped",
        display_name: "Un-grouped",
        status: "started",
      },
      reduced.parts,
      reduced.content,
      reduced.toolCalls,
      0,
      [],
      false,
      "message-1",
    );
    const childProgress = adaptPublicRunStreamEventV4({
      eventHeader: "subagent.progress",
      transportCursor: "run-1:1:3-0",
      generation: 3,
      value: {
        schema: "ai-platform.public-run-stream-event.v4",
        event_id: "child-progress-event",
        run_id: "run-1",
        message_id: "message-1",
        seq: 3,
        event_type: "subagent.progress",
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: "parent-event",
        emitted_at: "2026-01-01T00:00:02Z",
        payload: {
          subagent_id: "subagent-1",
          display_name: "Research worker",
          duration_ms: 1_200,
          current_category: "search",
          progress_percent: 40,
        },
      },
    }, { runId: "run-1", streamIncarnation: 1, generation: 3 });
    assert.ok(childProgress);
    reduced = processMessageEvent(childProgress, undefined, reduced.parts, reduced.content, reduced.toolCalls, 0, [], false, "message-1");
    const unknownPart = unknownChild.parts.find(
      (part) => part.type === "subagent" && part.agent_id === "un-grouped",
    );
    assert.ok(unknownPart && unknownPart.type === "subagent");
    assert.equal(unknownPart.parent_agent_id, undefined);

    renderProjection(dom.root, message(reduced.parts), actions);
    const lifecycle = dom.container.querySelector("[data-subagent-id=subagent-1]");
    const trigger = dom.container.querySelector("[data-subagent-trigger=subagent-1]") as HTMLButtonElement | null;
    assert.ok(lifecycle);
    assert.equal(lifecycle?.getAttribute("data-parent-agent-id"), "parent-agent-1");
    assert.ok(lifecycle?.className.includes("ml-4"));
    assert.ok(lifecycle?.className.includes("border-l-2"));
    assert.ok(trigger);
    assert.equal(trigger?.tagName, "BUTTON");
    assert.equal(trigger?.getAttribute("aria-label"), "Sub-agent: Running, Nested Agent");
    assert.match(lifecycle?.textContent || "", /Running/);
    assert.match(lifecycle?.textContent || "", /Nested Agent/);
    assert.doesNotMatch(lifecycle?.textContent || "", /parent-agent-1/);
    assert.match(lifecycle?.textContent || "", /Category: search/);
    assert.match(lifecycle?.textContent || "", /Progress: 40%/);
    trigger?.focus();
    assert.equal(dom.container.ownerDocument.activeElement, trigger);

    act(() => {
      trigger?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });
    assert.ok(getPersistentToolPanelState());
    closePersistentToolPanel();
    act(() => {
      trigger?.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
    });
    assert.ok(getPersistentToolPanelState());
    closePersistentToolPanel();

    const completed = processMessageEvent(
      "run_event",
      {
        event_id: "child-completed-event",
        event_type: "public_subagent_activity",
        subagent_id: "subagent-1",
        display_name: "Research worker",
        status: "completed",
        progress_percent: 100,
        duration_ms: 2_500,
        causation_event_id: "parent-event",
      },
      reduced.parts,
      reduced.content,
      reduced.toolCalls,
      0,
      [],
      false,
      "message-1",
    );
    renderProjection(dom.root, message(completed.parts), actions);
    const updated = dom.container.querySelector("[data-subagent-id=subagent-1]");
    const updatedTrigger = dom.container.querySelector("[data-subagent-trigger=subagent-1]");
    assert.equal(updatedTrigger?.getAttribute("aria-label"), "Sub-agent: Completed, Nested Agent");
    assert.match(updated?.textContent || "", /Completed/);
    assert.match(updated?.textContent || "", /Progress: 100%/);
    assert.match(updated?.textContent || "", /Duration: 2\.5s/);
  } finally {
    closePersistentToolPanel();
    dom.cleanup();
  }
});
