import assert from "node:assert/strict";
import test from "node:test";

// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { PROFILE_DRIVE_DRAG_TYPE } from "../profileDriveDrag";

function buttonByText(root: ParentNode, label: string): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll("button")).find((candidate) =>
    candidate.textContent?.includes(label),
  );
  assert.ok(button, `Missing button: ${label}`);
  return button as HTMLButtonElement;
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function changeInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(input),
    "value",
  )?.set;
  assert.ok(setter);
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

test("expands ProfileDrive folders and imports a file into the current workspace", async () => {
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

  const [{ act, createElement }, { createRoot }, { ProfileDriveWorkspaceBrowser }] =
    await Promise.all([
      import("react"),
      import("react-dom/client"),
      import("../ProfileDriveWorkspaceBrowser"),
    ]);
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init: RequestInit }> = [];
  let importRequestCount = 0;
  const pendingImport: { release?: () => void } = {};
  const directoryFailures: Record<
    string,
    { code: string; message: string; status: number }
  > = {
    Restricted: {
      code: "access_denied",
      message: "Access to the path was denied.",
      status: 403,
    },
    Missing: {
      code: "path_not_found",
      message: "The path was not found.",
      status: 404,
    },
    Broken: {
      code: "server_unavailable",
      message: "The server is unavailable.",
      status: 503,
    },
    Expired: {
      code: "reauth_required",
      message: "Authentication is required.",
      status: 409,
    },
  };
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/profile-drive/status")) {
      return new Response(
        JSON.stringify({
          status: "connected",
          connected: true,
          reauthRequired: false,
          connectedAtUtc: "2026-09-21T00:00:00Z",
          lastUsedAtUtc: "2026-09-21T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/profile-drive/files/list")) {
      const body = JSON.parse(String(init.body)) as { path: string };
      const failure = directoryFailures[body.path];
      if (failure) {
        return new Response(
          JSON.stringify({ status: failure.code, message: failure.message }),
          {
            status: failure.status,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      const entries =
        body.path === ""
          ? [
              {
                path: "$RECYCLE.BIN",
                name: "$RECYCLE.BIN",
                type: "directory",
                size: null,
                lastModifiedUtc: "2026-09-21T00:00:00Z",
              },
              {
                path: "Desktop",
                name: "Desktop",
                type: "directory",
                size: null,
                lastModifiedUtc: "2026-09-21T00:00:00Z",
              },
              {
                path: "Documents",
                name: "Documents",
                type: "directory",
                size: null,
                lastModifiedUtc: "2026-09-21T00:00:00Z",
              },
              ...["Restricted", "Missing", "Broken", "Expired"].map((name) => ({
                path: name,
                name,
                type: "directory",
                size: null,
                lastModifiedUtc: "2026-09-21T00:00:00Z",
              })),
            ]
          : body.path === "Documents"
            ? [
                {
                  path: "Documents/reports",
                  name: "reports",
                  type: "directory",
                  size: null,
                  lastModifiedUtc: "2026-09-21T00:00:00Z",
                },
              ]
            : [
                {
                  path: "Documents/reports/~$draft.doc",
                  name: "~$draft.doc",
                  type: "file",
                  size: 162,
                  lastModifiedUtc: "2026-09-21T00:00:00Z",
                },
                {
                  path: "Documents/reports/report.pdf",
                  name: "report.pdf",
                  type: "file",
                  size: 42,
                  lastModifiedUtc: "2026-09-21T00:00:00Z",
                },
                {
                  path: "Documents/reports/shortcut.lnk",
                  name: "shortcut.lnk",
                  type: "file",
                  size: 128,
                  lastModifiedUtc: "2026-09-21T00:00:00Z",
                },
              ];
      return new Response(
        JSON.stringify({
          status: "success",
          path: body.path,
          entries,
          truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/ai/chat/sessions/session-a/profile-drive-files")) {
      importRequestCount += 1;
      if (importRequestCount === 2) {
        await new Promise<void>((resolve) => {
          pendingImport.release = resolve;
        });
      }
      return new Response(
        JSON.stringify({
          file_id: "file-profile",
          run_id: null,
          name: "report.pdf",
          mime_type: "application/pdf",
          size_bytes: 42,
          preview_url: "/api/ai/files/file-profile/preview?session_id=session-a",
          download_url: "/api/ai/files/file-profile/download?session_id=session-a",
          created_at: "2026-09-21T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  }) as typeof fetch;

  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
  const imported: Array<{ file_id: string; preview_url: string | null }> = [];
  const addedPaths: Array<{ source_id: string; path: string }> = [];

  try {
    await act(async () => {
      root.render(
        createElement(ProfileDriveWorkspaceBrowser, {
          sessionId: "session-a",
          onImported: (file) => imported.push(file),
          onAddToConversation: (reference) => {
            addedPaths.push(reference);
          },
        }),
      );
      await flush();
      await flush();
    });

    assert.doesNotMatch(container.textContent ?? "", /\$RECYCLE\.BIN/);
    assert.match(container.textContent ?? "", /桌面/);
    assert.match(container.textContent ?? "", /文档/);
    const rootFilter = container.querySelector<HTMLInputElement>(
      'input[aria-label="筛选文件树"]',
    );
    assert.ok(rootFilter);
    await act(async () => changeInput(rootFilter, "文档"));
    assert.doesNotMatch(container.textContent ?? "", /桌面/);
    assert.match(container.textContent ?? "", /文档/);
    await act(async () => changeInput(rootFilter, "Restricted"));

    const restrictedButton = buttonByText(container, "Restricted");
    await act(async () => {
      restrictedButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.equal(restrictedButton.getAttribute("aria-expanded"), "true");
    assert.equal(rootFilter.value, "Restricted");
    assert.match(container.textContent ?? "", /没有权限访问此文件夹/);
    assert.doesNotMatch(container.textContent ?? "", /本地文件暂时不可用/);
    await act(async () => changeInput(rootFilter, ""));
    assert.match(container.textContent ?? "", /桌面/);
    assert.match(container.textContent ?? "", /文档/);
    assert.match(container.textContent ?? "", /没有权限访问此文件夹/);
    const returnButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="返回 Restricted"]',
    );
    assert.ok(returnButton);
    await act(async () => {
      returnButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    assert.equal(restrictedButton.getAttribute("aria-expanded"), "false");
    assert.doesNotMatch(container.textContent ?? "", /没有权限访问此文件夹/);
    assert.equal(dom.window.document.activeElement, restrictedButton);

    for (const [folder, expectedMessage] of [
      ["Missing", "此文件夹不存在或已被移除"],
      ["Broken", "暂时无法打开此文件夹"],
    ]) {
      const folderButton = buttonByText(container, folder);
      await act(async () => {
        folderButton.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });
      assert.match(container.textContent ?? "", new RegExp(expectedMessage));
      const backButton = container.querySelector<HTMLButtonElement>(
        `button[aria-label="返回 ${folder}"]`,
      );
      assert.ok(backButton);
      await act(async () => {
        backButton.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true }),
        );
      });
      assert.equal(folderButton.getAttribute("aria-expanded"), "false");
    }

    const documentsButton = buttonByText(container, "文档");
    assert.equal(documentsButton.getAttribute("aria-expanded"), "false");
    await act(async () => {
      documentsButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.equal(documentsButton.getAttribute("aria-expanded"), "true");
    assert.match(container.textContent ?? "", /reports/);

    const reportsButton = buttonByText(container, "reports");
    assert.equal(reportsButton.getAttribute("aria-expanded"), "false");
    await act(async () => {
      reportsButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.equal(reportsButton.getAttribute("aria-expanded"), "true");
    assert.match(container.textContent ?? "", /桌面/);
    assert.match(container.textContent ?? "", /report\.pdf/);
    const reportsLoadCount = requests.filter((request) => {
      if (!request.url.endsWith("/api/profile-drive/files/list")) return false;
      return JSON.parse(String(request.init.body)).path === "Documents/reports";
    }).length;
    await act(async () => {
      reportsButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    assert.equal(reportsButton.getAttribute("aria-expanded"), "false");
    assert.doesNotMatch(container.textContent ?? "", /report\.pdf/);
    await act(async () => {
      reportsButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    assert.equal(reportsButton.getAttribute("aria-expanded"), "true");
    assert.match(container.textContent ?? "", /report\.pdf/);
    assert.equal(
      requests.filter((request) => {
        if (!request.url.endsWith("/api/profile-drive/files/list")) return false;
        return JSON.parse(String(request.init.body)).path === "Documents/reports";
      }).length,
      reportsLoadCount,
    );
    assert.doesNotMatch(container.textContent ?? "", /~\$draft\.doc/);
    assert.ok(container.querySelector('button[aria-label="预览 report.pdf"]'));
    assert.ok(container.querySelector('button[aria-label="下载 shortcut.lnk"]'));

    const filter = container.querySelector<HTMLInputElement>(
      'input[aria-label="筛选文件树"]',
    );
    assert.ok(filter);
    await act(async () => changeInput(filter, "shortcut"));
    assert.doesNotMatch(container.textContent ?? "", /report\.pdf/);
    assert.match(container.textContent ?? "", /shortcut\.lnk/);
    await act(async () => changeInput(filter, ""));

    const reportButton = buttonByText(container, "report.pdf");
    const reportRow = reportButton.closest<HTMLElement>('[role="treeitem"]');
    assert.ok(reportRow);
    assert.equal(reportRow.draggable, true);
    const dragPayload = new Map<string, string>();
    const dataTransfer = {
      effectAllowed: "none",
      setData: (type: string, value: string) => dragPayload.set(type, value),
    };
    const dragStart = new dom.window.Event("dragstart", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(dragStart, "dataTransfer", { value: dataTransfer });
    await act(async () => reportRow.dispatchEvent(dragStart));
    assert.equal(dataTransfer.effectAllowed, "copy");
    assert.equal(
      dragPayload.get(PROFILE_DRIVE_DRAG_TYPE),
      JSON.stringify({
        source_id: "profile",
        path: "Documents/reports/report.pdf",
      }),
    );
    const addButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="添加 report.pdf 到会话"]',
    );
    assert.ok(addButton);
    await act(async () =>
      addButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      ),
    );
    assert.deepEqual(addedPaths, [
      { source_id: "profile", path: "Documents/reports/report.pdf" },
    ]);

    await act(async () => {
      reportButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    assert.equal(imported[0]?.file_id, "file-profile");
    assert.equal(
      imported[0]?.preview_url,
      "/api/ai/files/file-profile/preview?session_id=session-a",
    );
    const importRequest = requests.find((request) =>
      request.url.endsWith("/api/ai/chat/sessions/session-a/profile-drive-files"),
    );
    assert.ok(importRequest);
    assert.equal(importRequest.init.credentials, "include");
    assert.equal(new Headers(importRequest.init.headers).get("Authorization"), null);
    assert.deepEqual(JSON.parse(String(importRequest.init.body)), {
      source_id: "profile",
      path: "Documents/reports/report.pdf",
    });

    await act(async () => {
      buttonByText(container, "report.pdf").dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.ok(pendingImport.release);
    await act(async () => {
      root.render(
        createElement(ProfileDriveWorkspaceBrowser, {
          key: "session-b",
          sessionId: "session-b",
          onImported: (file) => imported.push(file),
          onAddToConversation: (reference) => {
            addedPaths.push(reference);
          },
        }),
      );
      await flush();
    });
    pendingImport.release();
    await act(async () => {
      await flush();
      await flush();
    });
    assert.equal(imported.length, 1);

    const profileSource = container.querySelector<HTMLElement>(
      '[data-profile-drive-source="profile"]',
    );
    assert.ok(profileSource);
    const expiredButton = buttonByText(profileSource, "Expired");
    await act(async () => {
      expiredButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.match(profileSource.textContent ?? "", /本地文件服务器需要重新认证/);
    assert.equal(profileSource.querySelector('[role="tree"]'), null);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
    dom.window.close();
  }
});

test("mounts the public drive with source-correct expansion and import", async () => {
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

  const [{ act, createElement }, { createRoot }, { ProfileDriveWorkspaceBrowser }] =
    await Promise.all([
      import("react"),
      import("react-dom/client"),
      import("../ProfileDriveWorkspaceBrowser"),
    ]);
  const originalFetch = globalThis.fetch;
  const bodies: Array<Record<string, unknown>> = [];
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/profile-drive/status")) {
      return new Response(
        JSON.stringify({
          status: "connected",
          connected: true,
          reauthRequired: false,
          connectedAtUtc: null,
          lastUsedAtUtc: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/profile-drive/files/list")) {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      bodies.push(body);
      const path = String(body.path);
      return new Response(
        JSON.stringify({
          status: "success",
          path,
          entries:
            path === ""
              ? [
                  {
                    path: "01-研发部",
                    name: "01-研发部",
                    type: "directory",
                    size: null,
                    lastModifiedUtc: "1970-01-01T00:00:00Z",
                  },
                ]
              : [
                  {
                    path: "01-研发部/report.pdf",
                    name: "report.pdf",
                    type: "file",
                    size: 42,
                    lastModifiedUtc: "2026-09-22T00:00:00Z",
                  },
                ],
          truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/ai/chat/sessions/session-a/profile-drive-files")) {
      bodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return new Response(
        JSON.stringify({
          file_id: "file-public",
          run_id: null,
          name: "report.pdf",
          mime_type: "application/pdf",
          size_bytes: 42,
          preview_url: "/api/ai/files/file-public/preview?session_id=session-a",
          download_url: "/api/ai/files/file-public/download?session_id=session-a",
          created_at: "2026-09-22T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  }) as typeof fetch;

  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(
        createElement(ProfileDriveWorkspaceBrowser, {
          sessionId: "session-a",
          onImported: () => undefined,
          onAddToConversation: () => undefined,
        }),
      );
      await flush();
      await flush();
    });

    const profileTab = buttonByText(container, "本地文件");
    const publicTab = buttonByText(container, "公盘文件");
    assert.equal(profileTab.getAttribute("aria-selected"), "true");
    assert.equal(publicTab.getAttribute("aria-selected"), "false");
    assert.equal(
      container.querySelectorAll('[data-librechat-context-section="files"]').length,
      1,
    );

    await act(async () => {
      publicTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    const publicPanel = container.querySelector<HTMLElement>(
      '#profile-drive-public-panel',
    );
    const profilePanel = container.querySelector<HTMLElement>(
      '#profile-drive-profile-panel',
    );
    assert.ok(publicPanel);
    assert.ok(profilePanel);
    assert.equal(publicPanel.hidden, false);
    assert.equal(profilePanel.hidden, true);
    const publicFolder = buttonByText(publicPanel, "01-研发部");
    assert.doesNotMatch(publicPanel.textContent ?? "", /刷新公盘文件/);
    assert.doesNotMatch(publicPanel.textContent ?? "", /!|…/);
    await act(async () => {
      publicFolder.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.equal(publicFolder.getAttribute("aria-expanded"), "true");
    assert.match(publicPanel.textContent ?? "", /report\.pdf/);
    await act(async () => {
      profileTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      publicTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.equal(publicFolder.getAttribute("aria-expanded"), "true");
    assert.match(publicPanel.textContent ?? "", /report\.pdf/);
    await act(async () => {
      buttonByText(publicPanel, "report.pdf").dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const publicLists = bodies.filter((body) => body.source === "public");
    const publicRootList = publicLists.find((body) => body.path === "");
    assert.deepEqual(publicRootList, {
      source: "public",
      path: "",
      maxEntries: 200,
    });
    const publicFolderList = publicLists.find(
      (body) => body.path === "01-研发部",
    );
    assert.deepEqual(publicFolderList, {
      source: "public",
      path: "01-研发部",
      maxEntries: 200,
    });
    assert.deepEqual(
      bodies.find((body) => body.source_id === "public"),
      {
        source_id: "public",
        path: "01-研发部/report.pdf",
      },
    );
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
    dom.window.close();
  }
});
