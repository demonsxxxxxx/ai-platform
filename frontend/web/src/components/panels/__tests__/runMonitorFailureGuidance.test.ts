import assert from "node:assert/strict";
import test from "node:test";

import type { AdminRunDetailResponse } from "../../../services/api/adminRuns.ts";
import { buildAdminFailureGuidance } from "../runFailureGuidance.ts";

function detail(
  overrides: Partial<AdminRunDetailResponse> = {},
): AdminRunDetailResponse {
  return {
    run: {
      run_id: "run-problem-1",
      session_id: "session-1",
      user_id: "user-1",
      status: "failed",
      error_code: "terminal_reconciliation_failed",
      error_message: "主体执行完成，但最终答复同步失败。",
    },
    worker_execution: { response: "", actions: [], model: {} },
    events: [],
    steps: [{ step_id: "step-1", status: "succeeded" }],
    artifacts: [
      {
        artifact_id: "artifact-1",
        artifact_type: "document",
        label: "结果.docx",
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 10,
      },
    ],
    sandbox_leases: [],
    skill_snapshots: [],
    audit: [],
    ...overrides,
  };
}

test("Run Monitor failure guidance explains retained work and problem number", () => {
  assert.deepEqual(buildAdminFailureGuidance(detail()), {
    whatHappened: "主体执行已经结束，但最终结果同步失败。",
    retained: "已保留 1 个已完成步骤、1 个已登记文件。",
    nextAction:
      "先查看已登记文件，再重试结果同步；不要重新执行已完成的主体工作。",
    problemNumber: "run-problem-1",
  });
});

test("Run Monitor blocks blind retry when a tool outcome is unconfirmed", () => {
  const guidance = buildAdminFailureGuidance(
    detail({
      run: {
        ...detail().run,
        error_code: "mcp_execution_outcome_unknown",
        error_message: "工具执行结果尚未确认。",
      },
      steps: [],
      artifacts: [],
    }),
  );

  assert.match(guidance?.nextAction ?? "", /请勿直接重试/);
  assert.equal(
    guidance?.retained,
    "未记录到可确认的已完成步骤或文件。",
  );
});
