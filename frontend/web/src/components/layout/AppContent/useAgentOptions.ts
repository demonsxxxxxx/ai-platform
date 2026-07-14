import { useState, useEffect, useCallback, useRef } from "react";
import type { AgentOption } from "../../../types/agentOptions";

export const DEFAULT_THINKING_LEVEL_STORAGE_KEY = "defaultThinkingLevel";
const EMPTY_AGENT_OPTION_DEFINITIONS: Record<string, AgentOption> = {};

const THINKING_LEVEL_OPTION_DEFS = [
  { value: "off", label_key: "agentOptions.enableThinking.options.off" },
  { value: "low", label_key: "agentOptions.enableThinking.options.low" },
  { value: "medium", label_key: "agentOptions.enableThinking.options.medium" },
  { value: "high", label_key: "agentOptions.enableThinking.options.high" },
  { value: "max", label_key: "agentOptions.enableThinking.options.max" },
] as const;

function normalizeThinkingOptionValue(value: boolean | string | number) {
  if (value === true) return "medium";
  if (value === false) return "off";
  if (typeof value !== "string") return value;

  const normalized = value.trim().toLowerCase();
  if (["off", "low", "medium", "high", "max"].includes(normalized)) {
    return normalized;
  }
  if (["enabled", "enable", "on", "true"].includes(normalized)) {
    return "medium";
  }
  if (["disabled", "disable", "false", "none"].includes(normalized)) {
    return "off";
  }
  return value;
}

export function normalizeAgentOptionValues(
  values?: Record<string, boolean | string | number>,
): Record<string, boolean | string | number> | undefined {
  if (!values) return values;

  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => {
      if (key === "enable_thinking") {
        return [key, normalizeThinkingOptionValue(value)];
      }
      return [key, value];
    }),
  );
}

export function normalizeAgentOptions(
  options?: Record<string, AgentOption>,
): Record<string, AgentOption> | undefined {
  if (!options) return options;

  return Object.fromEntries(
    Object.entries(options).map(([key, option]) => {
      if (key !== "enable_thinking") {
        return [key, option];
      }

      return [
        key,
        {
          ...option,
          type: "string",
          default: normalizeThinkingOptionValue(option.default),
          label: option.label || "Thinking",
          label_key: option.label_key || "agentOptions.enableThinking.label",
          description:
            option.description ||
            "Control thinking intensity (supported models only)",
          description_key:
            option.description_key || "agentOptions.enableThinking.description",
          icon: option.icon || "Brain",
          options: option.options?.length
            ? option.options
            : [...THINKING_LEVEL_OPTION_DEFS],
        },
      ];
    }),
  );
}

type StorageLike = Pick<Storage, "getItem">;

function applyStoredAgentOptionDefaults(
  defaultValues: Record<string, boolean | string | number>,
  options?: Record<string, AgentOption>,
  storage?: StorageLike,
): Record<string, boolean | string | number> {
  if (!options?.enable_thinking) {
    return defaultValues;
  }

  const storedThinkingLevel = storage?.getItem(
    DEFAULT_THINKING_LEVEL_STORAGE_KEY,
  );
  if (!storedThinkingLevel) {
    return defaultValues;
  }

  return {
    ...defaultValues,
    enable_thinking: normalizeThinkingOptionValue(storedThinkingLevel),
  };
}

export function buildAgentOptionValues(
  options?: Record<string, AgentOption>,
  restoredOptions?: Record<string, boolean | string | number>,
  storage: StorageLike | undefined = typeof window !== "undefined"
    ? window.localStorage
    : undefined,
): Record<string, boolean | string | number> {
  const normalizedOptions = normalizeAgentOptions(options);
  let defaultValues: Record<string, boolean | string | number> = {};

  if (normalizedOptions) {
    Object.entries(normalizedOptions).forEach(([key, option]) => {
      defaultValues[key] = option.default;
    });
  }

  defaultValues = applyStoredAgentOptionDefaults(
    defaultValues,
    normalizedOptions,
    storage,
  );

  if (!restoredOptions) {
    return defaultValues;
  }

  return {
    ...defaultValues,
    ...normalizeAgentOptionValues(restoredOptions),
  };
}

