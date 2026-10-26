import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installTestDom } from "./testDom";

import type { UseAgentReturn } from "../types.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";
import { installBrowserAuthTestDb } from "../../__tests__/browserAuthTestDb.ts";

const dom = installTestDom();
dom.window.history.replaceState(null, "", "/chat");
after(() => dom.close());
installBrowserAuthTestDb();

function clearPersistedSubmissionReferences() {
  for (let index = dom.window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = dom.window.localStorage.key(index);
    if (key?.startsWith("ai_platform_chat_submission")) {
      dom.window.localStorage.removeItem(key);
    }
  }
}

async function settle(act: typeof import("react").act) {
  for (let index = 0; index < 8; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function loadHarness() {
  clearPersistedSubmissionReferences();
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider } = await import("../../useAuth.tsx");
  const { useAgent } = await import("../../useAgent.ts");
  const { authApi } = await import("../../../services/api/auth.ts");
  let snapshot: UseAgentReturn | null = null;
  const container = dom.document.createElement("div");
  const root = createRoot(container as never);
  const originalGetCurrentUser = authApi.getCurrentUser;
  const originalBootstrapAuthContext = authApi.bootstrapAuthContext;
  authApi.getCurrentUser = async () => ({
    id: "user-a",
    tenant_id: "tenant-a",
    username: "user-a",
    email: "user-a@example.test",
    roles: [],
    permissions: [],
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });
  authApi.bootstrapAuthContext = async (request) => ({
    status: "ready",
    protocol_version: 2,
    generation: request.generation,
  });

  function Probe() {
    snapshot = useAgent();
    return null;
  }

  await React.act(async () => {
    root.render(
      React.createElement(AuthProvider, null, React.createElement(Probe)),
    );
  });

  return {
    act: React.act,
    get hook() {
      assert.ok(snapshot);
      return snapshot;
    },
    async cleanup() {
      await React.act(async () => root.unmount());
      authApi.getCurrentUser = originalGetCurrentUser;
      authApi.bootstrapAuthContext = originalBootstrapAuthContext;
    },
  };
}

test("useAgent forwards only an explicit Agent profile without inheriting it", async () => {
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const selectedAgentProfile = {
    agent_id: "agt_support",
  } as const;

  const harness = await loadHarness();
  const submissions: unknown[][] = [];
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: undefined,
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    let firstOutcome: { status: string } | undefined;
    await harness.act(async () => {
      firstOutcome = await harness.hook.sendMessage(
        "explicit profile",
        undefined,
        undefined,
        null,
        selectedAgentProfile,
      );
    });
    await settle(harness.act);

    assert.equal(firstOutcome?.status, "accepted");
    assert.deepEqual(submissions[0]?.[10], selectedAgentProfile);

    let secondOutcome: { status: string } | undefined;
    await harness.act(async () => {
      secondOutcome = await harness.hook.sendMessage("generic later");
    });
    await settle(harness.act);

    assert.equal(secondOutcome?.status, "accepted");
    assert.equal(submissions.length, 2);
    assert.equal(submissions[1]?.[10], null);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
  }
});

test("generic Chat never inherits an Agent Market binding", async () => {
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const harness = await loadHarness();
  const submissions: unknown[][] = [];
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: undefined,
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.equal((await harness.hook.sendMessage("generic first")).status, "accepted");
      assert.equal((await harness.hook.sendMessage("generic later")).status, "accepted");
    });
    await settle(harness.act);

    assert.equal(submissions.length, 2);
    assert.equal(submissions[0]?.[10], null);
    assert.equal(submissions[1]?.[10], null);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
  }
});

