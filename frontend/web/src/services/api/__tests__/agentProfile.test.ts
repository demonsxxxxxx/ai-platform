import assert from "node:assert/strict";
import test from "node:test";

import {
  agentProfileApi,
  buildAgentProfileCatalogUrl,
  buildAgentProfileDetailUrl,
} from "../agentProfile.ts";
import {
  projectAgentConversationIdentity,
  projectAgentProfilePublicProjection,
  validateAgentProfileAdminProjection,
} from "../../../types/agentProfile.ts";

const defaultEnterpriseProjection = {
  welcome_message: "",
  starter_prompts: [] as string[],
  capability_summary: "",
  recommended_tasks: [] as string[],
  supported_input_types: ["text", "file"] as ["text", "file"],
  expected_outputs: [] as string[],
  permissions_and_data_access_notice: "",
  avatar_seed: "agt_support",
  published_at: null,
};

test("builds server-authoritative catalog and detail URLs", () => {
  assert.equal(
    buildAgentProfileCatalogUrl({ query: "支持 助手", category: "support" }),
    "/api/ai/agent-profiles?query=%E6%94%AF%E6%8C%81+%E5%8A%A9%E6%89%8B&category=support",
  );
  assert.equal(
    buildAgentProfileDetailUrl("agent/with space"),
    "/api/ai/agent-profiles/agent%2Fwith%20space",
  );
});

test("projects a bounded Knowledge capability without retaining private source identities", () => {
  const projection = projectAgentProfilePublicProjection({
    agent_id: "agt_support",
    expected_revision: 7,
    name: "支持助手",
    description: "处理已授权的支持请求。",
    supported_input_types: ["text", "file"],
    avatar_ref: "builtin:assistant",
    category: "support",
    knowledge_capability: {
      enabled: true,
      source_count: 2,
      freshness_at: "2026-08-30T01:00:00Z",
      source_ids: ["private-source"],
    },
    knowledge_source_ids: ["private-source"],
  });

  assert.deepEqual(projection.knowledge_capability, {
    enabled: true,
    source_count: 2,
    freshness_at: "2026-08-30T01:00:00Z",
  });
  assert.equal("knowledge_source_ids" in projection, false);
  assert.equal(
    "source_ids" in (projection.knowledge_capability as Record<string, unknown>),
    false,
  );
});

