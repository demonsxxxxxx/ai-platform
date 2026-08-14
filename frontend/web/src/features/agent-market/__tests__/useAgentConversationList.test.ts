import assert from "node:assert/strict";
import test from "node:test";

import { projectAgentConversationSidebarSession } from "../useAgentConversationList.ts";

test("projects only complete immutable Agent conversations into the dedicated rail", () => {
  assert.deepEqual(
    projectAgentConversationSidebarSession({
      session_id: "session-support",
      workspace_id: "default",
      agent_id: "agt_support",
      title: "报销问题",
      purpose: "conversation",
      created_at: "2026-08-04T01:00:00Z",
      updated_at: "2026-08-04T02:00:00Z",
      agent_conversation: {
        agent_id: "agt_support",
        revision: 7,
        name: "支持助手",
        description: "处理企业内部支持请求。",
        welcome_message: "欢迎使用支持助手。",
        starter_prompts: ["帮我处理支持请求"],
        capability_summary: "在授权范围内处理企业支持请求。",
        recommended_tasks: ["支持请求分流"],
        supported_input_types: ["text"],
        expected_outputs: ["处理建议"],
        permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
        avatar_ref: "builtin:assistant",
        category: "support",
        published_at: "2026-08-04T01:00:00Z",
      },
    }),
    {
      id: "session-support",
      agent_id: "agt_support",
      created_at: "2026-08-04T01:00:00Z",
      updated_at: "2026-08-04T02:00:00Z",
      is_active: true,
      name: "报销问题",
      metadata: {},
    },
  );
});

test("rejects generic or incomplete sessions instead of leaking them into the Agent rail", () => {
  assert.throws(
    () =>
      projectAgentConversationSidebarSession({
        session_id: "session-generic",
        workspace_id: "default",
        agent_id: "general-agent",
        title: "Generic",
        purpose: "conversation",
        agent_conversation: null,
      }),
    /invalid_agent_conversation_catalog/,
  );
});
