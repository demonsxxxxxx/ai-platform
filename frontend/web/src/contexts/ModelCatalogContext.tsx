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
}

const ModelCatalogContext = createContext<ModelCatalogContextValue | undefined>(
  undefined,
);

interface SubjectModelCatalogSnapshot {
  subjectKey: string;
  dbModels: AvailableModel[] | null;
  adminDefaultModelId: string;
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
