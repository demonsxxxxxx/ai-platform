import assert from "node:assert/strict";
import test from "node:test";

import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import {
  PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
} from "../../../services/api/profileDrive";
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
  const fixture = installSelectTestDom();
  const dom = { window };
  document.body.innerHTML = '<div id="root"></div>';
  const [{ act, createElement }, { ProfileDriveWorkspaceBrowser }] = await Promise.all([
    import("react"), import("../ProfileDriveWorkspaceBrowser"),
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
  const root = await createLobeTestRoot(container);
  const imported: Array<{ file_id: string; preview_url: string | null }> = [];
  const addedPaths: Array<{ source_id: string; path: string }> = [];
  const stagedFile = {
    file_id: "file-profile-staged",
    run_id: null,
    name: "report.pdf",
    mime_type: "application/pdf",
    size_bytes: 42,
    preview_url: "/api/ai/files/file-profile-staged/preview",
    download_url: "/api/ai/files/file-profile-staged/download",
    created_at: "2026-09-21T00:00:00Z",
  };

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
    assert.equal(container.querySelector("h3, [role=checkbox], [data-profile-drive-count], nav"), null, "root only shows drive switch, refresh, search and tree");
    assert.notEqual(rootFilter.parentElement?.parentElement?.tagName, "LABEL", "Lobe Input owns the only search frame");
    assert.ok(rootFilter.parentElement?.querySelector("svg"), "search icon is in the Input prefix");
    await act(async () => changeInput(rootFilter, "文档"));
    assert.doesNotMatch(container.textContent ?? "", /桌面/);
    assert.match(container.textContent ?? "", /文档/);
    const clearSearch = rootFilter.parentElement?.querySelector<HTMLButtonElement>("[data-lobe-input-clear]");
    assert.ok(clearSearch);
    const beforeClear = requests.length;
    await act(async () => clearSearch.click());
    assert.equal(rootFilter.value, "");
    assert.match(container.textContent ?? "", /桌面/);
    assert.equal(requests.length, beforeClear, "clearing search does not read the drive");
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
    assert.equal(buttonByText(container, "文档 / reports").getAttribute("aria-expanded"), "false", "loaded single-child directory is compacted automatically");
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

    await act(async () => {
      root.render(
        createElement(ProfileDriveWorkspaceBrowser, {
          key: "no-session",
          sessionId: null,
          onImported: (file) => imported.push(file),
          onAddToConversation: (reference) => {
            addedPaths.push(reference);
            return stagedFile;
          },
        }),
      );
      await flush();
      await flush();
    });
    await act(async () => {
      buttonByText(container, "文档").dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    await act(async () => {
      buttonByText(container, "reports").dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    const stagedReportButton = buttonByText(container, "report.pdf");
    const stagedReportRow = stagedReportButton.closest<HTMLElement>(
      '[role="treeitem"]',
    );
    assert.ok(stagedReportRow);
    assert.equal(stagedReportButton.disabled, false);
    assert.equal(stagedReportRow.draggable, true);
    await act(async () => {
      stagedReportButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.equal(imported[1]?.file_id, "file-profile-staged");
    assert.equal(
      imported[1]?.preview_url,
      "/api/ai/files/file-profile-staged/preview",
    );
    assert.deepEqual(addedPaths.at(-1), {
      source_id: "profile",
      path: "Documents/reports/report.pdf",
    });
    const stagedAddButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="添加 report.pdf 到会话"]',
    );
    assert.ok(stagedAddButton);
    assert.equal(stagedAddButton.disabled, false);
    await act(async () => {
      stagedAddButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.deepEqual(addedPaths.at(-1), {
      source_id: "profile",
      path: "Documents/reports/report.pdf",
    });

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
    assert.ok(buttonByText(profileSource, "认证本地文件服务器"));
    assert.equal(profileSource.querySelector('[role="tree"]'), null);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;




    fixture.close();
  }
});

test("mounts the public drive with source-correct expansion and import", async () => {
  const fixture = installSelectTestDom();
  const dom = { window };
  document.body.innerHTML = '<div id="root"></div>';
  const [{ act, createElement }, { ProfileDriveWorkspaceBrowser }] = await Promise.all([
    import("react"), import("../ProfileDriveWorkspaceBrowser"),
  ]);
  const originalFetch = globalThis.fetch;
  const bodies: Array<Record<string, unknown>> = [];
  const pendingStatusResolvers: Array<(response: Response) => void> = [];
  const releaseStatus = () => {
    const statusPayload = JSON.stringify({
      status: "disconnected",
      connected: false,
      reauthRequired: false,
      connectedAtUtc: null,
      lastUsedAtUtc: null,
    });
    while (pendingStatusResolvers.length > 0) {
      pendingStatusResolvers.shift()!(
        new Response(statusPayload, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
  };
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/profile-drive/status")) {
      return new Promise<Response>((resolve) => {
        pendingStatusResolvers.push(resolve);
      });
    }
    if (url.endsWith("/api/profile-drive/files/list")) {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      bodies.push(body);
      const path = String(body.path);
      const publicRootRequestCount = bodies.filter(
        (request) => request.source === "public" && request.path === "",
      ).length;
      if (path === "" && body.source === "public" && publicRootRequestCount > 2) {
        return new Response(
          JSON.stringify({ status: "drive_unavailable", message: "retry later" }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          status: "success",
          path,
          entries:
            path === "" && body.source === "public" && publicRootRequestCount > 1
              ? [
                  {
                    path: "01-研发部",
                    name: "01-研发部",
                    type: "directory",
                    size: null,
                    lastModifiedUtc: "1970-01-01T00:00:00Z",
                  },
                  {
                    path: "公告.txt",
                    name: "公告.txt",
                    type: "file",
                    size: 18,
                    lastModifiedUtc: "2026-09-22T00:00:00Z",
                  },
                ]
              : path === ""
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
  const root = await createLobeTestRoot(container);
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
    assert.equal(profileTab.getAttribute("aria-controls"), "profile-drive-profile-panel");
    assert.equal(publicTab.getAttribute("aria-controls"), "profile-drive-public-panel");
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
    const refreshPublicButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="刷新公盘文件"]',
    );
    assert.ok(refreshPublicButton);
    assert.equal(refreshPublicButton.disabled, true);
    assert.match(publicPanel.textContent ?? "", /正在加载公盘文件/);
    await act(async () => {
      dom.window.dispatchEvent(
        new dom.window.Event(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT),
      );
      await flush();
      await flush();
    });
    releaseStatus();
    await act(async () => {
      await flush();
      await flush();
    });
    let publicFolder = buttonByText(publicPanel, "01-研发部");
    assert.equal(refreshPublicButton.disabled, false);
    assert.equal(publicPanel.querySelectorAll('[role="treeitem"]').length, 1);
    const publicRootRequestCount = () =>
      bodies.filter((body) => body.source === "public" && body.path === "").length;
    const rootRequestsBeforeRefresh = publicRootRequestCount();
    await act(async () => {
      refreshPublicButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
      await flush();
    });
    assert.equal(publicRootRequestCount(), rootRequestsBeforeRefresh + 1);
    assert.equal(publicPanel.querySelectorAll('[role="treeitem"]').length, 2);
    assert.ok(buttonByText(publicPanel, "公告.txt"));
    publicFolder = buttonByText(publicPanel, "01-研发部");
    await act(async () => {
      publicFolder.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });
    assert.equal(publicFolder.getAttribute("aria-expanded"), "true");
    assert.match(publicPanel.textContent ?? "", /report\.pdf/);
    const publicSearch = publicPanel.querySelector<HTMLInputElement>('input[aria-label="筛选文件树"]');
    assert.ok(publicSearch);
    await act(async () => changeInput(publicSearch, "report"));
    await act(async () => {
      profileTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      await flush();
    });
    const refreshProfileButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="刷新本地文件"]',
    );
    assert.ok(refreshProfileButton);
    assert.equal(refreshProfileButton.disabled, false);
    assert.equal(profilePanel.querySelectorAll('[role="treeitem"]').length, 1);
    await act(async () => {
      publicTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      await flush();
    });
    assert.equal(publicFolder.getAttribute("aria-expanded"), "true");
    assert.equal(publicSearch.value, "report", "tab changes preserve each drive's search");
    assert.match(publicPanel.textContent ?? "", /report\.pdf/);
    await act(async () => {
      buttonByText(publicPanel, "report.pdf").dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    await act(async () => {
      refreshPublicButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
      await flush();
      await flush();
    });
    assert.equal(refreshPublicButton.disabled, false);
    assert.equal(publicPanel.querySelectorAll('[role="treeitem"]').length, 0);
    assert.match(publicPanel.textContent ?? "", /公盘文件暂时不可用/);

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
    fixture.close();
  }
});

test("compacts loaded chains, focuses with breadcrumbs, and uses controlled Composer attachments", async () => {
  const fixture = installSelectTestDom();
  document.body.innerHTML = '<div id="root"></div>';
  const [{ act, createElement, useState }, { WorkbenchRightPanel }, { CloseContextPanelContext }] = await Promise.all([
    import("react"), import("../WorkbenchRightPanel"), import("../../../librechat-ui/ContextPanel"),
  ]);
  const previewEvents: string[] = [];
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  const added: string[] = [];
  const directory = (path: string) => ({ path, name: path.split("/").at(-1)!, type: "directory", size: null, lastModifiedUtc: "2026-10-29T00:00:00Z" });
  const filePath = "Projects/IB012/版本发布说明与上线公告.docx";
  const entries: Record<string, object[]> = {
    "": [directory("Projects"), directory("Other"), directory("Restricted")],
    Projects: [directory("Projects/IB012")],
    "Projects/IB012": [{ ...directory(filePath), type: "file", size: 95232 }],
    Other: [],
  };
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) return json({ credential: "synthetic" });
    if (url.endsWith("/api/profile-drive/status")) return json({ connected: true });
    if (url.endsWith("/api/profile-drive/files/list")) {
      const body = JSON.parse(String(init.body)) as { path: string };
      requests.push(body.path);
      if (body.path === "Restricted") return new Response(JSON.stringify({ status: "access_denied" }), { status: 403 });
      return json({ status: "success", path: body.path, entries: entries[body.path] ?? [], truncated: false });
    }
    if (url.endsWith("/profile-drive-files")) return json({ file_id: "preview-a", run_id: null, name: "版本发布说明与上线公告.docx", mime_type: "application/test", size_bytes: 42, preview_url: "/preview-a", download_url: "/download-a" });
    throw new Error(`Unexpected request ${url}`);
  }) as typeof fetch;
  const container = document.getElementById("root")!;
  const root = await createLobeTestRoot(container);
  let removeFromComposer = () => {};
  function Harness() {
    const [attachments, setAttachments] = useState<import("../../../types").MessageAttachment[]>([]);
    removeFromComposer = () => setAttachments([]);
    return createElement(CloseContextPanelContext.Provider, { value: () => previewEvents.push("close") }, createElement(WorkbenchRightPanel, {
      sessionId: "session-a", onProfileDriveFileImported: () => { previewEvents.push("preview"); }, attachments,
      onRemoveAttachment: (attachment) => setAttachments((current) => current.filter((item) => item.id !== attachment.id)),
      onProfileDriveFileDrop: (reference) => {
        added.push(reference.path);
        setAttachments([{ id: "file-a", key: "file-a", name: "版本发布说明与上线公告.docx", type: "document", mimeType: "application/test", size: 95232, profileDriveReference: reference }]);
      },
    }));
  }
  try {
    await act(async () => { root.render(createElement(Harness)); await flush(); await flush(); });
    const panel = container.querySelector('#profile-drive-profile-panel')!;
    const click = async (button: HTMLElement) => act(async () => { button.click(); await flush(); });
    assert.equal(requests.filter((path) => path === "Projects").length, 0, "no speculative traversal");
    await click(buttonByText(panel, "Projects"));
    const merged = buttonByText(panel, "Projects / IB012");
    assert.equal(requests.filter((path) => path === "Projects/IB012").length, 0);
    await click(merged);
    assert.match(panel.textContent ?? "", /版本发布说明与上线公告/);
    const focus = panel.querySelector<HTMLElement>('button[aria-label="聚焦目录 Projects / IB012"]')!;
    const count = requests.length;
    focus.focus();
    await click(focus);
    assert.equal(document.activeElement, panel.querySelector("nav button:last-child"), "focus moves from removed row to current breadcrumb");
    assert.equal(requests.length, count, "focus reuses loaded listing");
    assert.equal(panel.querySelector('[role="treeitem"]')?.getAttribute("aria-level"), "1");
    assert.ok(panel.querySelector('nav[aria-label="本地文件路径"]'));
    assert.doesNotMatch(panel.textContent ?? "", /本地文件/, "drive label appears only in the tab");
    assert.ok(!panel.querySelector(".selection-summary"));
    assert.doesNotMatch(panel.textContent ?? "", /93 KB|93\.0 KB/);
    const file = buttonByText(panel, "版本发布说明与上线公告.docx");
    assert.ok(file.title.includes(filePath));
    await click(file);
    assert.deepEqual(previewEvents, ["preview", "close"], "close covering Drawer only after authorized preview succeeds");
    const add = panel.querySelector<HTMLElement>('button[aria-label="添加 版本发布说明与上线公告.docx 到会话"]')!;
    await click(add);
    assert.deepEqual(added, [filePath]);
    const remove = panel.querySelector<HTMLElement>('button[aria-label="移除 版本发布说明与上线公告.docx 附件"]')!;
    assert.equal(remove.getAttribute("aria-pressed"), "true");
    await click(remove);
    assert.ok(panel.querySelector('button[aria-label="添加 版本发布说明与上线公告.docx 到会话"]'));
    await click(panel.querySelector<HTMLElement>('button[aria-label="添加 版本发布说明与上线公告.docx 到会话"]')!);
    await act(async () => removeFromComposer());
    assert.ok(panel.querySelector('button[aria-pressed="false"]'), "external Composer removal clears tree state");
    await click(panel.querySelector<HTMLElement>('button[aria-label="返回根目录"]')!);
    assert.match(panel.textContent ?? "", /Other/);
    assert.equal(requests.length, count, "returning to root does not reload");
    await click(buttonByText(panel, "Other"));
    const focusEmpty = panel.querySelector<HTMLElement>('button[aria-label="聚焦目录 Other"]')!;
    focusEmpty.focus();
    await click(focusEmpty);
    assert.match(panel.textContent ?? "", /目录为空/);
    assert.equal(document.activeElement, panel.querySelector("nav button:last-child"));
    await click(panel.querySelector<HTMLElement>('button[aria-label="返回根目录"]')!);
    const focusRestricted = panel.querySelector<HTMLElement>('button[aria-label="聚焦目录 Restricted"]')!;
    focusRestricted.focus();
    await click(focusRestricted);
    assert.match(panel.textContent ?? "", /没有权限访问/);
    assert.equal(document.activeElement, panel.querySelector("nav button:last-child"));
    await click(panel.querySelector<HTMLElement>('button[aria-label="返回 Restricted"]')!);
    assert.equal(panel.querySelector("nav"), null, "root does not repeat the active drive path");
    assert.equal(document.activeElement, panel.querySelector("input"), "return to root focuses the visible search instead of a removed breadcrumb");
    assert.match(panel.textContent ?? "", /Other/);
    const refresh = container.querySelector<HTMLElement>('button[aria-label="刷新本地文件"]')!;
    await click(refresh);
    assert.doesNotMatch(panel.querySelector("nav")?.textContent ?? "", /Projects|IB012/);
    assert.match(panel.textContent ?? "", /Projects/);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    fixture.close();
  }
});

test("authenticates the disconnected local file server from the files area", async () => {
  const fixture = installSelectTestDom();
  document.body.innerHTML = '<div id="root"></div>';
  const [{ act, createElement }, { ProfileDriveWorkspaceBrowser }] = await Promise.all([
    import("react"), import("../ProfileDriveWorkspaceBrowser"),
  ]);
  const originalFetch = globalThis.fetch;
  let connected = false;
  let failConnect = true;
  let releaseConnect: (() => void) | undefined;
  let connectCalls = 0;
  let connectionChanges = 0;
  const onConnectionChange = () => { connectionChanges += 1; };
  window.addEventListener(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT, onConnectionChange);
  const listedSources: string[] = [];
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json" },
    });
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) return json({ credential: "handoff-jwt" });
    if (url.endsWith("/api/profile-drive/status")) return json({ connected, reauthRequired: false });
    if (url.endsWith("/api/profile-drive/connect")) {
      connectCalls += 1;
      if (failConnect) return json({ status: "credential_rejected" }, 403);
      await new Promise<void>((resolve) => { releaseConnect = resolve; });
      connected = true;
      return json({ connected: true });
    }
    if (url.endsWith("/api/profile-drive/files/list")) {
      const body = JSON.parse(String(init.body)) as { source: string; path: string };
      listedSources.push(body.source);
      return json({ status: "success", path: body.path, truncated: false, entries: [{
        path: `${body.source}.txt`, name: `${body.source}.txt`, type: "file", size: 1,
        lastModifiedUtc: "2026-09-21T00:00:00Z",
      }] });
    }
    throw new Error(`Unexpected request: ${url}`);
  }) as typeof fetch;
  const container = document.getElementById("root")!;
  const root = await createLobeTestRoot(container);
  try {
    await act(async () => {
      root.render(createElement(ProfileDriveWorkspaceBrowser, {
        sessionId: null, onImported: () => undefined, onAddToConversation: () => undefined,
      }));
      await flush(); await flush();
    });
    const local = container.querySelector('[data-profile-drive-source="profile"]')!;
    const publicSource = container.querySelector('[data-profile-drive-source="public"]')!;
    assert.match(local.textContent ?? "", /服务器未连接/);
    assert.doesNotMatch(publicSource.textContent ?? "", /认证本地文件服务器/);
    const trigger = buttonByText(local, "认证本地文件服务器");
    await act(async () => { trigger.click(); });
    let dialog = document.querySelector('[role="dialog"]')!;
    assert.match(dialog.textContent ?? "", /认证本地文件服务器/);
    let password = dialog.querySelector<HTMLInputElement>("#file-server-password")!;
    assert.equal(password.type, "password");
    await act(async () => { changeInput(password, "synthetic-password"); });
    await act(async () => {
      dialog.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });
    assert.match(dialog.textContent ?? "", /企业账号或密码不正确/);
    assert.match(local.textContent ?? "", /服务器未连接/);
    assert.deepEqual(listedSources, []);
    await act(async () => { buttonByText(dialog, "取消").click(); await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.activeElement, trigger);
    assert.match(local.textContent ?? "", /服务器未连接/);
    await act(async () => { trigger.click(); });
    dialog = document.querySelector('[role="dialog"]')!;
    await act(async () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 300));
    });
    assert.equal(document.querySelector('[role="dialog"]'), null);
    await act(async () => { trigger.click(); });
    const idleBackdrop = document.querySelector<HTMLElement>('[role="presentation"][data-open]')!;
    assert.ok(idleBackdrop);
    await act(async () => {
      idleBackdrop.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      idleBackdrop.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, button: 0 }));
      idleBackdrop.click();
      await new Promise((resolve) => setTimeout(resolve, 300));
    });
    assert.equal(document.querySelector('[role="dialog"]'), null);
    await act(async () => { trigger.click(); });
    dialog = document.querySelector('[role="dialog"]')!;
    password = dialog.querySelector<HTMLInputElement>("#file-server-password")!;
    assert.equal(password.value, "");
    failConnect = false;
    await act(async () => { changeInput(password, "synthetic-password"); });
    await act(async () => {
      const form = dialog.querySelector("form")!;
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });
    assert.ok(releaseConnect);
    const close = dialog.querySelector<HTMLButtonElement>('button[aria-label="关闭本地文件服务器认证"]')!;
    assert.equal(close.disabled, true);
    assert.equal(buttonByText(dialog, "取消").disabled, true);
    const backdrop = document.querySelector<HTMLElement>('[role="presentation"][data-open]')!;
    assert.ok(backdrop);
    await act(async () => {
      close.click();
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      backdrop.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      backdrop.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, button: 0 }));
      backdrop.click();
      await flush();
    });
    assert.ok(document.querySelector('[role="dialog"]'));
    assert.equal(connectionChanges, 0);
    assert.equal(connectCalls, 2);
    await act(async () => { releaseConnect!(); await flush(); await flush(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.activeElement, container.querySelector("#profile-drive-profile-tab"));
    assert.equal(connectionChanges, 1);
    assert.match(local.textContent ?? "", /profile\.txt/);
    assert.match(publicSource.textContent ?? "", /public\.txt/);
    assert.deepEqual(listedSources.sort(), ["profile", "public"]);
    assert.doesNotMatch(local.textContent ?? "", /服务器未连接/);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    window.removeEventListener(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT, onConnectionChange);
    fixture.close();
  }
});
