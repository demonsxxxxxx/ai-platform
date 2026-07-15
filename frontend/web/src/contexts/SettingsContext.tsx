import {
  createContext,
  useContext,
  ReactNode,
  useMemo,
  useState,
  useEffect,
  useCallback,
  useRef,
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
const EMPTY_PINNED_MODEL_IDS: string[] = [];

interface SubjectSettingsSnapshot {
  subjectKey: string;
  dbModels: AvailableModel[] | null;
  adminDefaultModelId: string;
  pinnedModelIds: string[];
  isLoading: boolean;
}

interface SubjectRequestOwner {
  subjectKey: string;
  generation: number;
  abortController: AbortController;
}

function emptySubjectSettings(subjectKey: string): SubjectSettingsSnapshot {
  return {
    subjectKey,
    dbModels: null,
    adminDefaultModelId: "",
    pinnedModelIds: [],
    isLoading: true,
  };
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, user } = useAuth();
  const authSubjectKey =
    isAuthenticated && user?.tenant_id
      ? `${user.tenant_id}\u0000${user.id}`
      : null;
  const authSubjectKeyRef = useRef(authSubjectKey);
  authSubjectKeyRef.current = authSubjectKey;
  const subjectGenerationRef = useRef(0);
  const subjectOwnerRef = useRef<SubjectRequestOwner | null>(null);
  const [subjectSettings, setSubjectSettings] =
    useState<SubjectSettingsSnapshot | null>(null);
  const subjectSettingsRef = useRef(subjectSettings);
  subjectSettingsRef.current = subjectSettings;
  const [subjectError, setSubjectError] = useState<{
    subjectKey: string | null;
    message: string;
  } | null>(null);
  const savingKeys = useMemo(() => new Set<string>(), []);

  const isCurrentSubjectOwner = useCallback((owner: SubjectRequestOwner) => {
    return (
      authSubjectKeyRef.current === owner.subjectKey &&
      subjectGenerationRef.current === owner.generation &&
      subjectOwnerRef.current === owner &&
      !owner.abortController.signal.aborted
    );
  }, []);

  useEffect(() => {
    subjectGenerationRef.current += 1;
    subjectOwnerRef.current?.abortController.abort();
    subjectOwnerRef.current = null;
    setSubjectError(null);

    if (!authSubjectKey) {
      setSubjectSettings(null);
      return;
    }

    const owner: SubjectRequestOwner = {
      subjectKey: authSubjectKey,
      generation: subjectGenerationRef.current,
      abortController: new AbortController(),
    };
    subjectOwnerRef.current = owner;
    setSubjectSettings(emptySubjectSettings(authSubjectKey));

    const patchCurrentSubject = (
      patch: Partial<Omit<SubjectSettingsSnapshot, "subjectKey">>,
    ) => {
      if (!isCurrentSubjectOwner(owner)) return;
      setSubjectSettings((previous) => ({
        ...(previous?.subjectKey === owner.subjectKey
          ? previous
          : emptySubjectSettings(owner.subjectKey)),
        ...patch,
      }));
    };

    void modelPublicApi
      .listAvailable({ signal: owner.abortController.signal })
      .then((data) => {
        const dbModels =
          data.models && data.models.length > 0
            ? data.models.map((model) => ({
                id: model.id || "",
                value: model.value,
                provider: model.provider,
                label: model.label,
                description: model.description,
              }))
            : null;
        patchCurrentSubject({
          adminDefaultModelId: data.default_model_id || "",
          dbModels,
        });
      })
      .catch(() => {
        patchCurrentSubject({ adminDefaultModelId: "", dbModels: null });
      })
      .finally(() => patchCurrentSubject({ isLoading: false }));

    void modelPublicApi
      .getPinnedModelIds({ signal: owner.abortController.signal })
      .then((pinnedModelIds) => patchCurrentSubject({ pinnedModelIds }))
      .catch(() => patchCurrentSubject({ pinnedModelIds: [] }));

    return () => {
      if (subjectOwnerRef.current === owner) {
        subjectGenerationRef.current += 1;
        subjectOwnerRef.current = null;
      }
      owner.abortController.abort();
    };
  }, [authSubjectKey, isCurrentSubjectOwner]);

  const visibleSubjectSettings =
    authSubjectKey && subjectSettings?.subjectKey === authSubjectKey
      ? subjectSettings
      : null;
  const dbModels = visibleSubjectSettings?.dbModels ?? null;
  const adminDefaultModelId =
    visibleSubjectSettings?.adminDefaultModelId ?? "";
  const pinnedModelIds =
    visibleSubjectSettings?.pinnedModelIds ?? EMPTY_PINNED_MODEL_IDS;

  const togglePinnedModel = useCallback(
    (modelId: string) => {
      const owner = subjectOwnerRef.current;
      if (!owner || !isCurrentSubjectOwner(owner)) return;
      const current = subjectSettingsRef.current;
      if (current?.subjectKey !== owner.subjectKey) return;
      const nextPinnedModelIds = current.pinnedModelIds.includes(modelId)
        ? current.pinnedModelIds.filter((id) => id !== modelId)
        : [...current.pinnedModelIds, modelId];
      setSubjectSettings({ ...current, pinnedModelIds: nextPinnedModelIds });
      void modelPublicApi
        .updatePinnedModelIds(nextPinnedModelIds, {
          signal: owner.abortController.signal,
        })
        .then((serverPinnedModelIds) => {
          if (!isCurrentSubjectOwner(owner)) return;
          setSubjectSettings((previous) =>
            previous?.subjectKey === owner.subjectKey
              ? { ...previous, pinnedModelIds: serverPinnedModelIds }
              : previous,
          );
        })
        .catch(() => {
          // The optimistic value remains local to this exact subject. A later
          // authoritative hydration is the recovery route.
        });
    },
    [isCurrentSubjectOwner],
  );

  // Auto-clean orphaned pinned IDs (models that were deleted)
  const cleanedPinnedIds = useMemo(() => {
    if (!dbModels || pinnedModelIds.length === 0) return pinnedModelIds;
    const validIds = new Set(dbModels.map((m) => m.id));
    const cleaned = pinnedModelIds.filter((id) => validIds.has(id));
    return cleaned;
  }, [dbModels, pinnedModelIds]);

  useEffect(() => {
    if (cleanedPinnedIds.length === pinnedModelIds.length) return;
    const owner = subjectOwnerRef.current;
    if (!owner || !isCurrentSubjectOwner(owner)) return;
    setSubjectSettings((previous) =>
      previous?.subjectKey === owner.subjectKey
        ? { ...previous, pinnedModelIds: cleanedPinnedIds }
        : previous,
    );
    void modelPublicApi
      .updatePinnedModelIds(cleanedPinnedIds, {
        signal: owner.abortController.signal,
      })
      .catch(() => {});
  }, [cleanedPinnedIds, isCurrentSubjectOwner, pinnedModelIds.length]);

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
    setSubjectError({
      subjectKey: authSubjectKeyRef.current,
      message: "Settings management requires the phase 2 admin projection.",
    });
    return false;
  }, []);

  const clearError = useCallback(() => {
    setSubjectError(null);
  }, []);

  const error =
    subjectError?.subjectKey === authSubjectKey
      ? subjectError.message
      : null;

  const value: SettingsContextValue = {
    settings: null,
    enableSkills: true,
    enableMemory: true,
    availableModels,
    defaultModel,
    pinnedModelIds: cleanedPinnedIds,
    togglePinnedModel,
    isLoading: visibleSubjectSettings?.isLoading ?? false,
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
