import test from "node:test";
import assert from "node:assert/strict";
import {
  buildMessageForkUrl,
  buildRunCancelUrl,
  buildRunControlOperationUrl,
  buildRunRetryUrl,
  buildRunResumeUrl,
  buildSessionListUrl,
  buildAuthoritativeChatSessionUrl,
  buildSessionInputFilesUrl,
  buildSessionRunsUrl,
  buildChatSubmissionUrl,
  buildChatSubmissionRetryAdmissionUrl,
  buildAgentAppRunBody,
  buildAgentAppRunUrl,
  buildSubmitChatUrl,
  buildSubmitChatBody,
  isChatStreamNeedsConfirmation,
  resolveChatSessionAgentId,
  sessionApi,
} from "../session.ts";

const defaultEnterpriseProjection = {
  welcome_message: "",
  starter_prompts: [] as string[],
  capability_summary: "",
  recommended_tasks: [] as string[],
  supported_input_types: ["text", "file"] as ["text", "file"],
  expected_outputs: [] as string[],
  permissions_and_data_access_notice: "",
  published_at: null,
};

test("builds the active session list URL with pagination", () => {
  assert.equal(
    buildSessionListUrl({ status: "active", limit: 20, skip: 40 }),
    "/api/sessions?status=active&limit=20&skip=40",
  );
});

test("builds the authoritative Agent Conversation recovery URL", () => {
  assert.equal(
    buildAuthoritativeChatSessionUrl("session/with space"),
    "/api/ai/chat/sessions/session%2Fwith%20space",
  );
});

