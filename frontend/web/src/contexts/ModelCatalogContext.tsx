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

export interface AvailableModel {
  id: string;
  value: string;
  provider?: string;
  label: string;
  description?: string;
}

interface ModelCatalogContextValue {
  isLoading: boolean;
  availableModels: AvailableModel[] | null;
  defaultModel: string;
  pinnedModelIds: string[];
  togglePinnedModel: (modelId: string) => void;
}

const ModelCatalogContext = createContext<ModelCatalogContextValue | undefined>(
  undefined,
);
const EMPTY_PINNED_MODEL_IDS: string[] = [];

interface SubjectModelCatalogSnapshot {
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

function emptySubjectModelCatalog(subjectKey: string): SubjectModelCatalogSnapshot {
  return {
    subjectKey,
    dbModels: null,
    adminDefaultModelId: "",
    pinnedModelIds: [],
    isLoading: true,
  };
}

export function ModelCatalogProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, user } = useAuth();
  const authSubjectKey =
    isAuthenticated && user?.tenant_id
      ? `${user.tenant_id}\u0000${user.id}`
      : null;
  const authSubjectKeyRef = useRef(authSubjectKey);
  authSubjectKeyRef.current = authSubjectKey;
  const subjectGenerationRef = useRef(0);
  const subjectOwnerRef = useRef<SubjectRequestOwner | null>(null);
  const [subjectModelCatalog, setSubjectModelCatalog] =
    useState<SubjectModelCatalogSnapshot | null>(null);
  const subjectModelCatalogRef = useRef(subjectModelCatalog);
  subjectModelCatalogRef.current = subjectModelCatalog;
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

    if (!authSubjectKey) {
      setSubjectModelCatalog(null);
      return;
    }

    const owner: SubjectRequestOwner = {
      subjectKey: authSubjectKey,
      generation: subjectGenerationRef.current,
      abortController: new AbortController(),
    };
    subjectOwnerRef.current = owner;
    setSubjectModelCatalog(emptySubjectModelCatalog(authSubjectKey));

    const patchCurrentSubject = (
      patch: Partial<Omit<SubjectModelCatalogSnapshot, "subjectKey">>,
    ) => {
      if (!isCurrentSubjectOwner(owner)) return;
      setSubjectModelCatalog((previous) => ({
        ...(previous?.subjectKey === owner.subjectKey
          ? previous
          : emptySubjectModelCatalog(owner.subjectKey)),
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

  const visibleSubjectModelCatalog =
    authSubjectKey && subjectModelCatalog?.subjectKey === authSubjectKey
      ? subjectModelCatalog
      : null;
  const dbModels = visibleSubjectModelCatalog?.dbModels ?? null;
  const adminDefaultModelId =
    visibleSubjectModelCatalog?.adminDefaultModelId ?? "";
  const pinnedModelIds =
    visibleSubjectModelCatalog?.pinnedModelIds ?? EMPTY_PINNED_MODEL_IDS;

  const togglePinnedModel = useCallback(
    (modelId: string) => {
      const owner = subjectOwnerRef.current;
      if (!owner || !isCurrentSubjectOwner(owner)) return;
      const current = subjectModelCatalogRef.current;
      if (current?.subjectKey !== owner.subjectKey) return;
      const nextPinnedModelIds = current.pinnedModelIds.includes(modelId)
        ? current.pinnedModelIds.filter((id) => id !== modelId)
        : [...current.pinnedModelIds, modelId];
      setSubjectModelCatalog({ ...current, pinnedModelIds: nextPinnedModelIds });
      void modelPublicApi
        .updatePinnedModelIds(nextPinnedModelIds, {
          signal: owner.abortController.signal,
        })
        .then((serverPinnedModelIds) => {
          if (!isCurrentSubjectOwner(owner)) return;
          setSubjectModelCatalog((previous) =>
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
    setSubjectModelCatalog((previous) =>
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

  const value: ModelCatalogContextValue = {
    availableModels,
    defaultModel,
    pinnedModelIds: cleanedPinnedIds,
    togglePinnedModel,
    isLoading: visibleSubjectModelCatalog?.isLoading ?? false,
  };

  return (
    <ModelCatalogContext.Provider value={value}>
      {children}
    </ModelCatalogContext.Provider>
  );
}

// Fast refresh only works when a file only exports components.
// Use a new file to share constants or functions between components
// eslint-disable-next-line react-refresh/only-export-components
export function useModelCatalogContext() {
  const context = useContext(ModelCatalogContext);
  if (context === undefined) {
    throw new Error(
      "useModelCatalogContext must be used within a ModelCatalogProvider",
    );
  }
  return context;
}