test("a recovered Agent Conversation keeps composer models and omits client capabilities", async () => {
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const harness = await loadHarness();
  const submissions: unknown[][] = [];
  let authoritativeMode: "current" | "agent-mismatch" | "rejected" = "current";
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: sessionId.startsWith("session-agent") ? "agt_support" : "general-agent",
    created_at: "2026-07-29T00:00:00Z",
    updated_at: "2026-07-29T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => {
    if (authoritativeMode === "rejected") {
      throw Object.assign(new Error("stale"), { status: 409 });
    }
    const isAgentSession = sessionId.startsWith("session-agent");
    return {
      session_id: sessionId,
      workspace_id: "default",
      agent_id:
        authoritativeMode === "agent-mismatch"
          ? "agt_other"
          : isAgentSession
            ? "agt_support"
            : "general-agent",
      title: isAgentSession ? "支持助手" : "普通会话",
      purpose: "conversation",
      agent_conversation: isAgentSession
        ? {
            agent_id: "agt_support",
            revision: 7,
            name: "支持助手",
            description: "处理已授权的支持请求。",
            starter_prompts: ["帮我处理支持请求"],
            avatar_ref: "builtin:assistant",
            avatar_seed: "agt-support",
            published_at: "2026-08-04T01:00:00Z",
          }
        : null,
    };
  };
  sessionApi.getEvents = async () => ({ current_run_id: null, events: [] });
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: undefined,
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-agent");
    });
    await settle(harness.act);

    await harness.act(async () => {
      assert.equal(
        (
          await harness.hook.sendMessage(
          "bound first",
            {
              model_id: "client-model",
              model: "provider/client-model",
              enable_thinking: "high",
            },
            undefined,
            {
              skill_id: "client-skill",
              expected_version: "client-version",
            },
            { agent_id: "forged-agent" },
          )
        ).status,
        "accepted",
      );
      assert.equal((await harness.hook.sendMessage("bound later")).status, "accepted");
    });
    await settle(harness.act);

    assert.equal(submissions.length, 2);
    assert.equal(submissions[0]?.[11], "high");
    assert.equal(submissions[1]?.[11], "auto");
    assert.deepEqual(submissions[0]?.[2], {
      model_id: "client-model",
      model: "provider/client-model",
    });
    assert.deepEqual(submissions[1]?.[2], {});
    for (const submission of submissions) {
      assert.equal(submission[1], "session-agent");
      assert.equal("enable_thinking" in (submission[2] as object), false);
      assert.equal(submission[4], undefined, "Skill selectors must be omitted");
      assert.equal(submission[6], undefined, "selected Skill must be omitted");
      assert.equal(submission[9], undefined, "MCP selectors must be omitted");
      assert.deepEqual(submission[10], { agent_id: "agt_support" });
    }

    await harness.act(async () => {
      await harness.hook.loadHistory("session-generic");
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.equal(
        (
          await harness.hook.sendMessage(
            "generic after Agent",
            { model_id: "generic-model" },
            undefined,
            null,
            { agent_id: "forged-agent" },
          )
        ).status,
        "accepted",
      );
    });
    await settle(harness.act);

    assert.equal(submissions.length, 3);
    assert.equal(submissions[2]?.[1], "session-generic");
    assert.deepEqual(submissions[2]?.[2], { model_id: "generic-model" });
    assert.deepEqual(submissions[2]?.[4], []);
    assert.equal(submissions[2]?.[10], null);

    authoritativeMode = "agent-mismatch";
    await harness.act(async () => {
      await harness.hook.loadHistory("session-agent-mismatch");
      assert.equal((await harness.hook.sendMessage("must not submit mismatch")).status, "failed");
    });
    await settle(harness.act);
    assert.equal(submissions.length, 3);

    authoritativeMode = "rejected";
    await harness.act(async () => {
      await harness.hook.loadHistory("session-agent-stale");
      assert.equal((await harness.hook.sendMessage("must not submit stale")).status, "failed");
    });
    await settle(harness.act);
    assert.equal(submissions.length, 3);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
  }
});

