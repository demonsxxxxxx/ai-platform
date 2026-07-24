/** Session-scoped Chat configuration. MCP selection is server-authoritative. */

import { useState, useCallback, useEffect, useRef } from "react";
import type { SessionConfig } from "./useAgent/types";
import { normalizeAgentOptionValues } from "../components/layout/AppContent/useAgentOptions";
import {
  readSessionConfigStorage,
  writeSessionConfigStorage,
} from "../utils/sessionConfigStorage";

export interface SessionConfigState {
  disabledSkills: string[];
  selectedMcpToolIds: string[] | undefined;
  agentOptions: Record<string, boolean | string | number>;
}

export interface UseSessionConfigOptions {
  getDefaultDisabledSkills?: () => string[];
  getDefaultAgentOptions: () => Record<string, boolean | string | number>;
}

function loadPersistedDisabledSkills(): string[] | null {
  try {
    const raw = readSessionConfigStorage();
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed.disabledSkills) ? parsed.disabledSkills : null;
  } catch {
    return null;
  }
}

function persistDisabledSkills(disabledSkills: string[]) {
  try {
    writeSessionConfigStorage(JSON.stringify({ disabledSkills }));
  } catch {
    /* localStorage is an optional preference seam */
  }
}

export interface UseSessionConfigReturn {
  config: SessionConfigState;
  toggleSkill: (skillName: string) => void;
  toggleMcpTool: (toolId: string) => void;
  setAgentOption: (key: string, value: boolean | string | number) => void;
  setDisabledSkills: (skills: string[]) => void;
  setSelectedMcpToolIds: (toolIds: string[] | undefined) => void;
  setAgentOptions: (options: Record<string, boolean | string | number>) => void;
  resetToDefaults: () => void;
  restoreConfig: (config: SessionConfig) => void;
  isSkillEnabled: (skillName: string) => boolean;
  isMcpToolEnabled: (toolId: string) => boolean;
}

export function useSessionConfig(
  options: UseSessionConfigOptions,
): UseSessionConfigReturn {
  const defaultAgentOptionsRef = useRef<Record<string, boolean | string | number>>({});
  defaultAgentOptionsRef.current = options.getDefaultAgentOptions();

  const [config, setConfig] = useState<SessionConfigState>(() => ({
    disabledSkills:
      loadPersistedDisabledSkills() ?? options.getDefaultDisabledSkills?.() ?? [],
    selectedMcpToolIds: undefined,
    agentOptions: options.getDefaultAgentOptions(),
  }));

  const initializedRef = useRef(false);
  if (!initializedRef.current) {
    initializedRef.current = true;
    setConfig((previous) => ({
      ...previous,
      agentOptions: defaultAgentOptionsRef.current,
    }));
  }

  useEffect(() => {
    persistDisabledSkills(config.disabledSkills);
  }, [config.disabledSkills]);

  const toggleSkill = useCallback((skillName: string) => {
    setConfig((previous) => {
      const disabled = new Set(previous.disabledSkills);
      if (disabled.has(skillName)) {
        disabled.delete(skillName);
      } else {
        disabled.add(skillName);
      }
      return { ...previous, disabledSkills: Array.from(disabled) };
    });
  }, []);

  const toggleMcpTool = useCallback((toolId: string) => {
    setConfig((previous) => {
      const selected = new Set(previous.selectedMcpToolIds ?? []);
      if (selected.has(toolId)) {
        selected.delete(toolId);
      } else {
        selected.add(toolId);
      }
      return { ...previous, selectedMcpToolIds: Array.from(selected) };
    });
  }, []);

  const setAgentOption = useCallback(
    (key: string, value: boolean | string | number) => {
      setConfig((previous) => ({
        ...previous,
        agentOptions: { ...previous.agentOptions, [key]: value },
      }));
    },
    [],
  );

  const setDisabledSkills = useCallback((disabledSkills: string[]) => {
    setConfig((previous) => ({ ...previous, disabledSkills }));
  }, []);

  const setSelectedMcpToolIds = useCallback((selectedMcpToolIds: string[] | undefined) => {
    setConfig((previous) => ({ ...previous, selectedMcpToolIds }));
  }, []);

  const setAgentOptions = useCallback((agentOptions: Record<string, boolean | string | number>) => {
    setConfig((previous) => ({ ...previous, agentOptions }));
  }, []);

  const resetToDefaults = useCallback(() => {
    const disabledSkills = options.getDefaultDisabledSkills?.() || [];
    setConfig({
      disabledSkills,
      selectedMcpToolIds: undefined,
      agentOptions: defaultAgentOptionsRef.current,
    });
    persistDisabledSkills(disabledSkills);
  }, [options]);

  const restoreConfig = useCallback((sessionConfig: SessionConfig) => {
    setConfig({
      disabledSkills: sessionConfig.disabled_skills || [],
      selectedMcpToolIds: (
        sessionConfig as SessionConfig & { selected_mcp_tool_ids?: string[] }
      ).selected_mcp_tool_ids,
      agentOptions:
        normalizeAgentOptionValues(sessionConfig.agent_options) ||
        defaultAgentOptionsRef.current,
    });
  }, []);

  const isSkillEnabled = useCallback(
    (skillName: string) => !config.disabledSkills.includes(skillName),
    [config.disabledSkills],
  );
  const isMcpToolEnabled = useCallback(
    (toolId: string) => config.selectedMcpToolIds?.includes(toolId) === true,
    [config.selectedMcpToolIds],
  );

  return {
    config,
    toggleSkill,
    toggleMcpTool,
    setAgentOption,
    setDisabledSkills,
    setSelectedMcpToolIds,
    setAgentOptions,
    resetToDefaults,
    restoreConfig,
    isSkillEnabled,
    isMcpToolEnabled,
  };
}
