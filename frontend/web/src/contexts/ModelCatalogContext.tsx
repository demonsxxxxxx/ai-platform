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
import { matchPath, useLocation } from "react-router-dom";
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
  const { pathname } = useLocation();
  const expertAgentId = matchPath("/agent-market/:agentId/chat/*", pathname)?.params.agentId;
  const authSubjectKey =
    isAuthenticated && user?.tenant_id
      ? `${user.tenant_id}\u0000${user.id}\u0000${expertAgentId || "chat"}`
      : null;
  const authSubjectKeyRef = useRef(authSubjectKey);
  authSubjectKeyRef.current = authSubjectKey;
  const subjectGenerationRef = useRef(0);
  const subjectOwnerRef = useRef<SubjectRequestOwner | null>(null);
  const requestPromiseRef = useRef<Promise<boolean> | null>(null);
  const membershipIdRef = useRef("");
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
        setModelGroups(groups);
        // Membership is server-owned; only ordinary chat excludes the default group.
        const selected = groups.length === 1 && (groups[0].type === "business" || Boolean(expertAgentId)) && groups[0].enabled
          ? groups[0] : null;
        if (!selected) {
          setSelectedGroupId("");
          setSelectionError("所属模型组不可用或归属异常，请联系管理员。");
          setSubjectModelCatalog({ subjectKey: authSubjectKey, dbModels: [], adminDefaultModelId: "", isLoading: false });
          return true;
        }
        setSelectedGroupId(selected.id);
        setSelectionError(null);
        if (membershipIdRef.current !== selected.id) {
          setSubjectModelCatalog(emptySubjectModelCatalog(authSubjectKey));
        }
        membershipIdRef.current = selected.id;
        const data = await modelPublicApi.listAvailable({ groupId: selected.id, expertAgentId, signal: owner.abortController.signal });
        if (!isCurrentSubjectOwner(owner)) return false;
        setSubjectModelCatalog({ subjectKey: authSubjectKey, dbModels: data.models ?? [],
          adminDefaultModelId: data.default_model_id || "", isLoading: false });
        return true;
      } catch (caught) {
        if (!isCurrentSubjectOwner(owner)) return false;
        const denied = caught instanceof ApiRequestError && (caught.status === 401 || caught.status === 403);
        if (denied) {
          membershipIdRef.current = "";
          setModelGroups([]);
          setSelectedGroupId("");
          setSelectionError("模型或业务组授权已失效，请联系管理员。");
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
  }, [authSubjectKey, expertAgentId, isCurrentSubjectOwner]);

  useEffect(() => {
    authSubjectKeyRef.current = authSubjectKey;
    subjectOwnerRef.current?.abortController.abort();
    subjectOwnerRef.current = null;
    requestPromiseRef.current = null;
    subjectGenerationRef.current += 1;
    membershipIdRef.current = "";
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
    if (!availableModels || availableModels.length === 0) return "";
    return availableModels.find((model) => model.id === adminDefaultModelId)?.value || "";
  }, [adminDefaultModelId, availableModels]);

  const eligibleGroups = (visibleSubjectModelCatalog ? modelGroups : []).filter((group) => (group.type === "business" || Boolean(expertAgentId)) && group.enabled);
  const selectedGroup = eligibleGroups.find((group) => group.id === selectedGroupId) ?? null;
  const value: ModelCatalogContextValue = {
    availableModels,
    defaultModel,
    modelGroups: eligibleGroups,
    selectedGroupId: visibleSubjectModelCatalog ? selectedGroupId : "",
    selectedGroup,
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