export type AgentOptionSyncMode = "restore" | "reset" | "preserve" | "skip";

export function getAgentOptionSyncMode({
  optionsJson,
  previousOptionsJson,
  hasPendingRestoredOptions,
}: {
  optionsJson: string;
  previousOptionsJson: string;
  hasPendingRestoredOptions: boolean;
}): AgentOptionSyncMode {
  if (hasPendingRestoredOptions) {
    return "restore";
  }

  if (!previousOptionsJson) {
    return "reset";
  }

  if (optionsJson === previousOptionsJson) {
    return "skip";
  }

  return "preserve";
}

export function useAgentOptions(
  optionDefinitions: Record<string, AgentOption> = EMPTY_AGENT_OPTION_DEFINITIONS,
) {
  const [agentOptionValues, setAgentOptionValues] = useState<
    Record<string, boolean | string | number>
  >({});
  const pendingRestoredOptionsRef = useRef<Record<
    string,
    boolean | string | number
  > | null>(null);
  // Track option definitions so model metadata refreshes preserve user selections.
  const prevAgentOptionsJsonRef = useRef<string>("");

  const currentAgentOptions = normalizeAgentOptions(optionDefinitions) || {};

  useEffect(() => {
    const options = normalizeAgentOptions(optionDefinitions);
    const optionsJson = JSON.stringify(options);
    const syncMode = getAgentOptionSyncMode({
      optionsJson,
      previousOptionsJson: prevAgentOptionsJsonRef.current,
      hasPendingRestoredOptions: pendingRestoredOptionsRef.current !== null,
    });

    const prevJson = prevAgentOptionsJsonRef.current;
    prevAgentOptionsJsonRef.current = optionsJson;

    if (syncMode === "skip") {
      return;
    }

    if (syncMode === "restore" && pendingRestoredOptionsRef.current) {
      const nextValues = buildAgentOptionValues(
        options,
        pendingRestoredOptionsRef.current,
      );
      pendingRestoredOptionsRef.current = null;
      setAgentOptionValues(nextValues);
      return;
    }

    if (syncMode === "reset" || !prevJson) {
      setAgentOptionValues(buildAgentOptionValues(options));
      return;
    }

    setAgentOptionValues((prev) => {
      const rebuilt = buildAgentOptionValues(options);
      for (const key of Object.keys(prev)) {
        if (key in rebuilt) {
          (rebuilt as Record<string, boolean | string | number>)[key] =
            prev[key];
        }
      }
      return rebuilt;
    });
  }, [optionDefinitions]);

  useEffect(() => {
    const handleThinkingPreferenceUpdated = () => {
      const options = normalizeAgentOptions(optionDefinitions);
      // Only update the thinking level default; preserve all other user selections.
      setAgentOptionValues((prev) => ({
        ...buildAgentOptionValues(options),
        // Keep non-thinking user selections intact
        ...Object.fromEntries(
          Object.entries(prev).filter(([k]) => k !== "enable_thinking"),
        ),
      }));
    };

    window.addEventListener(
      "thinking-preference-updated",
      handleThinkingPreferenceUpdated,
    );
    return () => {
      window.removeEventListener(
        "thinking-preference-updated",
        handleThinkingPreferenceUpdated,
      );
    };
  }, [optionDefinitions]);

  const handleToggleAgentOption = useCallback(
    (key: string, value: boolean | string | number) => {
      setAgentOptionValues((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  // Reset to agent defaults (for new session)
  const resetAgentOptionDefaults = useCallback(() => {
    const options = normalizeAgentOptions(optionDefinitions);
    setAgentOptionValues(buildAgentOptionValues(options));
  }, [optionDefinitions]);

  // 从外部恢复配置
  const restoreAgentOptions = useCallback(
    (options: Record<string, boolean | string | number>) => {
      const normalizedOptions = normalizeAgentOptionValues(options) || {};
      pendingRestoredOptionsRef.current = normalizedOptions;
      setAgentOptionValues(normalizedOptions);
    },
    [],
  );

  return {
    agentOptionValues,
    currentAgentOptions,
    handleToggleAgentOption,
    restoreAgentOptions,
    resetAgentOptionDefaults,
  };
}
