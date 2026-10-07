import assert from "node:assert/strict";
import test from "node:test";

import { ApiRequestError } from "../../services/api/fetch.ts";
import { buildChatSubmissionFailureGuidance } from "../useAgent.ts";

test("preflight rejection says the Run was not created", () => {
  const guidance = buildChatSubmissionFailureGuidance({
    error: new ApiRequestError(
      "当前账号无权使用这个专家。",
      403,
      "capability_not_authorized",
      "rejected_before_persist",
      "diag_0123456789abcdef",
    ),
    message: "当前账号无权使用这个专家。",
    problemNumber: "submission-fallback",
    persistedRunPossible: false,
  });

  assert.equal(
    guidance.retained,
    "任务未创建，也未进入执行队列；没有消耗一次完整运行。",
  );
  assert.match(guidance.nextAction, /联系管理员/);
  assert.equal(guidance.problemNumber, "diag_0123456789abcdef");
});

test("lost submission acknowledgement warns that background work may continue", () => {
  const guidance = buildChatSubmissionFailureGuidance({
    error: new TypeError("network offline"),
    message: "暂时无法确认任务状态。",
    problemNumber: "submission-1",
    persistedRunPossible: true,
  });

  assert.match(guidance.retained, /后台也可能仍在继续/);
  assert.match(guidance.nextAction, /不要重复提交/);
  assert.equal(guidance.problemNumber, "submission-1");
});

test("service unavailability is not described as an account permission problem", () => {
  const guidance = buildChatSubmissionFailureGuidance({
    error: new ApiRequestError(
      "执行服务暂时不可用。",
      503,
      "execution_service_not_available",
    ),
    message: "执行服务暂时不可用。",
    problemNumber: "submission-2",
    persistedRunPossible: false,
  });

  assert.doesNotMatch(guidance.nextAction, /重新登录/);
  assert.match(guidance.nextAction, /修正输入|联系管理员/);
});

for (const [status, code, expected, forbidden] of [
  [500, "chat_submission_internal_error", /稍后重试/, /修正输入|重新登录/],
  [503, "execution_service_not_available", /稍后重试/, /修正输入|重新登录/],
  [403, "required_capability_unavailable", /专家或工具配置/, /重新登录/],
  [403, "capability_not_authorized", /有权使用/, /重新登录/],
  [503, "context_file_storage_unavailable", /稍后重试/, /重新上传|修正输入/],
  [400, "current_request_too_large", /缩短或拆分当前请求/, /重新上传/],
  [400, "input_context_too_large", /减少附件/, /服务不可用/],
  [400, "input_image_invalid", /图片格式/, /服务不可用/],
  [409, "agent_profile_revision_stale", /刷新专家配置/, /修正输入/],
  [409, "session_workspace_mismatch", /工作区/, /修正输入/],
  [409, "user_active_run_limit_exceeded", /等待运行中的任务/, /修正输入/],
  [401, "unauthorized", /重新登录/, /修正输入/],
  [422, "validation_error", /必填项和格式/, /重新登录/],
] as const) {
  test(`confirmed admission ${code} keeps cause-specific recovery`, () => {
    const guidance = buildChatSubmissionFailureGuidance({
      error: new ApiRequestError("private diagnostic", status, code),
      message: "safe projected copy",
      problemNumber: "submission-safe",
      persistedRunPossible: false,
    });
    assert.match(guidance.nextAction, expected);
    assert.doesNotMatch(guidance.nextAction, forbidden);
    assert.doesNotMatch(JSON.stringify(guidance), /private diagnostic/);
  });
}

test("malformed success cannot become a confirmed rejection or blind retry", () => {
  for (const persistedRunPossible of [true, false]) {
    const guidance = buildChatSubmissionFailureGuidance({
      error: new ApiRequestError("safe protocol error", 200, "api_response_invalid"),
      message: "safe projected copy",
      problemNumber: "submission-safe",
      persistedRunPossible,
    });
    assert.match(guidance.retained, /任务可能已经创建/);
    assert.match(guidance.nextAction, /不要重复提交/);
    assert.doesNotMatch(guidance.retained, /任务未创建/);
  }
});

test("guidance rejects an untrusted diagnostic identifier", () => {
  const guidance = buildChatSubmissionFailureGuidance({
    error: new ApiRequestError("safe", 500, "chat_submission_internal_error", undefined, "private-secret"),
    message: "safe projected copy",
    problemNumber: "submission-safe",
    persistedRunPossible: false,
  });
  assert.equal(guidance.problemNumber, "submission-safe");
});
