import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCheckpointForkUrl,
  buildMessageCheckpointUrl,
  buildMessageForkUrl,
  buildRunCancelUrl,
  buildSessionListUrl,
  buildSessionRunsUrl,
  buildChatSubmissionUrl,
  buildChatSubmissionRetryAdmissionUrl,
  buildSubmitChatUrl,
  buildSubmitChatBody,
  isChatStreamNeedsConfirmation,
  resolveChatSessionAgentId,
} from "../session.ts";

test("fails closed instead of sending unsupported project or favorite filters", () => {
  assert.equal(
    buildSessionListUrl({ project_id: "project-1", favorites_only: true }),
    "/api/sessions",
  );
});

test("builds the default session runs url", () => {
  assert.equal(
    buildSessionRunsUrl("session-1"),
    "/api/sessions/session-1/runs",
  );
});

test("builds the canonical run cancel url", () => {
  assert.equal(
    buildRunCancelUrl("run-1"),
    "/api/ai/runs/run-1/cancel",
  );
});

test("includes trace_id when looking up a specific run by trace", () => {
  assert.equal(
    buildSessionRunsUrl("session-1", { trace_id: "trace-123" }),
    "/api/sessions/session-1/runs?trace_id=trace-123",
  );
});

test("includes user_timezone in the submit chat body when available", () => {
  assert.deepEqual(
    buildSubmitChatBody({
      message: "hello",
      sessionId: "session-1",
      userTimezone: "Asia/Shanghai",
    }),
    {
      message: "hello",
      session_id: "session-1",
      agent_options: undefined,
      attachments: undefined,
      disabled_skills: undefined,
      enabled_skills: undefined,
      disabled_mcp_tools: undefined,
      user_timezone: "Asia/Shanghai",
    },
  );
});

test("carries an opaque submission id and resolves its exact status route", () => {
  assert.deepEqual(
    buildSubmitChatBody({
      message: "hello",
      submissionId: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    }),
    {
      message: "hello",
      session_id: undefined,
      agent_options: undefined,
      attachments: undefined,
      disabled_skills: undefined,
      enabled_skills: undefined,
      disabled_mcp_tools: undefined,
      submission_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    },
  );
  assert.equal(
    buildChatSubmissionUrl("7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"),
    "/api/chat/submissions/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
  );
  assert.equal(
    buildChatSubmissionRetryAdmissionUrl("7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"),
    "/api/chat/submissions/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4/retry-admission",
  );
});

test("submits one authorized Skill through the exact nested selector", () => {
  const body = buildSubmitChatBody({
    message: "review this document",
    selectedSkill: {
      skill_id: "document-review",
      expected_version: "a1b2c3d4",
    },
    disabledSkills: ["legacy-review"],
    enabledSkills: ["planning"],
  });

  assert.deepEqual(body, {
    message: "review this document",
    session_id: undefined,
    agent_options: undefined,
    attachments: undefined,
    selected_skill: {
      skill_id: "document-review",
      expected_version: "a1b2c3d4",
    },
    disabled_skills: undefined,
    enabled_skills: undefined,
    disabled_mcp_tools: undefined,
  });
  assert.equal("skill_id" in body, false);
});

test("keeps the fixed capability path unchanged without a selected Skill", () => {
  const body = buildSubmitChatBody({
    message: "plan the rollout",
    disabledSkills: ["document-review"],
    enabledSkills: ["planning"],
  });

  assert.equal("selected_skill" in body, false);
  assert.deepEqual(body.disabled_skills, ["document-review"]);
  assert.deepEqual(body.enabled_skills, ["planning"]);
  assert.equal("skill_id" in body, false);
});

test("detects chat stream confirmation responses without a run id", () => {
  assert.equal(
    isChatStreamNeedsConfirmation({
      status: "needs_confirmation",
      session_id: undefined,
      run_id: null,
      suggestions: [
        {
          capability_id: "document_review",
          label: "文档审核",
          reason: "审核这个 Word",
        },
      ],
    }),
    true,
  );
});

test("uses the routed agent for same-tab session continuation", () => {
  const routedAgentId = resolveChatSessionAgentId(
    {
      session_id: "session-translation",
      run_id: "run-translation",
      trace_id: "trace-translation",
      status: "queued",
      intent_decision: {
        agent_id: "baoyu-translate",
      },
    },
    "general-agent",
  );

  assert.equal(routedAgentId, "baoyu-translate");
  assert.equal(
    buildSubmitChatUrl(routedAgentId),
    "/api/chat/stream?agent_id=baoyu-translate",
  );
});

test("keeps the current agent when the response has no authoritative routed agent", () => {
  assert.equal(
    resolveChatSessionAgentId(
      {
        session_id: "session-a",
        run_id: "run-a",
        trace_id: "trace-a",
        status: "queued",
      },
      "baoyu-translate",
    ),
    "baoyu-translate",
  );
});

test("builds the message fork url", () => {
  assert.equal(
    buildMessageForkUrl("session-1", "message-1"),
    "/api/sessions/session-1/messages/message-1/fork",
  );
});

test("builds the message checkpoint url", () => {
  assert.equal(
    buildMessageCheckpointUrl("session-1", "message-1"),
    "/api/sessions/session-1/messages/message-1/checkpoints",
  );
});

test("builds the checkpoint fork url", () => {
  assert.equal(
    buildCheckpointForkUrl("session-1", "checkpoint-1"),
    "/api/sessions/session-1/checkpoints/checkpoint-1/fork",
  );
});
