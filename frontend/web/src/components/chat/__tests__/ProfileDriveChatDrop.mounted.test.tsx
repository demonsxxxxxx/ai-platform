import assert from "node:assert/strict";
import test from "node:test";

// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { PROFILE_DRIVE_DRAG_TYPE } from "../../workbench/profileDriveDrag";

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("drops a ProfileDrive path into the Composer without treating it as a local upload", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body><div id='root'></div></body></html>",
    { url: "http://localhost/chat/session-a", pretendToBeVisual: true },
  );
  const globalValues: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    HTMLTextAreaElement: dom.window.HTMLTextAreaElement,
    Event: dom.window.Event,
    InputEvent: dom.window.InputEvent,
    CustomEvent: dom.window.CustomEvent,
    localStorage: dom.window.localStorage,
    sessionStorage: dom.window.sessionStorage,
    requestAnimationFrame: (callback: FrameRequestCallback) =>
      dom.window.setTimeout(() => callback(Date.now()), 0),
    cancelAnimationFrame: (handle: number) => dom.window.clearTimeout(handle),
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
  Object.defineProperty(dom.window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }),
  });

  const [{ act, createElement }, { createRoot }, { ChatInput }, { AuthProvider }] =
    await Promise.all([
      import("react"),
      import("react-dom/client"),
      import("../ChatInput"),
      import("../../../hooks/useAuth"),
    ]);
  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
  const droppedPaths: Array<{ source_id: string; path: string }> = [];

  try {
    await act(async () => {
      root.render(
        createElement(
          AuthProvider,
          null,
          createElement(ChatInput, {
            onSend: async () => ({ status: "accepted" as const }),
            onStop: async () => "unavailable" as const,
            isLoading: false,
            attachments: [],
            onAttachmentsChange: () => undefined,
            acceptedFileTypes: [],
            disableSlashCommands: true,
            tools: [],
            skills: [],
            enableSkills: false,
            agentOptionValues: {},
            availableModels: [],
            onProfileDriveFileDrop: async (reference) => {
              droppedPaths.push(reference);
            },
          }),
        ),
      );
      await flush();
    });

    const composer = container.querySelector<HTMLElement>(
      '[data-disable-global-file-drop="true"]',
    );
    assert.ok(composer);
    const drop = new dom.window.Event("drop", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(drop, "dataTransfer", {
      value: {
        types: [PROFILE_DRIVE_DRAG_TYPE],
        getData: (type: string) =>
          type === PROFILE_DRIVE_DRAG_TYPE
            ? "Documents/report.pdf"
            : "",
      },
    });

    await act(async () => {
      composer.dispatchEvent(drop);
      await flush();
    });

    assert.equal(drop.defaultPrevented, true);
    assert.deepEqual(droppedPaths, [
      { source_id: "profile", path: "Documents/report.pdf" },
    ]);

    const publicDrop = new dom.window.Event("drop", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(publicDrop, "dataTransfer", {
      value: {
        types: [PROFILE_DRIVE_DRAG_TYPE],
        getData: (type: string) =>
          type === PROFILE_DRIVE_DRAG_TYPE
            ? JSON.stringify({
                source_id: "public",
                path: "01-研发部/report.pdf",
              })
            : "",
      },
    });
    await act(async () => {
      composer.dispatchEvent(publicDrop);
      await flush();
    });
    assert.deepEqual(droppedPaths[1], {
      source_id: "public",
      path: "01-研发部/report.pdf",
    });
  } finally {
    await act(async () => root.unmount());
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
    dom.window.close();
  }
});
