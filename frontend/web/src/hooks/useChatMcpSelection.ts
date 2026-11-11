import { useCallback, useEffect, useRef, useState } from "react";
import type { ToolState } from "../types";
import { hasValidatedChatMcpCatalog, type ChatMcpCatalogStatus } from "./useTools";

interface ChatMcpSelectionOptions {
  enabled: boolean;
  identityScope: string;
  selectionScope: string;
  sessionId: string | null;
  tools: readonly ToolState[];
  catalogStatus: ChatMcpCatalogStatus;
  selectedToolIds: string[] | undefined;
  serverSelectedToolIds: string[] | undefined;
  setSelectedToolIds: (toolIds: string[] | undefined) => void;
}

/** Initialize a Chat draft from server defaults without overriding explicit selection. */
export function useChatMcpSelection({
  enabled, identityScope, selectionScope, sessionId, tools, catalogStatus,
  selectedToolIds, serverSelectedToolIds, setSelectedToolIds,
}: ChatMcpSelectionOptions) {
  const [resetVersion, setResetVersion] = useState(0);
  const owner = JSON.stringify([identityScope, selectionScope, resetVersion]);
  const initializedRef = useRef<string | null>(null);
  const restoredRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !hasValidatedChatMcpCatalog(catalogStatus)) return;
    const restoreKey = sessionId && serverSelectedToolIds !== undefined
      ? JSON.stringify([identityScope, sessionId, serverSelectedToolIds]) : null;
    const restore = restoreKey !== null && restoredRef.current !== restoreKey;
    if (restore) restoredRef.current = restoreKey;
    const initialize = sessionId === null && initializedRef.current !== owner;
    if (initialize) initializedRef.current = owner;
    const next = restore ? serverSelectedToolIds : initialize && selectedToolIds === undefined
      ? tools.filter((tool) => tool.default_enabled && !tool.system_disabled).map((tool) => tool.name)
      : undefined;
    if (next === undefined || (selectedToolIds !== undefined && next.length === selectedToolIds.length &&
      next.every((toolId, index) => selectedToolIds[index] === toolId))) return;
    setSelectedToolIds(next);
  }, [enabled, identityScope, owner, sessionId, tools, catalogStatus, selectedToolIds, serverSelectedToolIds, setSelectedToolIds]);

  const markToolsEdited = useCallback((_toolIds: readonly string[]) => {
    initializedRef.current = owner;
  }, [owner]);
  const resetDefaults = useCallback(() => setResetVersion((version) => version + 1), []);
  return { markToolsEdited, resetDefaults };
}
