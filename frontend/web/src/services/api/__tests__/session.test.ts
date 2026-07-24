import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCheckpointForkUrl,
  buildMessageCheckpointUrl,
  buildMessageForkUrl,
  buildRunCancelUrl,
  buildRunControlOperationUrl,
  buildRunRetryUrl,
  buildRunResumeUrl,
  buildSessionListUrl,
  buildSessionInputFilesUrl,
  buildSessionRunsUrl,
  buildChatSubmissionUrl,
  buildChatSubmissionRetryAdmissionUrl,
  buildSubmitChatUrl,
  buildSubmitChatBody,
  isChatStreamNeedsConfirmation,
  resolveChatSessionAgentId,
  sessionApi,
} from "../session.ts";

test("builds the active session list URL with pagination", () => {
  assert.equal(
    buildSessionListUrl({ status: "active", limit: 20, skip: 40 }),
    "/api/sessions?status=active&limit=20&skip=40",
  );
});

test("builds the default session runs url", () => {
  assert.equal(
    buildSessionRunsUrl("session-1"),
    "/api/sessions/session-1/runs",
  );
});

test("builds the authoritative session input-file projection url with opaque session id", () => {
  assert.equal(
    buildSessionInputFilesUrl("session/a"),
    "/api/ai/chat/sessions/session%2Fa/files",
  );
});

test("builds the canonical run cancel url", () => {
  assert.equal(
    buildRunCancelUrl("run-1"),
    "/api/ai/runs/run-1/cancel",
  );
});

test("builds the canonical retry and checkpoint-resume URLs", () => {
  const operationId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  assert.equal(
    buildRunRetryUrl("run/with space", operationId),
    "/api/ai/runs/run%2Fwith%20space/retry?operation_id=7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
  );
  assert.equal(
    buildRunResumeUrl("run/with space", operationId),
    "/api/ai/runs/run%2Fwith%20space/resume?operation_id=7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
  );
  assert.equal(
    buildRunControlOperationUrl("run/with space", "resume", operationId),
    "/api/ai/runs/run%2Fwith%20space/control-operations/resume/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
  );
});

test("run-control mutations use the shared cookie-session transport and forward AbortSignal", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const calls: Array<{ url: string; method?: string; signal?: AbortSignal | null }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method,
      signal: init?.signal,
    });
    return new Response(
      JSON.stringify({ run_id: "run-child", session_id: "session-child", status: "queued" }),
    );
  }) as typeof fetch;

  try {
    const operationId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
    await sessionApi.cancelRun("run-parent", { signal: controller.signal });
    await sessionApi.retryRun("run-parent", operationId, { signal: controller.signal });
    await sessionApi.resumeRun("run-parent", operationId, { signal: controller.signal });
    await sessionApi.resolveRunControlOperation("run-parent", "retry", operationId, {
      signal: controller.signal,
    });
    assert.deepEqual(
      calls.map((call) => [call.url, call.method, call.signal]),
      [
        ["/api/ai/runs/run-parent/cancel", "POST", controller.signal],
        [
          "/api/ai/runs/run-parent/retry?operation_id=7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
          "POST",
          controller.signal,
        ],
        [
          "/api/ai/runs/run-parent/resume?operation_id=7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
          "POST",
          controller.signal,
        ],
        [
          "/api/ai/runs/run-parent/control-operations/retry/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
          undefined,
          controller.signal,
        ],
      ],
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
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

test("preserves MCP selection tri-state in the structured Chat request", () => {
  const omitted = buildSubmitChatBody({ message: "inherit" });
  const cleared = buildSubmitChatBody({
    message: "clear",
    selectedMcpToolIds: [],
  });
  const selected = buildSubmitChatBody({
    message: "select",
    selectedMcpToolIds: ["tenant-search"],
  });

  assert.equal("selected_mcp_tool_ids" in omitted, false);
  assert.deepEqual(cleared.selected_mcp_tool_ids, []);
  assert.deepEqual(selected.selected_mcp_tool_ids, ["tenant-search"]);
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

test("resolves a chat submission with cache disabled", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; method?: string; cache?: RequestCache }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method,
      cache: init?.cache,
    });
    return new Response(
      JSON.stringify({
        submission_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        state: "queued",
      }),
    );
  }) as typeof fetch;

  try {
    await sessionApi.getChatSubmission("7ea93033-30f5-40ea-8a33-2f3c6e7b21c4");
    assert.deepEqual(calls, [
      {
        url: "/api/chat/submissions/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        method: undefined,
        cache: "no-store",
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
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