test("preserves legacy session get while adding safe authoritative recovery", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (input) => {
    const url = String(input);
    calls.push(url);
    if (url.startsWith("/api/sessions/")) {
      return new Response(
        JSON.stringify({
          id: "session-agent",
          agent_id: "agt_support",
          created_at: "2026-07-29T00:00:00Z",
          updated_at: "2026-07-29T00:00:00Z",
          is_active: true,
          metadata: { agent_id: "agt_support" },
        }),
        { status: 200 },
      );
    }
    return new Response(
      JSON.stringify({
        session_id: "session-agent",
        workspace_id: "default",
        agent_id: "agt_support",
        title: "支持助手",
        agent_conversation: {
          ...defaultEnterpriseProjection,
          agent_id: "agt_support",
          revision: 7,
          name: "支持助手",
          description: "处理已授权的支持请求。",
          avatar_ref: "builtin:assistant",
          category: "support",
          selected_skill: { skill_id: "private-skill" },
          mcp_tool_ids: ["private-mcp"],
          content_hash: "private-hash",
        },
      }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    const legacy = await sessionApi.get("session-agent");
    const authoritative = await sessionApi.getAuthoritative("session-agent");
    assert.equal(legacy?.id, "session-agent");
    assert.deepEqual(authoritative.agent_conversation, {
      ...defaultEnterpriseProjection,
      agent_id: "agt_support",
      revision: 7,
      name: "支持助手",
      description: "处理已授权的支持请求。",
      avatar_ref: "builtin:assistant",
      avatar_seed: "agt_support",
      category: "support",
    });
    assert.equal("selected_skill" in authoritative.agent_conversation!, false);
    assert.equal("content_hash" in authoritative.agent_conversation!, false);
    assert.deepEqual(calls, [
      "/api/sessions/session-agent",
      "/api/ai/chat/sessions/session-agent",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("authoritative recovery keeps ordinary sessions generic and rejects missing sessions", async () => {
  const originalFetch = globalThis.fetch;
  let status = 200;
  globalThis.fetch = (async () =>
    status === 200
      ? new Response(
          JSON.stringify({
            session_id: "session-generic",
            workspace_id: "default",
            agent_id: "general-agent",
            title: "普通会话",
          }),
          { status: 200 },
        )
      : new Response(JSON.stringify({ detail: "session_not_found" }), { status })) as typeof fetch;

  try {
    const generic = await sessionApi.getAuthoritative("session-generic");
    assert.equal(generic.agent_conversation, null);
    status = 404;
    await assert.rejects(
      sessionApi.getAuthoritative("session-missing"),
      (error: unknown) =>
        typeof error === "object" &&
        error !== null &&
        (error as { status?: unknown }).status === 404,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
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

test("MCP retry obtains a fresh context and reuses the exact operation id", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const calls: Array<{
    url: string;
    body?: string | null;
    signal?: AbortSignal | null;
    jwt?: string | null;
  }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      body: typeof init?.body === "string" ? init.body : null,
      signal: init?.signal,
      jwt: new Headers(init?.headers).get("JWT-Authorization"),
    });
    if (calls.length === 1) {
      return new Response(
        JSON.stringify({ detail: "mcp_context_required_for_retry" }),
        { status: 409 },
      );
    }
    if (calls.length === 2) {
      return new Response(
        JSON.stringify({
          mcp_context_id: "mcpctx-fresh",
          expires_at: "2026-08-18T12:00:00Z",
        }),
      );
    }
    return new Response(
      JSON.stringify({
        run_id: "run-child",
        session_id: "session-a",
        status: "queued",
      }),
    );
  }) as typeof fetch;

  try {
    const operationId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
    const result = await sessionApi.retryRun("run-parent", operationId, {
      signal: controller.signal,
    });

    assert.equal(result.run_id, "run-child");
    assert.equal(calls[0]?.url, calls[2]?.url);
    assert.equal(calls[0]?.body, null);
    assert.equal(
      calls[1]?.url,
      "/api/ai/mcp/runtime-contexts",
    );
    assert.equal(calls[1]?.jwt, null);
    assert.deepEqual(JSON.parse(calls[2]?.body ?? "{}"), {
      mcp_context_id: "mcpctx-fresh",
    });
    assert.ok(calls.every((call) => call.signal === controller.signal));
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

test("carries selected platform MCP IDs with only the opaque context id", () => {
  const body = buildSubmitChatBody({
    message: "use an MCP tool",
    mcpContextId: "mcpctx_opaque",
    selectedMcpToolIds: ["inventory-read"],
  });

  assert.equal(body.mcp_context_id, "mcpctx_opaque");
  assert.deepEqual(body.selected_mcp_tool_ids, ["inventory-read"]);
  assert.equal("mcp_gateway_tool_names" in body, false);
  assert.equal("jwt" in body, false);
  assert.equal("token" in body, false);
  assert.equal("authorization" in body, false);
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

test("submits only the exact published Agent profile lock", () => {
  const body = buildSubmitChatBody({
    message: "review this request",
    selectedAgentProfile: {
      agent_id: "agt_support",
      expected_revision: 4,
    },
  });

  assert.deepEqual(body.selected_agent_profile, {
    agent_id: "agt_support",
    expected_revision: 4,
  });
  assert.equal("instructions" in body, false);
  assert.equal("mcp_tool_ids" in body, false);
});

test("builds the selector-free Agent App run URL and deduplicated file body", () => {
  const attachment = {
    id: "client-upload-8bd6fe68-4c41-4577-a4b6-60c3ec36b75a",
    key: "file-a",
    name: "source.pdf",
    type: "document" as const,
    mimeType: "application/pdf",
    size: 42,
    url: "/private/source.pdf",
  };

  assert.equal(
    buildAgentAppRunUrl("agent/with space", "session/with space"),
    "/api/ai/agent-apps/agent%2Fwith%20space/conversations/session%2Fwith%20space/runs",
  );
  assert.deepEqual(
    buildAgentAppRunBody({
      message: "Review this",
      attachments: [attachment, attachment],
      submissionId: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      userTimezone: "Asia/Shanghai",
      mcpContextId: "mcpctx-profile",
    }),
    {
      message: "Review this",
      submission_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      file_ids: ["file-a"],
      user_timezone: "Asia/Shanghai",
      mcp_context_id: "mcpctx-profile",
    },
  );
});

test("omits unfinished Agent App attachments without a server file id", () => {
  assert.deepEqual(
    buildAgentAppRunBody({
      message: "Review this",
      attachments: [
        {
          id: "client-upload-pending",
          key: "",
          name: "pending.pdf",
          type: "document",
          mimeType: "application/pdf",
          size: 42,
          isUploading: true,
        },
      ],
      submissionId: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    }),
    {
      message: "Review this",
      submission_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      file_ids: [],
    },
  );
});

test("pinned Agent conversations submit only through the dedicated selector-free transport", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({
      session_id: "session-agent",
      run_id: "run-agent",
      trace_id: "trace-agent",
      status: "queued",
    }));
  }) as typeof fetch;

  try {
    await sessionApi.submitChat(
      "Review this",
      "session-agent",
      { model_id: "client-override" },
      [],
      ["client-disabled-skill"],
      ["client-disabled-mcp"],
      { skill_id: "client-skill", expected_version: "client-version" },
      "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      "general-agent",
      ["client-mcp"],
      { agent_id: "agt_support", expected_revision: 7 },
      "mcpctx-profile",
    );

    assert.equal(
      calls[0]?.url,
      "/api/ai/agent-apps/agt_support/conversations/session-agent/runs",
    );
    assert.equal(calls[0]?.body.message, "Review this");
    assert.equal(calls[0]?.body.submission_id, "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4");
    assert.deepEqual(calls[0]?.body.file_ids, []);
    assert.equal(calls[0]?.body.mcp_context_id, "mcpctx-profile");
    for (const forbidden of [
      "agent_options",
      "selected_agent_profile",
      "selected_skill",
      "selected_mcp_tool_ids",
      "disabled_skills",
      "disabled_mcp_tools",
      "expected_revision",
    ]) {
      assert.equal(forbidden in calls[0]!.body, false);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
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
