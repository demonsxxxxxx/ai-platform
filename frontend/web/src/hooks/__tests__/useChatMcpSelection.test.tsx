import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { after } from "node:test";
import type { ToolState } from "../../types/tool.ts";
import { useChatMcpSelection } from "../useChatMcpSelection.ts";
import { useSessionConfig } from "../useSessionConfig.ts";
import { installTestDom } from "../useAgent/__tests__/testDom.ts";

const dom = installTestDom();
after(() => dom.close());
const React = await import("react");
const { createRoot } = await import("react-dom/client");
const base: ToolState = { name: "gateway::base", server: "gateway", description: "Synthetic", category: "mcp", parameters: [], enabled: false, default_enabled: true };
const other = { ...base, name: "gateway::other", default_enabled: false };
type Options = Omit<Parameters<typeof useChatMcpSelection>[0], "selectedToolIds" | "setSelectedToolIds">;
async function mount(overrides: Partial<Options> = {}) {
  const root = createRoot(document.createElement("div"));
  let options: Options = { enabled: true, identityScope: "tenant:user", selectionScope: "draft", sessionId: null, catalogStatus: "ready", tools: [base, other], serverSelectedToolIds: undefined, ...overrides };
  let snapshot: { config: ReturnType<typeof useSessionConfig>; selection: ReturnType<typeof useChatMcpSelection> } | null = null;
  function Probe() {
    const config = useSessionConfig({ getDefaultAgentOptions: () => ({}) });
    const selection = useChatMcpSelection({ ...options, selectedToolIds: config.config.selectedMcpToolIds, setSelectedToolIds: config.setSelectedMcpToolIds });
    snapshot = { config, selection };
    return null;
  }
  const current = () => { if (!snapshot) throw new Error("Probe missing"); return snapshot; };
  await React.act(async () => { root.render(<Probe />); });
  return { current, async update(next: Partial<Options>) { options = { ...options, ...next }; await React.act(async () => { root.render(<Probe />); }); }, async close() { await React.act(async () => { root.unmount(); }); } };
}

test("new Chat selects backend defaults only, independent of server display names", async () => {
  const probe = await mount({ tools: [base, other, { ...base, name: "gateway::disabled", system_disabled: true }] });
  try { assert.deepEqual(probe.current().config.config.selectedMcpToolIds, [base.name]); }
  finally { await probe.close(); }
});

test("new Chat without defaults preserves omission until the user explicitly clears tools", async () => {
  for (const tools of [[], [other]]) {
    const probe = await mount({ tools, catalogStatus: tools.length ? "ready" : "empty" });
    try {
      assert.equal(probe.current().config.config.selectedMcpToolIds, undefined);
      await React.act(async () => { probe.current().selection.markToolsEdited([]); probe.current().config.setSelectedMcpToolIds([]); });
      await probe.update({ tools: [base], catalogStatus: "ready" });
      assert.deepEqual(probe.current().config.config.selectedMcpToolIds, []);
    } finally { await probe.close(); }
  }
});

test("restored explicit selections including empty override defaults", async () => {
  for (const selected of [[], [other.name]]) {
    const probe = await mount({ sessionId: "session-a", selectionScope: "session-a", serverSelectedToolIds: selected });
    try { assert.deepEqual(probe.current().config.config.selectedMcpToolIds, selected); }
    finally { await probe.close(); }
  }
});

test("existing sessions without a server selection preserve omission across changed defaults", async () => {
  const probe = await mount({ sessionId: "existing-session", selectionScope: "existing-session" });
  try {
    assert.equal(probe.current().config.config.selectedMcpToolIds, undefined);
    await probe.update({ catalogStatus: "loading", tools: [] });
    await probe.update({ catalogStatus: "ready", tools: [{ ...base, default_enabled: false }, { ...other, default_enabled: true }] });
    assert.equal(probe.current().config.config.selectedMcpToolIds, undefined);
    await probe.update({ sessionId: "another-session", selectionScope: "another-session" });
    assert.equal(probe.current().config.config.selectedMcpToolIds, undefined);
  } finally { await probe.close(); }
});

test("manual deselection survives catalog refresh and first-send URL canonicalization", async () => {
  const probe = await mount();
  try {
    await React.act(async () => { probe.current().selection.markToolsEdited([base.name]); probe.current().config.setSelectedMcpToolIds([]); });
    await probe.update({ catalogStatus: "loading", tools: [] });
    await probe.update({ catalogStatus: "ready", tools: [base, other] });
    await probe.update({ sessionId: "created-session", serverSelectedToolIds: [] });
    assert.deepEqual(probe.current().config.config.selectedMcpToolIds, []);
    await React.act(async () => { probe.current().config.resetToDefaults(); probe.current().selection.resetDefaults(); });
    await probe.update({ sessionId: null, serverSelectedToolIds: undefined });
    assert.deepEqual(probe.current().config.config.selectedMcpToolIds, [base.name]);
  } finally { await probe.close(); }
});

test("loading and locked Agent controls never initialize generic defaults", async () => {
  const probe = await mount({ enabled: false });
  try {
    assert.equal(probe.current().config.config.selectedMcpToolIds, undefined);
    await probe.update({ enabled: true, catalogStatus: "loading", tools: [] });
    assert.equal(probe.current().config.config.selectedMcpToolIds, undefined);
    await probe.update({ catalogStatus: "degraded", tools: [base] });
    assert.deepEqual(probe.current().config.config.selectedMcpToolIds, [base.name]);
  } finally { await probe.close(); }
});

test("manual choice during loading is not overwritten when defaults arrive", async () => {
  const probe = await mount({ catalogStatus: "loading", tools: [] });
  try {
    await React.act(async () => { probe.current().selection.markToolsEdited([]); probe.current().config.setSelectedMcpToolIds([]); });
    await probe.update({ catalogStatus: "ready", tools: [base] });
    assert.deepEqual(probe.current().config.config.selectedMcpToolIds, []);
  } finally { await probe.close(); }
});

test("retired drive connection and name special cases remain absent", () => {
  const source = readFileSync(new URL("../useChatMcpSelection.ts", import.meta.url), "utf8");
  assert.doesNotMatch(source, /ProfileDrive|profileDrive|CONNECTION_CHANGED|upstream_server/);
  const chat = readFileSync(new URL("../../components/layout/AppContent/ChatAppContent.tsx", import.meta.url), "utf8");
  assert.match(chat, /useChatMcpSelection\(\{\s*enabled: !agentConversationControlsLocked/);
  assert.match(chat, /setSelectedToolIds: setSelectedMcpToolIds/);
  assert.doesNotMatch(chat, /restoredMcpSelectionRef/);
});
