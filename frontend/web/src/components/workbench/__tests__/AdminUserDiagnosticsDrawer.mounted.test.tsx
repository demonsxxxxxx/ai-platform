import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { act } from "react";

import "../../../i18n";
import type { AdminUserDiagnosticsResponse } from "../../../services/api/adminUsers";
const fixture = installSelectTestDom();
const dom = { window };
test.after(() => fixture.close());
const { AdminUserDiagnosticsDrawer } = await import("../AdminUserDiagnosticsDrawer");


test("administrator user diagnostics links verified Runs without rendering raw payloads", async () => {
  document.body.innerHTML = '<div id="root"></div>';
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
  const root = await createLobeTestRoot(container);
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

    assert.ok(document.querySelector('[data-admin-user-diagnostics="user@example.com"]'));
    assert.match(document.body.textContent ?? "", /run-a/);
    assert.match(document.body.textContent ?? "", /actor \/ run/);
    assert.doesNotMatch(document.body.textContent ?? "", /payload_json|PRIVATE_PROMPT/);
    assert.equal(
      document.querySelector("a")?.getAttribute("href"),
      "/runs?user_id=user%40example.com&run_id=run-a",
    );

    const closeButton = document.querySelector('[role="dialog"] button') as HTMLButtonElement | null;
    assert.ok(closeButton);
    await act(async () => {
      closeButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.equal(closeCount, 1);
  } finally {
    await act(async () => {
      root.unmount();
    });

  }
});
