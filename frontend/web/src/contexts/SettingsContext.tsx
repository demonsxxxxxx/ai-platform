import {
  createContext,
  useContext,
  ReactNode,
  useMemo,
  useState,
  useEffect,
  useCallback,
} from "react";
import { useAuth } from "../hooks/useAuth";
import { modelPublicApi } from "../services/api/modelPublic";
import type { SettingsResponse } from "../types";

export interface AvailableModel {
  id: string;
  value: string;
  provider?: string;
  label: string;
  description?: string;
}

interface SettingsContextValue {
  settings: SettingsResponse | null;
  enableSkills: boolean;
  enableMemory: boolean;
  isLoading: boolean;
  error: string | null;
  savingKeys: Set<string>;
  availableModels: AvailableModel[] | null;
  defaultModel: string;
  pinnedModelIds: string[];
  togglePinnedModel: (modelId: string) => void;
  updateSetting: (
    key: string,
    value: string | number | boolean | object,
  ) => Promise<boolean>;
  resetSetting: (key: string) => Promise<boolean>;
  resetAllSettings: () => Promise<boolean>;
  clearError: () => void;
  exportSettings: () => void;
  importSettings: (
    file: File,
  ) => Promise<{ success: boolean; updatedCount: number; errors: string[] }>;
}

const SettingsContext = createContext<SettingsContextValue | undefined>(
  undefined,
);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const savingKeys = useMemo(() => new Set<string>(), []);

  // 从 DB 的 model_configs 读取可用模型
  const [dbModels, setDbModels] = useState<AvailableModel[] | null>(null);
  const [adminDefaultModelId, setAdminDefaultModelId] = useState<string>("");

  // 置顶模型 ID
  const [pinnedModelIds, setPinnedModelIds] = useState<string[]>([]);

  const fetchModels = useCallback(() => {
    modelPublicApi
      .listAvailable()
      .then((data) => {
        setAdminDefaultModelId(data.default_model_id || "");
        if (data.models && data.models.length > 0) {
          setDbModels(
            data.models.map((m) => ({
              id: m.id || "",
              value: m.value,
              provider: m.provider,
              label: m.label,
              description: m.description,
            })),
          );
        } else {
          setDbModels(null);
        }
      })
      .catch(() => {
        setAdminDefaultModelId("");
        setDbModels(null);
      });
  }, []);

  const fetchPinnedModels = useCallback(() => {
    modelPublicApi
      .getPinnedModelIds()
      .then(setPinnedModelIds)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      fetchModels();
      fetchPinnedModels();
    }
  }, [isAuthenticated, fetchModels, fetchPinnedModels]);

  const togglePinnedModel = useCallback((modelId: string) => {
    setPinnedModelIds((prev) => {
      const next = prev.includes(modelId)
        ? prev.filter((id) => id !== modelId)
        : [...prev, modelId];
      modelPublicApi.updatePinnedModelIds(next).catch(() => {});
      return next;
    });
  }, []);

  // Auto-clean orphaned pinned IDs (models that were deleted)
  const cleanedPinnedIds = useMemo(() => {
    if (!dbModels || pinnedModelIds.length === 0) return pinnedModelIds;
    const validIds = new Set(dbModels.map((m) => m.id));
    const cleaned = pinnedModelIds.filter((id) => validIds.has(id));
    return cleaned;
  }, [dbModels, pinnedModelIds]);

  useEffect(() => {
    if (cleanedPinnedIds.length === pinnedModelIds.length) return;
    setPinnedModelIds(cleanedPinnedIds);
    modelPublicApi.updatePinnedModelIds(cleanedPinnedIds).catch(() => {});
  }, [cleanedPinnedIds, pinnedModelIds.length]);

  // 从 DB 读取模型
  const availableModels = useMemo(() => {
    return dbModels;
  }, [dbModels]);

  const defaultModel = useMemo(() => {
    if (!availableModels || availableModels.length === 0) {
      return "";
    }
    return (
      availableModels.find((model) => model.id === adminDefaultModelId)
        ?.value || availableModels[0].value
    );
  }, [adminDefaultModelId, availableModels]);

  const unsupportedSettingsMutation = useCallback(async () => {
    setError("Settings management requires the phase 2 admin projection.");
    return false;
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const value: SettingsContextValue = {
    settings: null,
    enableSkills: true,
    enableMemory: true,
    availableModels,
    defaultModel,
    pinnedModelIds: cleanedPinnedIds,
    togglePinnedModel,
    isLoading: false,
    error,
    savingKeys,
    updateSetting: unsupportedSettingsMutation,
    resetSetting: unsupportedSettingsMutation,
    resetAllSettings: unsupportedSettingsMutation,
    clearError,
    exportSettings: () => {},
    importSettings: async () => ({
      success: false,
      updatedCount: 0,
      errors: ["Settings import requires the phase 2 admin projection."],
    }),
  };

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
}

// Fast refresh only works when a file only exports components.
// Use a new file to share constants or functions between components
// eslint-disable-next-line react-refresh/only-export-components
export function useSettingsContext() {
  const context = useContext(SettingsContext);
  if (context === undefined) {
    throw new Error(
      "useSettingsContext must be used within a SettingsProvider",
    );
  }
  return context;
}
