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
