import assert from "node:assert/strict";
import { register } from "node:module";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";
import type { SessionConfig, SubmissionOutcome, UseAgentReturn } from "../../../hooks/useAgent/types";
import type { AgentConversationSessionProjection, AgentProfilePublicProjection } from "../../../types/agentProfile";
import type { MessageAttachment } from "../../../types";

register(new URL("./frontendAssetLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));
const dom = installSelectTestDom();
after(() => dom.close());
const React = await import("react");
const { createRoot } = await import("react-dom/client");
const { useAgentFirstSend } = await import("../useAgentFirstSend");
const { agentProfileApi } = await import("../../../services/api/agentProfile");

const profile: AgentProfilePublicProjection = {
  agent_id: "agt_synthetic", name: "合成专家", description: "合成任务",
  starter_prompts: ["合成任务"], avatar_ref: "builtin:assistant", avatar_seed: "synthetic",
  market_tags: [], is_favorite: false, published_at: "2026-10-01T00:00:00Z",
};
const operationKey = `agent-conversation-operation:${profile.agent_id}`;
const created: AgentConversationSessionProjection = {
  session_id: "session-synthetic", agent_id: profile.agent_id, workspace_id: "default",
  title: "合成任务", purpose: "conversation", agent_conversation: { ...profile, revision: 1 },
  created_at: profile.published_at, updated_at: profile.published_at,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
async function mount(options: {
  loadHistory?: UseAgentReturn["loadHistory"];
  sendMessage?: UseAgentReturn["sendMessage"];
} = {}) {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  let current!: ReturnType<typeof useAgentFirstSend>;
  function Harness() {
    current = useAgentFirstSend({
      agentWorkspace: profile, startProfile: profile, readOnly: false,
      loadHistory: options.loadHistory ?? (async () => ({})),
      sendMessage: options.sendMessage ?? (async () => ({ status: "accepted" })),
      navigate: () => {},
    });
    return <span data-handoff={current.draftHandoffKey ?? ""}>{current.error}</span>;
  }
  await React.act(async () => root.render(<Harness />));
  let closed = false;
  return {
    get current() { return current; },
    container,
    async close() {
      if (!closed) await React.act(async () => root.unmount());
      closed = true;
      container.remove();
    },
  };
}

// These interleave real hook updates around awaits, not only the pure flight helpers.
test("mounted first send binds once, keeps draft/attachments through canonical identity, and clears only accepted operation", async () => {
  sessionStorage.clear();
  const original = agentProfileApi.createConversation;
  const creation = deferred<AgentConversationSessionProjection>();
  const binding = deferred<SessionConfig | null>();
  const submission = deferred<SubmissionOutcome>();
  let creates = 0, binds = 0;
  const sends: Parameters<UseAgentReturn["sendMessage"]>[] = [];
  agentProfileApi.createConversation = async () => { creates += 1; return creation.promise; };
  const view = await mount({
    loadHistory: async () => { binds += 1; return binding.promise; },
    sendMessage: async (...args) => { sends.push(args); return submission.promise; },
  });
  const attachments: MessageAttachment[] = [{ id: "file-synthetic", key: "file-synthetic", name: "合成.txt", type: "document", mimeType: "text/plain", size: 12 }];
  try {
    let first!: Promise<SubmissionOutcome>, duplicate!: Promise<SubmissionOutcome>;
    await React.act(async () => {
      first = view.current.sendFirstMessage("合成任务", { enable_thinking: true }, attachments);
      duplicate = view.current.sendFirstMessage("合成任务", { enable_thinking: true }, attachments);
    });
    const operationId = sessionStorage.getItem(operationKey);
    assert.ok(operationId);
    assert.equal(creates, 1);
    await React.act(async () => { creation.resolve(created); });
    assert.equal(binds, 1);
    assert.equal(sends.length, 0, "must await successful history binding");
    assert.equal(view.current.draftHandoffKey, created.session_id);
    await React.act(async () => { view.current.resetForIdentity(`${profile.agent_id}:${created.session_id}`); });
    assert.equal(view.current.draftHandoffKey, created.session_id, "canonical route keeps first-send draft handoff");
    await React.act(async () => { binding.resolve({}); });
    assert.equal(sends.length, 1);
    assert.deepEqual(sends[0], ["合成任务", { enable_thinking: true }, attachments, null]);
    assert.equal(sessionStorage.getItem(operationKey), operationId, "pending receipt cannot clear retry identity");
    await React.act(async () => { submission.resolve({ status: "accepted" }); await Promise.all([first, duplicate]); });
    assert.equal(sessionStorage.getItem(operationKey), null);
    assert.deepEqual(await first, { status: "accepted" });
    assert.deepEqual(await duplicate, { status: "accepted" });
  } finally { await view.close(); agentProfileApi.createConversation = original; }
});

test("mounted first send keeps failed receipt operation and retries the same bound conversation", async () => {
  sessionStorage.clear();
  const original = agentProfileApi.createConversation;
  let creates = 0, binds = 0, sends = 0;
  agentProfileApi.createConversation = async () => { creates += 1; return created; };
  const view = await mount({
    loadHistory: async () => { binds += 1; return {}; },
    sendMessage: async () => ({ status: ++sends === 1 ? "failed" : "accepted" }),
  });
  try {
    await React.act(async () => { assert.deepEqual(await view.current.sendFirstMessage("合成任务"), { status: "failed" }); });
    assert.ok(sessionStorage.getItem(operationKey));
    await React.act(async () => { assert.deepEqual(await view.current.sendFirstMessage("合成任务"), { status: "accepted" }); });
    assert.equal(creates, 1);
    assert.equal(binds, 1);
    assert.equal(sends, 2);
    assert.equal(sessionStorage.getItem(operationKey), null);
  } finally { await view.close(); agentProfileApi.createConversation = original; }
});

for (const invalidation of ["new-task", "identity", "unmount"] as const) {
  test(`mounted ${invalidation} fences a late creation before it can bind, hand off or submit`, async () => {
    sessionStorage.clear();
    const original = agentProfileApi.createConversation;
    const creation = deferred<AgentConversationSessionProjection>();
    let binds = 0, sends = 0;
    agentProfileApi.createConversation = async () => creation.promise;
    const view = await mount({
      loadHistory: async () => { binds += 1; return {}; },
      sendMessage: async () => { sends += 1; return { status: "accepted" }; },
    });
    try {
      let pending!: Promise<SubmissionOutcome>;
      await React.act(async () => { pending = view.current.sendFirstMessage("合成任务"); });
      if (invalidation === "unmount") await view.close();
      else await React.act(async () => {
        if (invalidation === "new-task") view.current.resetForNewTask();
        else view.current.resetForIdentity("another-agent:another-session");
      });
      await React.act(async () => { creation.resolve(created); assert.deepEqual(await pending, { status: "failed" }); });
      assert.equal(binds, 0);
      assert.equal(sends, 0);
      assert.equal(view.current.draftHandoffKey, null);
      assert.equal(view.current.error, null);
      if (invalidation === "new-task") assert.equal(sessionStorage.getItem(operationKey), null);
    } finally { await view.close(); agentProfileApi.createConversation = original; }
  });
}

test("identity change while binding is pending prevents submission after binding resolves", async () => {
  sessionStorage.clear();
  const original = agentProfileApi.createConversation;
  const binding = deferred<SessionConfig | null>();
  let sends = 0;
  agentProfileApi.createConversation = async () => created;
  const view = await mount({ loadHistory: () => binding.promise, sendMessage: async () => { sends += 1; return { status: "accepted" }; } });
  try {
    let pending!: Promise<SubmissionOutcome>;
    await React.act(async () => { pending = view.current.sendFirstMessage("合成任务"); });
    assert.equal(view.current.draftHandoffKey, created.session_id);
    await React.act(async () => view.current.resetForIdentity("another-agent:another-session"));
    await React.act(async () => { binding.resolve({}); assert.deepEqual(await pending, { status: "failed" }); });
    assert.equal(sends, 0);
    assert.equal(view.current.draftHandoffKey, null);
  } finally { await view.close(); agentProfileApi.createConversation = original; }
});
