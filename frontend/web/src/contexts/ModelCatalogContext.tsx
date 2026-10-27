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
import { modelGroupsApi, type PublicModelGroup } from "../services/api/modelGroups";

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
  modelGroups: PublicModelGroup[];
  selectedGroupId: string;
  selectedGroup: PublicModelGroup | null;
  selectGroup: (groupId: string) => void;
  selectionError: string | null;
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
  const groupRequestRef = useRef<AbortController | null>(null);
  const requestedGroupIdRef = useRef("");
  const [modelGroups, setModelGroups] = useState<PublicModelGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [selectionError, setSelectionError] = useState<string | null>(null);
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
    groupRequestRef.current?.abort();
    groupRequestRef.current = null;
    subjectOwnerRef.current?.abortController.abort();
    subjectOwnerRef.current = null;
    subjectGenerationRef.current += 1;
    setModelGroups([]);
    setSelectedGroupId("");
    requestedGroupIdRef.current = "";
    setSelectionError(null);

    if (!authSubjectKey) {
      setSubjectModelCatalog(null);
      return undefined;
    }

    setSubjectModelCatalog(emptySubjectModelCatalog(authSubjectKey));
    const controller = new AbortController();
    groupRequestRef.current = controller;
    void modelGroupsApi
      .listPublic({ signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted || authSubjectKeyRef.current !== authSubjectKey) return;
        const businessGroups = (result.groups ?? []).filter((group) => group.type === "business" && group.enabled);
        setModelGroups(result.groups ?? []);
        const requestedGroupId = requestedGroupIdRef.current;
        const restoredGroup = businessGroups.find((group) => group.id === requestedGroupId);
        if (requestedGroupId && !restoredGroup) {
          setSelectedGroupId("");
          setSelectionError("恢复的业务组已不可用，请重新选择业务组。");
        } else {
          const primary = (result.groups ?? []).find((group) => group.is_primary);
          const selected = restoredGroup ?? (primary?.enabled && primary.type === "business" ? primary : null);
          setSelectedGroupId(selected?.id ?? "");
          setSelectionError(!selected && primary ? "主组已不可用，请明确选择其他业务组或联系管理员。" : null);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted && authSubjectKeyRef.current === authSubjectKey) {
          setModelGroups([]);
          setSelectedGroupId("");
          setSelectionError("业务组暂时无法加载，请重试或联系管理员。");
        }
      });

    return () => {
      controller.abort();
      if (groupRequestRef.current === controller) groupRequestRef.current = null;
      subjectOwnerRef.current?.abortController.abort();
      subjectOwnerRef.current = null;
    };
  }, [authSubjectKey]);

  const selectGroup = useCallback((groupId: string) => {
    requestedGroupIdRef.current = groupId;
    const group = modelGroups.find((item) => item.id === groupId && item.type === "business" && item.enabled);
    if (!group) {
      setSelectedGroupId("");
      setSelectionError("所选业务组已不可用，请重新选择业务组。");
      return;
    }
    setSelectionError(null);
    setSelectedGroupId(groupId);
  }, [modelGroups]);

  useEffect(() => {
    if (!authSubjectKey) return undefined;
    if (!selectedGroupId) {
      subjectOwnerRef.current?.abortController.abort();
      subjectOwnerRef.current = null;
      subjectGenerationRef.current += 1;
      setSubjectModelCatalog({ subjectKey: authSubjectKey, dbModels: null, adminDefaultModelId: "", isLoading: false });
      return undefined;
    }
    const owner: SubjectRequestOwner = {
      subjectKey: authSubjectKey,
      generation: ++subjectGenerationRef.current,
      abortController: new AbortController(),
    };
    subjectOwnerRef.current?.abortController.abort();
    subjectOwnerRef.current = owner;
    setSubjectModelCatalog(emptySubjectModelCatalog(authSubjectKey));
    const patchCurrentSubject = (
      patch: Partial<Omit<SubjectModelCatalogSnapshot, "subjectKey">>,
    ) => {
      if (!isCurrentSubjectOwner(owner)) return;
      setSubjectModelCatalog((previous) => ({
        ...(previous?.subjectKey === owner.subjectKey ? previous : emptySubjectModelCatalog(owner.subjectKey)),
        ...patch,
      }));
    };
    void modelPublicApi
      .listAvailable({ groupId: selectedGroupId, signal: owner.abortController.signal })
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
  }, [authSubjectKey, isCurrentSubjectOwner, selectedGroupId]);

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

  const businessGroups = modelGroups.filter((group) => group.type === "business" && group.enabled);
  const selectedGroup = businessGroups.find((group) => group.id === selectedGroupId) ?? null;
  const value: ModelCatalogContextValue = {
    availableModels,
    defaultModel,
    modelGroups: businessGroups,
    selectedGroupId,
    selectedGroup,
    selectGroup,
    selectionError,
    isLoading: visibleSubjectModelCatalog?.isLoading ?? Boolean(authSubjectKey && selectedGroupId),
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
