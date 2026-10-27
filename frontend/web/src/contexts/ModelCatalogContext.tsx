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
import { ApiRequestError } from "../services/api/fetch";
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
  refreshModels: (options?: { force?: boolean }) => Promise<boolean>;
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
  const requestPromiseRef = useRef<Promise<boolean> | null>(null);
  const requestedGroupIdRef = useRef("");
  const initializedRef = useRef(false);
  const [modelGroups, setModelGroups] = useState<PublicModelGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [subjectModelCatalog, setSubjectModelCatalog] = useState<SubjectModelCatalogSnapshot | null>(null);
  const isCurrentSubjectOwner = useCallback((owner: SubjectRequestOwner) => (
    authSubjectKeyRef.current === owner.subjectKey &&
    subjectGenerationRef.current === owner.generation &&
    subjectOwnerRef.current === owner && !owner.abortController.signal.aborted
  ), []);

  const refreshModels = useCallback(async (options: { force?: boolean } = {}) => {
    if (!authSubjectKey || authSubjectKeyRef.current !== authSubjectKey) return false;
    if (requestPromiseRef.current && !options.force) return requestPromiseRef.current;
    subjectOwnerRef.current?.abortController.abort();
    const owner: SubjectRequestOwner = {
      subjectKey: authSubjectKey, generation: ++subjectGenerationRef.current,
      abortController: new AbortController(),
    };
    subjectOwnerRef.current = owner;
    setSubjectModelCatalog((previous) => ({
      ...(previous?.subjectKey === authSubjectKey ? previous : emptySubjectModelCatalog(authSubjectKey)),
      isLoading: true,
    }));
    const request = (async () => {
      try {
        const result = await modelGroupsApi.listPublic({ signal: owner.abortController.signal });
        if (!isCurrentSubjectOwner(owner)) return false;
        const groups = result.groups ?? [];
        const businessGroups = groups.filter((group) => group.type === "business" && group.enabled);
        setModelGroups(groups);
        const requestedId = requestedGroupIdRef.current;
        const primary = groups.find((group) => group.is_primary);
        const selected = requestedId
          ? businessGroups.find((group) => group.id === requestedId)
          : !initializedRef.current ? businessGroups.find((group) => group.id === primary?.id) : undefined;
        initializedRef.current = true;
        if (!selected) {
          requestedGroupIdRef.current = "";
          setSelectedGroupId("");
          setSelectionError(requestedId
            ? "之前选择的业务组已不可用，请重新选择业务组。"
            : primary && !primary.enabled ? "主组已不可用，请明确选择其他业务组或联系管理员。"
            : "请选择可用业务组；如无可用组，请联系管理员。");
          setSubjectModelCatalog({ subjectKey: authSubjectKey, dbModels: [], adminDefaultModelId: "", isLoading: false });
          return true;
        }
        requestedGroupIdRef.current = selected.id;
        setSelectedGroupId(selected.id);
        setSelectionError(null);
        const data = await modelPublicApi.listAvailable({ groupId: selected.id, signal: owner.abortController.signal });
        if (!isCurrentSubjectOwner(owner)) return false;
        setSubjectModelCatalog({ subjectKey: authSubjectKey, dbModels: data.models ?? [],
          adminDefaultModelId: data.default_model_id || "", isLoading: false });
        return true;
      } catch (caught) {
        if (!isCurrentSubjectOwner(owner)) return false;
        const denied = caught instanceof ApiRequestError && (caught.status === 401 || caught.status === 403);
        if (denied) {
          requestedGroupIdRef.current = "";
          initializedRef.current = true;
          setModelGroups([]);
          setSelectedGroupId("");
          setSelectionError("模型或业务组授权已失效，请重新选择或联系管理员。");
        }
        setSubjectModelCatalog((previous) => ({
          ...(previous?.subjectKey === authSubjectKey ? previous : emptySubjectModelCatalog(authSubjectKey)),
          ...(denied ? { dbModels: [], adminDefaultModelId: "" } : {}), isLoading: false,
        }));
        return false;
      }
    })().finally(() => {
      if (requestPromiseRef.current === request) requestPromiseRef.current = null;
    });
    requestPromiseRef.current = request;
    return request;
  }, [authSubjectKey, isCurrentSubjectOwner]);

  useEffect(() => {
    authSubjectKeyRef.current = authSubjectKey;
    subjectOwnerRef.current?.abortController.abort();
    subjectOwnerRef.current = null;
    requestPromiseRef.current = null;
    subjectGenerationRef.current += 1;
    requestedGroupIdRef.current = "";
    initializedRef.current = false;
    setModelGroups([]);
    setSelectedGroupId("");
    setSelectionError(null);
    setSubjectModelCatalog(authSubjectKey ? emptySubjectModelCatalog(authSubjectKey) : null);
    void refreshModels();
    const refreshOnReturn = () => {
      if (document.visibilityState !== "hidden") void refreshModels();
    };
    window.addEventListener("focus", refreshOnReturn);
    document.addEventListener("visibilitychange", refreshOnReturn);
    return () => {
      subjectGenerationRef.current += 1;
      subjectOwnerRef.current?.abortController.abort();
      subjectOwnerRef.current = null;
      requestPromiseRef.current = null;
      window.removeEventListener("focus", refreshOnReturn);
      document.removeEventListener("visibilitychange", refreshOnReturn);
      authSubjectKeyRef.current = null;
    };
  }, [authSubjectKey, refreshModels]);

  const selectGroup = useCallback((groupId: string) => {
    if (!authSubjectKey || authSubjectKeyRef.current !== authSubjectKey) return;
    requestedGroupIdRef.current = groupId;
    // A group switch invalidates both the group read and its dependent model read.
    subjectOwnerRef.current?.abortController.abort();
    subjectGenerationRef.current += 1;
    requestPromiseRef.current = null;
    const group = modelGroups.find((item) => item.id === groupId && item.type === "business" && item.enabled);
    setSelectedGroupId(group?.id ?? "");
    setSelectionError(group ? null : "所选业务组已不可用，请重新选择业务组。");
    setSubjectModelCatalog(emptySubjectModelCatalog(authSubjectKey));
    void refreshModels({ force: true });
  }, [authSubjectKey, modelGroups, refreshModels]);

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

  const businessGroups = (visibleSubjectModelCatalog ? modelGroups : []).filter((group) => group.type === "business" && group.enabled);
  const selectedGroup = businessGroups.find((group) => group.id === selectedGroupId) ?? null;
  const value: ModelCatalogContextValue = {
    availableModels,
    defaultModel,
    modelGroups: businessGroups,
    selectedGroupId: visibleSubjectModelCatalog ? selectedGroupId : "",
    selectedGroup,
    selectGroup,
    selectionError: visibleSubjectModelCatalog ? selectionError : null,
    refreshModels,
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