test("loads only the safe public Agent Profile projection", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  const unicodeAvatarSeed = "\u{1F680}".repeat(128);
  globalThis.fetch = (async (input) => {
    calls.push(String(input));
    return new Response(
      JSON.stringify({
        agent_profiles: [
          {
            agent_id: "agt_support",
            expected_revision: 7,
            name: "支持助手",
            description: "处理已授权的支持请求。",
            supported_input_types: ["text", "file"],
            avatar_ref: "builtin:assistant",
            avatar_seed: unicodeAvatarSeed,
            category: "support",
            instructions: "PRIVATE_PROMPT",
            model_id: "private-model",
            mcp_tool_ids: ["private-mcp"],
            content_hash: "private-hash",
          },
        ],
      }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    const result = await agentProfileApi.listPublished({ category: "support" });
    assert.deepEqual(calls, ["/api/ai/agent-profiles?category=support"]);
    assert.deepEqual(result, {
      agent_profiles: [
        {
          ...defaultEnterpriseProjection,
          agent_id: "agt_support",
          expected_revision: 7,
          name: "支持助手",
          description: "处理已授权的支持请求。",
          avatar_ref: "builtin:assistant",
          avatar_seed: unicodeAvatarSeed,
          category: "support",
          knowledge_capability: {
            enabled: false,
            source_count: 0,
            freshness_at: null,
          },
        },
      ],
    });
    assert.equal("content_hash" in result.agent_profiles[0], false);
    assert.equal("mcp_tool_ids" in result.agent_profiles[0], false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("published authorization reads bypass cache and preserve transport failures", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{
    url: string;
    cache?: RequestCache;
    credentials?: RequestCredentials;
  }> = [];
  let responseKind: "catalog" | "detail" | "denied" | "aborted" = "catalog";
  const abortError = new DOMException("aborted", "AbortError");
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      cache: init?.cache,
      credentials: init?.credentials,
    });
    if (responseKind === "denied")
      return new Response(JSON.stringify({ detail: "agent_profile_not_authorized" }), {
        status: 403,
      });
    if (responseKind === "aborted") throw abortError;
    const profile = {
      agent_id: "agt_support",
      expected_revision: 7,
      name: "支持助手",
      description: "处理已授权的支持请求。",
      supported_input_types: ["text", "file"],
      avatar_ref: "builtin:assistant",
      category: "support",
    };
    return new Response(
      JSON.stringify(responseKind === "catalog" ? { agent_profiles: [profile] } : profile),
    );
  }) as typeof fetch;

  try {
    await agentProfileApi.listPublished();
    responseKind = "detail";
    await agentProfileApi.getPublished("agt_support");
    assert.deepEqual(calls, [
      {
        url: "/api/ai/agent-profiles",
        cache: "no-store",
        credentials: "include",
      },
      {
        url: "/api/ai/agent-profiles/agt_support",
        cache: "no-store",
        credentials: "include",
      },
    ]);

    responseKind = "denied";
    await assert.rejects(
      agentProfileApi.getPublished("agt_support"),
      (error: unknown) => (error as { status?: unknown }).status === 403,
    );
    responseKind = "aborted";
    await assert.rejects(agentProfileApi.listPublished(), (error: unknown) => error === abortError);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("lists only server-authorized conversations with their immutable safe identity", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; cache?: RequestCache }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({ url: String(input), cache: init?.cache });
    return new Response(
      JSON.stringify({
        sessions: [
          {
            session_id: "session-agent",
            workspace_id: "default",
            agent_id: "agt_support",
            title: "支持助手",
            created_at: "2026-07-29T00:00:00Z",
            updated_at: "2026-07-30T00:00:00Z",
            agent_conversation: {
              agent_id: "agt_support",
              revision: 7,
              name: "支持助手",
              description: "处理已授权的支持请求。",
              supported_input_types: ["text", "file"],
              avatar_ref: "builtin:assistant",
              category: "support",
              model_id: "private-model",
            },
          },
        ],
        next_cursor: "cursor-page-2",
      }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    const page = await agentProfileApi.listConversations(
      { agent_id: "agt_support", expected_revision: 7 },
      { limit: 20 },
    );
    assert.deepEqual(calls, [
      {
        url: "/api/ai/chat/sessions?agent_id=agt_support&revision=7&limit=20",
        cache: "no-store",
      },
    ]);
    assert.deepEqual(page, {
      sessions: [
        {
          session_id: "session-agent",
          workspace_id: "default",
          agent_id: "agt_support",
          title: "支持助手",
          purpose: "conversation",
          agent_conversation: {
            ...defaultEnterpriseProjection,
            agent_id: "agt_support",
            revision: 7,
            name: "支持助手",
            description: "处理已授权的支持请求。",
            avatar_ref: "builtin:assistant",
            category: "support",
          },
          created_at: "2026-07-29T00:00:00Z",
          updated_at: "2026-07-30T00:00:00Z",
        },
      ],
      next_cursor: "cursor-page-2",
    });
    assert.equal("model_id" in page.sessions[0]!.agent_conversation!, false);

    await agentProfileApi.listConversations(
      { agent_id: "agt_support", expected_revision: 7 },
      { cursor: "cursor+page/2=", limit: 50 },
    );
    assert.equal(
      calls[1]?.url,
      "/api/ai/chat/sessions?agent_id=agt_support&revision=7&limit=50&cursor=cursor%2Bpage%2F2%3D",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("creates a durable Agent Conversation with one caller-owned operation identity", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; method?: string; body?: string | null }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method,
      body: typeof init?.body === "string" ? init.body : null,
    });
    return new Response(
      JSON.stringify({
        session_id: "session-agent",
        workspace_id: "default",
        agent_id: "agt_support",
        title: "支持助手",
        agent_conversation: {
          agent_id: "agt_support",
          revision: 7,
          name: "支持助手",
          description: "处理已授权的支持请求。",
          supported_input_types: ["text", "file"],
          avatar_ref: "builtin:assistant",
          category: "support",
          model_id: "private-model",
          content_hash: "private-hash",
        },
        created_at: "2026-07-29T00:00:00Z",
        updated_at: "2026-07-29T00:00:00Z",
      }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    const response = await agentProfileApi.createConversation({
      agent_id: "agt_support",
      expected_revision: 7,
    }, "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4");
    assert.deepEqual(calls, [
      {
        url: "/api/ai/agent-conversations",
        method: "POST",
        body: JSON.stringify({
          selected_agent_profile: {
            agent_id: "agt_support",
            expected_revision: 7,
          },
          operation_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        }),
      },
    ]);
    assert.deepEqual(response.agent_conversation, {
      ...defaultEnterpriseProjection,
      agent_id: "agt_support",
      revision: 7,
      name: "支持助手",
      description: "处理已授权的支持请求。",
      avatar_ref: "builtin:assistant",
      category: "support",
    });
    assert.equal("content_hash" in response.agent_conversation!, false);
    assert.equal("model_id" in response.agent_conversation!, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("uses the current admin profile contract without retired file-type transport fields", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; method?: string; schema: string | null }> = [];
  const draftWriteBodies: Array<Record<string, unknown>> = [];
  const draft = {
    name: "Support assistant",
    description: "Approved support helper.",
    welcome_message: "",
    starter_prompts: [],
    capability_summary: "Approved support requests.",
    recommended_tasks: ["Review a request"],
    supported_input_types: ["text", "file"] as Array<"text" | "file">,
    expected_outputs: [],
    permissions_and_data_access_notice: "",
    instructions: "Keep answers concise.",
    selected_skill: { skill_id: "general-chat", expected_version: "version-a" },
    skill_set: [{ skill_id: "general-chat", expected_version: "version-a" }],
    mcp_tool_ids: [],
    avatar_ref: "builtin:agent" as const,
    avatar_seed: "support-assistant",
    avatar_asset_id: null,
    category: "support" as const,
    visibility: "tenant" as const,
    allowed_department_ids: [],
    allowed_roles: [],
    allowed_user_ids: [],
    expected_draft_revision: 0,
  };
  const adminProfile = {
    ...draft,
    agent_id: "agt_support",
    revision: 7,
    published_revision: null,
    status: "draft" as const,
    content_hash: "hash-a",
    created_at: null,
    published_at: null,
  };
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method,
      schema: new Headers(init?.headers).get("x-ai-agent-profile-schema"),
    });
    if (typeof init?.body === "string") {
      const body = JSON.parse(init.body) as Record<string, unknown>;
      if (typeof body.name === "string") draftWriteBodies.push(body);
    }
    const isList = !init?.method || init.method === "GET";
    return new Response(
      JSON.stringify(
        isList
          ? { agent_profiles: [adminProfile] }
          : { agent_profile: adminProfile, audit_id: "audit-a" },
      ),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    await agentProfileApi.listAdmin();
    await agentProfileApi.listHistory("agt_support");
    await agentProfileApi.saveDraft(draft);
    await agentProfileApi.saveDraft({ ...draft, expected_draft_revision: 7 }, "agt_support");
    await agentProfileApi.publish("agt_support", 7);
    await agentProfileApi.unpublish("agt_support", 7);

    assert.deepEqual(calls, [
      {
        url: "/api/ai/admin/agent-profiles",
        method: undefined,
        schema: null,
      },
      {
        url: "/api/ai/admin/agent-profiles/agt_support/history",
        method: undefined,
        schema: null,
      },
      {
        url: "/api/ai/admin/agent-profiles",
        method: "POST",
        schema: null,
      },
      {
        url: "/api/ai/admin/agent-profiles/agt_support",
        method: "PUT",
        schema: null,
      },
      {
        url: "/api/ai/admin/agent-profiles/agt_support/publish",
        method: "POST",
        schema: null,
      },
      {
        url: "/api/ai/admin/agent-profiles/agt_support/unpublish",
        method: "POST",
        schema: null,
      },
    ]);
    assert.equal("supported_file_types" in draft, false);
    assert.equal(
      draftWriteBodies.some((body) => "supported_file_types" in body),
      false,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects non-universal supported input projections instead of repairing them", () => {
  const publicProfile = {
    agent_id: "agt_support",
    expected_revision: 7,
    name: "支持助手",
    description: "处理已授权的支持请求。",
    supported_input_types: ["text", "file"],
    avatar_ref: "builtin:assistant",
    category: "support",
  };
  const conversationIdentity = {
    ...publicProfile,
    revision: publicProfile.expected_revision,
  };
  delete (conversationIdentity as { expected_revision?: number }).expected_revision;
  const adminProfile = {
    ...publicProfile,
    revision: 7,
    status: "draft",
    content_hash: "hash-a",
  };

  for (const invalid of [["text"], ["file", "text"], ["text", "file", "file"]]) {
    assert.throws(() =>
      projectAgentProfilePublicProjection({ ...publicProfile, supported_input_types: invalid }),
    );
    assert.throws(() =>
      projectAgentConversationIdentity({
        ...conversationIdentity,
        supported_input_types: invalid,
      }),
    );
    assert.throws(() =>
      validateAgentProfileAdminProjection({ ...adminProfile, supported_input_types: invalid }),
    );
  }
});


test("preserves typed 403 and stale revision failures from conversation admission", async () => {
  const originalFetch = globalThis.fetch;
  let status = 403;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        detail: status === 403 ? "agent_profile_not_authorized" : "agent_profile_not_available",
      }),
      { status },
    )) as typeof fetch;

  try {
    await assert.rejects(
      agentProfileApi.createConversation(
        { agent_id: "agt_support", expected_revision: 7 },
        "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      ),
      (error: unknown) =>
        typeof error === "object" &&
        error !== null &&
        (error as { status?: unknown }).status === 403,
    );
    status = 409;
    await assert.rejects(
      agentProfileApi.createConversation(
        { agent_id: "agt_support", expected_revision: 7 },
        "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      ),
      (error: unknown) =>
        typeof error === "object" &&
        error !== null &&
        (error as { status?: unknown }).status === 409,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
