import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../components/common/__tests__/selectTestDom";
import { mcpApi } from "../../services/api/mcp";

const dom = installSelectTestDom();
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useMcpAdminCatalog } = await import("../useMcpAdminCatalog");

test("admin catalog isolates identities, stale responses and missing tools", async () => {
  const original = mcpApi.getCatalogTools;
  const pending: Array<(value: unknown) => void> = [];
  mcpApi.getCatalogTools = () => new Promise(resolve => pending.push(resolve));
  const container = dom.document.createElement("div");
  const root = createRoot(container);
  let snapshot: ReturnType<typeof useMcpAdminCatalog> | undefined;
  function Harness({ scope, enabled = true }: { scope: string; enabled?: boolean }) {
    snapshot = useMcpAdminCatalog(scope, enabled);
    return createElement("div", null, snapshot.tools.map(tool => tool.name).join(","));
  }
  const response = (name: string, available = true) => ({ tools: [{
    tool_id: `gateway::${name}`, label: name, description: "Synthetic", category: "mcp", server: "gateway", cached: false, available,
  }], count: 1, unavailable: [] });
  try {
    await act(async () => { root.render(createElement(Harness, { scope: "admin-a" })); });
    await act(async () => { root.render(createElement(Harness, { scope: "admin-b" })); });
    assert.equal(pending.length, 2);
    await act(async () => { pending[0](response("stale")); });
    assert.equal(snapshot!.tools.length, 0);
    await act(async () => { pending[1](response("missing", false)); });
    assert.equal(snapshot!.tools[0].system_disabled, true);
    assert.equal(snapshot!.tools[0].name, "gateway::missing");
    await act(async () => { root.render(createElement(Harness, { scope: "ordinary", enabled: false })); });
    assert.equal(pending.length, 2);
    assert.equal(snapshot!.tools.length, 0);
    await act(async () => { root.render(createElement(Harness, { scope: "admin-c" })); });
    await act(async () => { pending[2]({ tools: [], count: 0, unavailable: [{ label: "gateway", reason: "discovery_failed" }] }); });
    assert.equal(snapshot!.error, "mcp_catalog_unavailable");
    assert.deepEqual(snapshot!.unavailableServers, ["gateway"]);
  } finally {
    await act(async () => root.unmount());
    mcpApi.getCatalogTools = original;
  }
});