test("an ordinary failed file-Skill session preserves generic continuation authority", async () => {
  const harness = await loadHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalSubmitChat = sessionApi.submitChat;
  const submissions: unknown[][] = [];
  const historyReads: Array<string | null> = [];
  const persistedEvents = [
    {
      id: "message-file-skill",
      event_type: "user:message",
      run_id: "run-file-skill-failed",
      timestamp: "2026-07-30T01:38:00Z",
      data: {
        message_id: "message-file-skill",
        content: "创建受控文件",
        locked_skill_label: "controlled-file",
      },
    },
  ];
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: "general-agent",
    title: "普通会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.getEvents = async (_sessionId, options) => {
    historyReads.push(options?.run_id || null);
    return { current_run_id: "run-file-skill-failed", events: persistedEvents };
  };
  sessionApi.getStatus = (async () => ({
    session_id: "session-file-skill-failed",
    run_id: "run-file-skill-failed",
    status: "error",
    raw_status: "failed",
    error: "executor_failure",
  })) as typeof sessionApi.getStatus;
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.notEqual(
        await harness.hook.loadHistory("session-file-skill-failed"),
        null,
      );
    });
    await settle(harness.act);

    assert.deepEqual(historyReads, [null, "run-file-skill-failed"]);
    assert.equal(harness.hook.error, null);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(
      harness.hook.messages.filter((message) => message.role === "user").length,
      1,
    );
    assert.equal(harness.hook.messages[0]?.lockedSkillLabel, "controlled-file");
    assert.equal(
      harness.hook.messages
        .flatMap((message) => message.parts || [])
        .some((part) => part.type === "artifact"),
      false,
    );

    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("同会话继续"), {
        status: "accepted",
      });
    });
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0]?.[1], "session-file-skill-failed");
    assert.equal(submissions[0]?.[8], "general-agent");
    assert.equal(submissions[0]?.[10], null);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("required session projection failures remain fail-closed with bounded diagnostics", async () => {
  for (const failingRead of ["session", "authoritative"] as const) {
    const harness = await loadHarness();
    const { sessionApi } = await import("../../../services/api/session.ts");
    const originalGet = sessionApi.get;
    const originalGetAuthoritative = sessionApi.getAuthoritative;
    const originalSubmitChat = sessionApi.submitChat;
    const originalConsoleError = console.error;
    const diagnostics: unknown[][] = [];
    let submissions = 0;
    console.error = (...args: unknown[]) => diagnostics.push(args);
    sessionApi.get = async (sessionId) => {
      if (failingRead === "session") {
        throw new ApiRequestError(
          "private compatibility response",
          503,
          "session_projection_unavailable",
        );
      }
      return {
        id: sessionId,
        agent_id: "general-agent",
        created_at: "2026-07-30T01:38:00Z",
        updated_at: "2026-07-30T01:38:00Z",
        is_active: true,
        metadata: {},
      };
    };
    sessionApi.getAuthoritative = async (sessionId) => {
      if (failingRead === "authoritative") {
        throw new ApiRequestError(
          "private authoritative response",
          503,
          "session_projection_unavailable",
        );
      }
      return {
        session_id: sessionId,
        workspace_id: "default",
        agent_id: "general-agent",
        title: "普通会话",
        purpose: "conversation",
        agent_conversation: null,
      };
    };
    sessionApi.submitChat = (async () => {
      submissions += 1;
      return { status: "needs_confirmation", suggestions: [] };
    }) as typeof sessionApi.submitChat;

    try {
      await harness.act(async () => {
        assert.equal(await harness.hook.loadHistory(`session-${failingRead}`), null);
      });
      await settle(harness.act);
      assert.equal(harness.hook.error, "加载会话失败");
      await harness.act(async () => {
        assert.deepEqual(await harness.hook.sendMessage("不得提交"), {
          status: "failed",
        });
      });
      assert.equal(submissions, 0);
      assert.deepEqual(diagnostics, [
        [
          "[loadHistory] failed",
          {
            phase: "session_projection",
            status: 503,
            code: "session_projection_unavailable",
          },
        ],
      ]);
    } finally {
      console.error = originalConsoleError;
      sessionApi.get = originalGet;
      sessionApi.getAuthoritative = originalGetAuthoritative;
      sessionApi.submitChat = originalSubmitChat;
      await harness.cleanup();
    }
  }
});

test("event history failure preserves generic identity but requires confirmed history before continuation", async () => {
  const harness = await loadHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const originalSubmitChat = sessionApi.submitChat;
  const originalConsoleError = console.error;
  const diagnostics: unknown[][] = [];
  const submissions: unknown[][] = [];
  let historyAvailable = false;
  console.error = (...args: unknown[]) => diagnostics.push(args);
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: "general-agent",
    title: "普通会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.getEvents = async () => {
    if (!historyAvailable) {
      throw new ApiRequestError(
        "private event payload",
        502,
        "history_events_unavailable",
      );
    }
    return { current_run_id: null, events: [] };
  };
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.equal(await harness.hook.loadHistory("session-history-retry"), null);
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("继续同一会话"), {
        status: "failed",
      });
    });
    assert.equal(submissions.length, 0);
    assert.equal(harness.hook.continuationPending, true);
    assert.deepEqual(diagnostics, [
      [
        "[loadHistory] failed",
        {
          phase: "event_history",
          status: 502,
          code: "history_events_unavailable",
        },
      ],
    ]);

    historyAvailable = true;
    await harness.act(async () => {
      assert.notEqual(
        await harness.hook.loadHistory("session-history-retry"),
        null,
      );
    });
    await settle(harness.act);
    assert.equal(harness.hook.error, null);
    assert.equal(harness.hook.continuationPending, false);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("继续同一会话"), { status: "accepted" });
    });
    assert.equal(submissions[0]?.[1], "session-history-retry");
    assert.equal(submissions[0]?.[10], null);
  } finally {
    console.error = originalConsoleError;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("an authoritative identity mismatch remains fail-closed", async () => {
  const harness = await loadHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalSubmitChat = sessionApi.submitChat;
  const originalConsoleError = console.error;
  const diagnostics: unknown[][] = [];
  let submissions = 0;
  console.error = (...args: unknown[]) => diagnostics.push(args);
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async () => ({
    session_id: "different-session",
    workspace_id: "default",
    agent_id: "general-agent",
    title: "冲突会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.submitChat = (async () => {
    submissions += 1;
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.equal(await harness.hook.loadHistory("session-identity-mismatch"), null);
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("不得提交"), {
        status: "failed",
      });
    });
    assert.equal(submissions, 0);
    assert.deepEqual(diagnostics, [
      [
        "[loadHistory] failed",
        {
          phase: "identity_validation",
          status: null,
          code: "agent_conversation_identity_mismatch",
        },
      ],
    ]);
  } finally {
    console.error = originalConsoleError;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});
