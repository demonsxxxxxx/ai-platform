import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { act } from "react";
import { createRoot } from "react-dom/client";

import "../../../i18n";
import type { AdminUserDiagnosticsResponse } from "../../../services/api/adminUsers";
import { AdminUserDiagnosticsDrawer } from "../AdminUserDiagnosticsDrawer";


test("administrator user diagnostics links verified Runs without rendering raw payloads", async () => {
  const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
    url: "http://localhost/users",
  });
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

  const user = {
    user_id: "user@example.com",
    display_name: "测试用户",
    status: "active",
    session_count: 1,
    run_count: 1,
    queued_run_count: 0,
    running_run_count: 0,
    succeeded_run_count: 0,
    failed_run_count: 1,
    cancelled_run_count: 0,
  };
  const diagnostics: AdminUserDiagnosticsResponse = {
    schema_version: "ai-platform.admin-user-diagnostics.v1",
    user,
    sessions: [
      {
        session_id: "session-a",
        workspace_id: "workspace-a",
        agent_id: "agent-a",
        status: "active",
        purpose: "conversation",
        run_count: 1,
        failed_run_count: 1,
      },
    ],
    runs: [
      {
        run_id: "run-a",
        session_id: "session-a",
        workspace_id: "workspace-a",
        status: "failed",
        agent_id: "agent-a",
        execution_kind: "skill",
        error_code: "worker_execution_failed",
      },
    ],
    audit: [
      {
        audit_id: "audit-a",
        actor_user_id: "user@example.com",
        action: "run.failed",
        target_type: "run",
        target_id: "run-a",
        relations: ["actor", "run"],
        run_id: "run-a",
      },
    ],
    limits: { sessions: 20, runs: 50, audit: 50 },
  };
  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
  let closeCount = 0;

  try {
    await act(async () => {
      root.render(
        <AdminUserDiagnosticsDrawer
          user={user}
          diagnostics={diagnostics}
          error={null}
          loading={false}
          onClose={() => {
            closeCount += 1;
          }}
        />,
      );
    });

    assert.ok(container.querySelector('[data-admin-user-diagnostics="user@example.com"]'));
    assert.match(container.textContent ?? "", /run-a/);
    assert.match(container.textContent ?? "", /actor \/ run/);
    assert.doesNotMatch(container.textContent ?? "", /payload_json|PRIVATE_PROMPT/);
    assert.equal(
      container.querySelector("a")?.getAttribute("href"),
      "/runs?user_id=user%40example.com&run_id=run-a",
    );

    const closeButton = container.querySelector("aside button") as HTMLButtonElement | null;
    assert.ok(closeButton);
    await act(async () => {
      closeButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.equal(closeCount, 1);
  } finally {
    await act(async () => {
      root.unmount();
    });
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
    dom.window.close();
  }
});
