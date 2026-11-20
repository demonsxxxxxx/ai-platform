import { useCallback, useEffect, useRef, useState } from "react";
import { mcpApi } from "../../services/api/mcp";
import type { MCPPluginResponse } from "../../types/mcp";

/** User-facing registry reads are independent of live handshake results. */
export function usePluginCatalog(scope: string, enabled: boolean) {
  const generation = useRef(0);
  const currentScope = useRef(scope);
  const mounted = useRef(false);
  const active = useRef(enabled);
  currentScope.current = scope;
  active.current = enabled;
  const [state, setState] = useState({ scope, plugins: [] as MCPPluginResponse[], loading: true, failed: false });
  const refresh = useCallback(async () => {
    if (!mounted.current || !enabled || !active.current || currentScope.current !== scope) return;
    const request = ++generation.current;
    const current = () => currentScope.current === scope && request === generation.current;
    setState({ scope, plugins: [], loading: true, failed: false });
    try {
      const plugins: MCPPluginResponse[] = [];
      let expected: number | undefined;
      for (let page = 0; page < 1000; page++) {
        const response = await mcpApi.getPlugins(plugins.length);
        if (!current()) return;
        if (!Number.isSafeInteger(response.total) || response.total < 0 || response.skip !== plugins.length ||
          response.limit !== 200 || !Array.isArray(response.servers) || response.servers.length > 200 ||
          (expected !== undefined && expected !== response.total)) throw new Error("plugin_directory_invalid");
        expected = response.total;
        for (const plugin of response.servers) {
          if (!plugin || typeof plugin.name !== "string" || !plugin.name ||
            typeof plugin.display_name !== "string" || typeof plugin.description !== "string" ||
            plugin.installation !== "system_preinstalled" || !plugin.enabled ||
            !["none", "profile_drive"].includes(plugin.user_authorization) ||
            plugins.some(row => row.name === plugin.name)) throw new Error("plugin_directory_invalid");
          plugins.push(plugin);
        }
        if (plugins.length === expected) {
          setState({ scope, plugins, loading: false, failed: false });
          return;
        }
        if (!response.servers.length || plugins.length > expected) throw new Error("plugin_directory_incomplete");
      }
      throw new Error("plugin_directory_incomplete");
    } catch {
      if (current()) setState({ scope, plugins: [], loading: false, failed: true });
    }
  }, [scope, enabled]);
  useEffect(() => {
    const requests = generation;
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; requests.current++; };
  }, [refresh]);
  const saveGatewayPreference = async (name: string, enabled: boolean) => {
    const saved = await mcpApi.updateGatewayConversationPreference(name, enabled);
    if (mounted.current && active.current && currentScope.current === scope) {
      setState(previous => previous.scope !== scope ? previous : {
        ...previous, plugins: previous.plugins.map(plugin => plugin.name === name
          ? { ...plugin, use_in_conversation: saved.use_in_conversation } : plugin),
      });
    }
  };
  const visible = enabled && state.scope === scope ? state : { scope, plugins: [], loading: enabled, failed: false };
  return { ...visible, refresh, saveGatewayPreference };
}
