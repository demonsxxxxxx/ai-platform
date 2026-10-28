import { Button, Checkbox, Input, SwitchRoot, SwitchThumb, Table, Tag } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useMemo, useRef, useState, type HTMLAttributes } from "react";
import { DatabaseZap, RefreshCw, Save, Search, PlugZap, RotateCcw } from "lucide-react";
import {
  modelAdminApi,
  type AdminModelEntry,
  type AdminModelState,
} from "../../services/api/modelAdmin";
import { ApiRequestError } from "../../services/api/fetch";
import { modelGroupsApi, type AdminModelGroupDetails, type AdminModelGroupSummary } from "../../services/api/modelGroups";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { GroupDrawer } from "./ModelGroupManagement";

import { useModelCatalogContext } from "../../contexts/ModelCatalogContext";

const STATUS_FILTERS = [
  ["all", "状态：全部"],
  ["enabled", "状态：已启用"],
  ["disabled", "状态：未启用"],
  ["unavailable", "状态：上游缺失"],
] as const;

const MODEL_ADMIN_ERROR_MESSAGES: Record<string, string> = {
  model_connection_endpoint_invalid: "API 地址格式无效，请填写模型服务地址。",
  model_connection_endpoint_must_be_origin: "API 地址应为服务根地址或以 /v1 结尾，不能含其它路径。",
  model_connection_endpoint_forbidden: "该 API 地址未获平台网络策略授权。",
  model_connection_dns_unavailable: "API 地址无法解析，请检查主机名和网络。",
  model_connection_https_required: "公网模型 API 必须使用 HTTPS。",
  model_connection_api_key_invalid: "API Key 格式无效，请重新检查。",
  model_connection_api_key_required: "请输入 API Key。",
  model_connection_authentication_failed: "API Key 无效或上游拒绝认证。",
  model_connection_rate_limited: "上游请求过于频繁，请稍后重试。",
  model_connection_catalog_failed: "无法读取上游 /v1/models，请检查地址和服务状态。",
  model_connection_catalog_invalid: "上游 /v1/models 返回格式不符合兼容协议。",
  model_connection_catalog_empty: "上游 /v1/models 没有返回任何模型。",
  model_upstream_unavailable: "无法连接模型服务，请检查地址、网络和服务状态。",
  model_catalog_revision_conflict: "模型配置已被更新，请重新加载配置后再发布。",
  model_catalog_discovery_changed: "上游模型已变化，请重新获取后再发布。",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    const message = error.code ? MODEL_ADMIN_ERROR_MESSAGES[error.code] : undefined;
    if (message) return message;
    if (error.status >= 500) return "模型服务暂不可用，请检查连接后重试。";
    if (error.status === 409) return "模型配置已变化，请重新获取后再发布。";
    if (error.status === 400 || error.status === 422) return "模型配置无效，请检查后重试。";
  }
  return error instanceof Error ? error.message : "模型配置操作失败";
}

function validTokenLimit(value: number | undefined): boolean {
  return typeof value === "number"
    && Number.isInteger(value) && value >= 1 && value <= 10_000_000;
}

// Claude Code accepts automatic-compaction windows from 100k through 1M.
const AUTO_COMPACT_WINDOW_MIN = 100_000;
const AUTO_COMPACT_WINDOW_MAX = 1_000_000;

function validAutoCompactWindow(value: number | undefined): boolean {
  return typeof value === "number"
    && Number.isInteger(value)
    && value >= AUTO_COMPACT_WINDOW_MIN && value <= AUTO_COMPACT_WINDOW_MAX;
}

type ModelAdminOperation = "load" | "test" | "discover" | "publish" | "batch";
interface ModelAdminRequestOwner {
  sequence: number;
  connectionVersion: number;
  controller: AbortController;
  operation: ModelAdminOperation;
}
interface ModelCatalogReceipt {
  connectionVersion: number;
  revision: number | null;
}

function connectionOrigin(value: string): string {
  const trimmed = value.trim();
  try {
    const url = new URL(trimmed);
    if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      && !url.search && !url.hash && ["", "/v1"].includes(url.pathname.replace(/\/+$/, ""))) {
      return url.origin;
    }
  } catch { /* The server owns endpoint validation. */ }
  return trimmed;
}

function modelDraftSignature(models: AdminModelEntry[]): string {
  return JSON.stringify(models.map((model) => [
    model.id, model.value, model.label, model.enabled,
    model.order, model.auto_compact_window ?? null, model.max_output_tokens ?? null,
  ]));
}

function mergeModelCandidates(candidates: AdminModelEntry[], draft: AdminModelEntry[]): AdminModelEntry[] {
  return candidates.map((candidate) => {
    const previous = draft.find((model) => model.id === candidate.id && model.value === candidate.value);
    return previous ? {
      ...candidate,
      label: previous.label, enabled: previous.enabled,
      auto_compact_window: previous.auto_compact_window, max_output_tokens: previous.max_output_tokens,
    } : candidate;
  });
}

export type ModelAdminControlState = "loading" | "ready" | "degraded";

export function ModelAdminControl({
  canManage = true,
  onStateChange,
  groupRevision = 0,
  onGroupsChanged,
}: {
  canManage?: boolean;
  onStateChange?: (state: ModelAdminControlState) => void;
  groupRevision?: number;
  onGroupsChanged?: () => void;
}) {
  const { refreshModels } = useModelCatalogContext();
  const [state, setState] = useState<AdminModelState | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [draft, setDraft] = useState<AdminModelEntry[]>([]);
  const [catalogReceipt, setCatalogReceipt] = useState<ModelCatalogReceipt | null>(null);
  const [testState, setTestState] = useState<"untested" | "testing" | "passed" | "failed">("untested");
  const [busy, setBusy] = useState<ModelAdminOperation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [groupFilter, setGroupFilter] = useState("all");
  const [editingGroup, setEditingGroup] = useState<AdminModelGroupSummary | null>(null);
  const [groupTab, setGroupTab] = useState<"models" | "members">("models");
  const groupTrigger = useRef<HTMLElement | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [groupOptions, setGroupOptions] = useState<AdminModelGroupSummary[]>([]);
  const [groupDetails, setGroupDetails] = useState<Record<string, AdminModelGroupDetails>>({});
  const [groupDetailsReady, setGroupDetailsReady] = useState(false);
  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([]);
  const [batchOperation, setBatchOperation] = useState<"add" | "replace" | "remove" | null>(null);
  const [batchGroupIds, setBatchGroupIds] = useState<string[]>([]);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchPreview, setBatchPreview] = useState<Awaited<ReturnType<typeof modelGroupsApi.batchModels>> | null>(null);
  const [batchPreviewIntent, setBatchPreviewIntent] = useState<{
    groupIds: string[];
    modelIds: string[];
    operation: "add" | "replace" | "remove";
    revisions: Record<string, number>;
  } | null>(null);
  const groupRequestRef = useRef<{ controller: AbortController; sequence: number } | null>(null);
  const groupRequestSequence = useRef(0);
  const connectionVersion = useRef(0);
  const requestSequence = useRef(0);
  const activeRequest = useRef<ModelAdminRequestOwner | null>(null);

  const abandonRequest = useCallback(() => {
    requestSequence.current += 1;
    activeRequest.current?.controller.abort();
    activeRequest.current = null;
  }, []);

  const startRequest = useCallback((operation: ModelAdminOperation) => {
    if (activeRequest.current) return null;
    const owner: ModelAdminRequestOwner = {
      sequence: ++requestSequence.current,
      connectionVersion: connectionVersion.current,
      controller: new AbortController(), operation,
    };
    activeRequest.current = owner;
    setBusy(operation);
    return owner;
  }, []);

  const ownsRequest = useCallback((owner: ModelAdminRequestOwner) =>
    activeRequest.current === owner && owner.sequence === requestSequence.current
      && owner.connectionVersion === connectionVersion.current && !owner.controller.signal.aborted, []);

  const finishRequest = useCallback((owner: ModelAdminRequestOwner) => {
    if (!ownsRequest(owner)) return;
    activeRequest.current = null;
    setBusy(null);
  }, [ownsRequest]);

  const applyState = useCallback((next: AdminModelState) => {
    setState(next);
    setBaseUrl(next.connection.base_url || "");
    setCredential("");
    setDraft(next.models);
    setCatalogReceipt(next.connection.configured ? {
      connectionVersion: connectionVersion.current, revision: next.connection.revision,
    } : null);
  }, []);

  const load = useCallback(async () => {
    if (activeRequest.current?.operation === "publish") return;
    abandonRequest();
    connectionVersion.current += 1;
    const owner = startRequest("load");
    if (!owner) return;
    setCredential("");
    setCatalogReceipt(null);
    setTestState("untested");
    setError(null);
    setPublishError(null);
    setMessage(null);
    onStateChange?.("loading");
    try {
      const next = await modelAdminApi.get({ signal: owner.controller.signal });
      if (!ownsRequest(owner)) return;
      applyState(next);
      onStateChange?.("ready");
    } catch (caught) {
      if (!ownsRequest(owner)) return;
      setError(errorMessage(caught));
      onStateChange?.("degraded");
    } finally { finishRequest(owner); }
  }, [abandonRequest, startRequest, ownsRequest, applyState, onStateChange, finishRequest]);

  useEffect(() => {
    if (canManage) void load();
    else {
      setState(null); setBaseUrl(""); setCredential(""); setDraft([]);
      setCatalogReceipt(null); setTestState("untested"); setBusy(null);
    }
    return () => {
      abandonRequest();
      connectionVersion.current += 1;
    };
  }, [canManage, load, abandonRequest]);

  useEffect(() => {
    groupRequestRef.current?.controller.abort();
    groupRequestRef.current = null;
    setGroupOptions([]);
    setGroupDetails({});
    setGroupDetailsReady(false);
    if (!canManage) return undefined;
    const controller = new AbortController();
    const sequence = ++groupRequestSequence.current;
    groupRequestRef.current = { controller, sequence };
    void modelGroupsApi.listAdmin({ signal: controller.signal }).then(async (result) => {
      const details = await Promise.all(result.groups.map(async (group) => {
        try { return await modelGroupsApi.details(group.id, { signal: controller.signal }); }
        catch { return null; }
      }));
      if (controller.signal.aborted || groupRequestSequence.current !== sequence) return;
      setGroupOptions(result.groups);
      setGroupDetailsReady(details.every((item) => item !== null));
      setGroupDetails(Object.fromEntries(details.filter((item): item is AdminModelGroupDetails => item !== null).map((item) => [item.id, item])));
    }).catch(() => {
      if (!controller.signal.aborted && groupRequestSequence.current === sequence) {
        setGroupOptions([]);
        setGroupDetails({});
        setGroupDetailsReady(false);
      }
    });
    return () => controller.abort();
  }, [canManage, groupRevision]);

  const invalidateConnection = () => {
    if (activeRequest.current?.operation === "publish" || activeRequest.current?.operation === "load") return false;
    abandonRequest();
    connectionVersion.current += 1;
    setBusy(null);
    setCatalogReceipt(null);
    setTestState("untested");
    setError(null); setPublishError(null); setMessage(null);
    return true;
  };

  const reuseSavedCredential = Boolean(state?.connection.configured
    && connectionOrigin(baseUrl) === connectionOrigin(state.connection.base_url));
  const connectionReady = Boolean(state && baseUrl.trim() && (credential.trim() || reuseSavedCredential));
  const hasChanges = Boolean(state && (credential.trim()
    || connectionOrigin(baseUrl) !== connectionOrigin(state.connection.base_url)
    || modelDraftSignature(draft) !== modelDraftSignature(state.models)));
  const canPublish = Boolean(state && hasChanges && catalogReceipt
    && catalogReceipt.connectionVersion === connectionVersion.current);
  const connectionLocked = busy === "load" || busy === "publish";

  const discover = async (operation: "test" | "discover") => {
    if (!connectionReady) return;
    const owner = startRequest(operation);
    if (!owner) return;
    const requestedUrl = baseUrl.trim();
    const requestedCredential = credential.trim() || undefined;
    setTestState("testing");
    setError(null); setPublishError(null); setMessage(null);
    try {
      const result = await modelAdminApi.discover(requestedUrl, requestedCredential, {
        signal: owner.controller.signal,
      });
      if (!ownsRequest(owner)) return;
      setTestState("passed");
      // Discovery owns candidates, never the editable address or credential.
      if (operation === "discover") {
        // Only a loaded or published state owns the draft's compare-and-swap baseline.
        // Discovery may refresh candidates, but cannot acknowledge another admin's edits.
        if (!state || result.connection.revision !== state.connection.revision) {
          setCatalogReceipt(null);
          setPublishError("模型配置已被更新，未保存编辑已保留，请重新加载配置后再发布。");
          return;
        }
        setDraft(mergeModelCandidates(result.models, draft));
        setCatalogReceipt({ connectionVersion: owner.connectionVersion, revision: state.connection.revision });
        setMessage(`已获取 ${result.models.length} 个候选模型，已有编辑已保留；保存后生效。`);
      } else {
        setMessage(`连接测试通过，上游返回 ${result.models.length} 个模型；测试不会保存配置。`);
      }
    } catch (caught) {
      if (!ownsRequest(owner)) return;
      setTestState("failed");
      setError(errorMessage(caught));
    } finally { finishRequest(owner); }
  };

  const updateDraft = (id: string, change: Partial<AdminModelEntry>) => {
    if (activeRequest.current) return;
    setDraft((current) => current.map((model) => model.id === id ? { ...model, ...change } : model));
    setMessage(null); setPublishError(null);
  };

  const cancelDraft = () => {
    if (!state || connectionLocked) return;
    abandonRequest();
    connectionVersion.current += 1;
    setBusy(null); applyState(state); setTestState("untested");
    setError(null); setPublishError(null);
    setMessage("已放弃未保存的修改，恢复已生效配置。");
  };

  const publish = async () => {
    if (activeRequest.current || !hasChanges) return;
    const available = draft.filter((model) => model.available);
    const enabled = available.filter((model) => model.enabled);
    setPublishError(null);
    if (!canPublish || !catalogReceipt) {
      setPublishError("连接已修改，请先获取当前地址的模型，再保存配置。"); return;
    }
    if (!enabled.length) { setPublishError("请至少启用一个模型。"); return; }
    const invalid = available.find((model) => {
      if (!model.enabled && model.auto_compact_window === undefined
        && model.max_output_tokens === undefined) return false;
      // An empty window takes the platform default; only a filled one must be valid.
      return (model.auto_compact_window !== undefined
        && !validAutoCompactWindow(model.auto_compact_window))
        || !validTokenLimit(model.max_output_tokens);
    });
    if (invalid) {
      setPublishError(invalid.enabled
        ? `请填写 ${invalid.label} 的最大输出 Token（1–10,000,000）；压缩窗口留空即默认 100,000，或填写 100,000–1,000,000。`
        : `请填写 ${invalid.label}（${invalid.value}）的最大输出 Token（1–10,000,000）和有效的压缩窗口（100,000–1,000,000），或同时留空。`);
      return;
    }
    if (available.some((model) => model.label.trim().length > 160)) {
      setPublishError("平台显示名称最多 160 个字符。"); return;
    }
    const owner = startRequest("publish");
    if (!owner) return;
    setError(null); setMessage(null);
    try {
      const next = await modelAdminApi.publish(
        baseUrl.trim(), credential.trim() || undefined, catalogReceipt.revision,
        available.map((model) => ({ ...model, display_name: model.label.trim() })),
        { signal: owner.controller.signal },
      );
      if (!ownsRequest(owner)) return;
      applyState(next);
      setTestState("passed");
      const refreshed = await refreshModels({ force: true });
      if (!ownsRequest(owner)) return;
      setMessage(refreshed
        ? "模型配置已保存并生效，聊天中的模型列表已重新读取。"
        : "模型配置已保存并生效；模型列表暂未刷新，打开选择器或回到前台后会重试。");
    } catch (caught) {
      if (!ownsRequest(owner)) return;
      if (caught instanceof ApiRequestError && caught.status === 409) setCatalogReceipt(null);
      setPublishError(errorMessage(caught));
    } finally { finishRequest(owner); }
  };

  const enabledCount = draft.filter((model) => model.enabled).length;
  const modelGroupIds = useMemo(() => {
    const ids = new Map<string, string[]>();
    for (const group of groupOptions) {
      for (const model of groupDetails[group.id]?.models ?? []) {
        ids.set(model.model_id, [...(ids.get(model.model_id) ?? []), group.id]);
      }
    }
    return ids;
  }, [groupDetails, groupOptions]);
  const selectedModels = draft.filter((model) => selectedModelIds.includes(model.id));
  const batchGroups = groupOptions.filter((group) => group.enabled);
  const filteredGroup = groupOptions.find((group) => group.id === groupFilter
    || (groupFilter === "default" && group.type === "ordinary_default"));
  const refreshGroups = async (signal?: AbortSignal) => {
    const result = await modelGroupsApi.listAdmin({ signal });
    const details = await Promise.all(result.groups.map((g) => modelGroupsApi.details(g.id, { signal })));
    if (signal?.aborted) return;
    setGroupOptions(result.groups);
    setGroupDetails(Object.fromEntries(details.map((g) => [g.id, g])));
    setGroupDetailsReady(true);
    onGroupsChanged?.();
  };
  const batchAffectedGroupIds = useMemo(() => {
    const ids = new Set<string>(batchGroupIds);
    if (batchOperation === "replace") {
      for (const model of selectedModels) for (const groupId of modelGroupIds.get(model.id) ?? []) ids.add(groupId);
    }
    return [...ids];
  }, [batchGroupIds, batchOperation, modelGroupIds, selectedModels]);
  const batchRevisions = Object.fromEntries(
    batchAffectedGroupIds
      .map((id) => [id, groupDetails[id]?.revision] as const)
      .filter((entry): entry is readonly [string, number] => typeof entry[1] === "number"),
  ) as Record<string, number>;
  const visibleDraft = draft.filter((model) => {
    const normalizedQuery = query.trim().toLowerCase();
    const matchesQuery = !normalizedQuery
      || model.label.toLowerCase().includes(normalizedQuery)
      || model.value.toLowerCase().includes(normalizedQuery);
    const matchesGroup = groupFilter === "all"
      || (groupFilter === "unassigned" && !modelGroupIds.has(model.id))
      || (groupFilter === "default" && groupOptions.some((group) => (modelGroupIds.get(model.id) ?? []).includes(group.id) && group.type === "ordinary_default"))
      || (groupFilter === "business" && (modelGroupIds.get(model.id) ?? []).some((id) => groupOptions.find((group) => group.id === id)?.type === "business"))
      || (groupFilter !== "default" && groupFilter !== "business" && groupFilter !== "unassigned" && (modelGroupIds.get(model.id) ?? []).includes(groupFilter));
    const matchesStatus = statusFilter === "all"
      || (statusFilter === "enabled" && model.enabled)
      || (statusFilter === "disabled" && !model.enabled)
      || (statusFilter === "unavailable" && !model.available);
    return matchesQuery && matchesGroup && matchesStatus;
  });

  const clearBatchPreview = useCallback(() => {
    setBatchPreview(null);
    setBatchPreviewIntent(null);
  }, []);
  const currentBatchIntent = useMemo(() => batchOperation ? ({
    groupIds: [...batchGroupIds].sort(),
    modelIds: [...selectedModelIds].sort(),
    operation: batchOperation,
    revisions: batchRevisions,
  }) : null, [batchGroupIds, batchOperation, batchRevisions, selectedModelIds]);
  const sameBatchIntent = useCallback((left: typeof currentBatchIntent, right: NonNullable<typeof batchPreviewIntent>) =>
    left !== null && left.operation === right.operation
      && JSON.stringify(left.groupIds) === JSON.stringify(right.groupIds)
      && JSON.stringify(left.modelIds) === JSON.stringify(right.modelIds)
      && JSON.stringify(left.revisions) === JSON.stringify(right.revisions), []);
  useEffect(() => {
    if (batchPreviewIntent && !sameBatchIntent(currentBatchIntent, batchPreviewIntent)) clearBatchPreview();
  }, [batchPreviewIntent, currentBatchIntent, clearBatchPreview, sameBatchIntent]);

  const runBatch = async (preview: boolean) => {
    if (activeRequest.current) return;
    const previewIntent = currentBatchIntent;
    const intent = preview ? previewIntent : batchPreviewIntent;
    if (!intent) return;
    if (intent.groupIds.length === 0 || intent.modelIds.length === 0) return;
    if (!preview && (!batchPreview || !sameBatchIntent(currentBatchIntent, intent))) {
      setBatchError("批量选择或分组版本已变化，请重新预览后确认。");
      clearBatchPreview();
      return;
    }
    if (!groupDetailsReady || intent.groupIds.length === 0 || Object.keys(intent.revisions).length !== batchAffectedGroupIds.length) {
      setBatchError("分组版本仍在加载，请刷新分组详情后重试。");
      return;
    }
    const owner = startRequest("batch");
    if (!owner) return;
    setBatchError(null);
    try {
      const result = await modelGroupsApi.batchModels({ group_ids: intent.groupIds, model_ids: intent.modelIds, operation: intent.operation, expected_group_revisions: intent.revisions, preview }, { signal: owner.controller.signal });
      if (!ownsRequest(owner)) return;
      if (preview) {
        setBatchPreview(result);
        setBatchPreviewIntent(intent as NonNullable<typeof batchPreviewIntent>);
      }
      if (!preview) {
        setBatchOperation(null);
        setSelectedModelIds([]);
        setBatchGroupIds([]);
        clearBatchPreview();
        const refreshed = await Promise.all(groupOptions.map((group) => modelGroupsApi.details(group.id, { signal: owner.controller.signal })));
        if (ownsRequest(owner)) { setGroupDetails(Object.fromEntries(refreshed.map((item) => [item.id, item]))); onGroupsChanged?.(); }
      }
    } catch (caught) {
      if (!ownsRequest(owner)) return;
      setBatchError(caught instanceof ApiRequestError && caught.code === "model_group_default_change_required"
        ? "操作会移除分组的默认模型，请先在分组中指定其他默认模型，再重新预览。"
        : errorMessage(caught));
      clearBatchPreview();
      if (caught instanceof ApiRequestError && caught.status === 409) {
        const refreshed = await Promise.all(Object.keys(intent.revisions).map((id) => modelGroupsApi.details(id, { signal: owner.controller.signal })));
        if (ownsRequest(owner)) setGroupDetails((current) => ({ ...current, ...Object.fromEntries(refreshed.map((item) => [item.id, item])) }));
      }
    } finally { finishRequest(owner); }
  };

  if (!canManage) return null;

  return (
    <section
      aria-label="模型管理"
      className="flex min-h-full min-w-0 shrink-0 flex-col gap-4 p-4 lg:h-full lg:min-h-0 lg:shrink"
      data-model-admin-control
    >
      <div className="shrink-0 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">连接信息</h2>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">兼容模型网关 · 测试和获取候选不会修改已生效配置</p>
          </div>
          <Button type="text"
            size="small"
            data-model-admin-reload
            disabled={connectionLocked}
            onClick={() => void load()}
            htmlType="button"
          ><RefreshCw size={14} aria-hidden="true" />重新加载已保存配置</Button>
        </div>
        <div className="grid min-w-0 gap-3 md:grid-cols-2">
          <label className="flex min-w-0 flex-col gap-1.5 text-sm">
            <span className="font-medium">API 地址</span>
            <Input
              aria-label="模型 API 地址"
              aria-describedby="model-address-help"
              size="large"
              disabled={connectionLocked}
              onChange={(event) => {
                if (!invalidateConnection()) return;
                setBaseUrl(event.target.value);
                setCredential("");
              }}
              placeholder="https://gateway.example.com"
              value={baseUrl}
            />
            <span id="model-address-help" className="text-xs text-[var(--theme-text-secondary)]">支持服务根地址或 /v1；更换地址后需重新填写 Key。</span>
          </label>
          <label className="flex min-w-0 flex-col gap-1.5 text-sm">
            <span className="font-medium">API Key</span>
            <Input
              aria-label="模型 API Key"
              aria-describedby="model-key-help"
              autoComplete="new-password"
              size="large"
              disabled={connectionLocked}
              onChange={(event) => {
                if (!invalidateConnection()) return;
                setCredential(event.target.value);
              }}
              placeholder={reuseSavedCredential ? "留空则保持已保存的 Key" : "输入当前地址对应的 API Key"}
              type="password"
              value={credential}
            />
            <span id="model-key-help" className="text-xs text-[var(--theme-text-secondary)]">{reuseSavedCredential ? "已保存的 Key 不回显；填写后将替换。" : "新地址不会复用已保存的 Key。"}</span>
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <Tag color={testState === "passed" ? "success" : testState === "failed" ? "error" : undefined} role="status" data-model-admin-test-state={testState}>
            {busy === "load" ? "正在加载配置" : testState === "testing" ? "正在测试当前连接"
              : testState === "passed" ? "当前连接测试通过" : testState === "failed" ? "当前连接测试失败"
                : state?.connection.configured && reuseSavedCredential && !credential ? "已配置 · 尚未测试" : "连接草稿 · 尚未测试"}
          </Tag>
          <div className="flex flex-wrap gap-2">
            <Button type="default"
            data-model-admin-test
              disabled={busy !== null || !connectionReady}
              onClick={() => void discover("test")}
              htmlType="button"
            ><PlugZap size={16} aria-hidden="true" />测试连接</Button>
            <Button type="primary"
              loading={busy === "discover"}
              data-model-admin-discover
              disabled={busy !== null || !connectionReady}
              onClick={() => void discover("discover")}
              htmlType="button"
            ><RefreshCw size={16} aria-hidden="true" />获取候选模型</Button>
          </div>
        </div>
        {error ? <p className="mt-3 text-sm text-[var(--theme-danger-text)]" role="alert">{error}</p> : null}
        {message ? <p className="mt-3 text-sm text-[var(--theme-text-secondary)]" role="status">{message}</p> : null}
      </div>

      <div className="flex min-w-0 shrink-0 flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] lg:min-h-0 lg:flex-1 lg:shrink">
        <div className="shrink-0 border-b border-[var(--theme-border)] px-3 py-3">
          <h2 className="text-sm font-semibold">模型配置</h2>
          <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{catalogReceipt ? "编辑模型启用状态和容量；路由默认项由模型分组管理。" : "连接已修改，请获取当前地址的候选模型后再保存。"}</p>
        </div>
        <div className="flex shrink-0 flex-col gap-3 border-b border-[var(--theme-border)] p-3 md:flex-row md:items-center md:justify-between">
          <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row">
            <label className="relative min-w-0 sm:max-w-sm sm:flex-1">
              <Input
                aria-label="搜索模型"
                disabled={connectionLocked}
                prefix={<Search size={16} />} size="large"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索模型名称或模型 ID"
                value={query}
              />
            </label>
            <LobeSelectField
              aria-label="筛选模型分组"
              disabled={connectionLocked}
              className="h-10 sm:w-44"
              onChange={setGroupFilter}
              value={groupFilter}
              options={[
                ["all", "分组：全部"], ["default", "分组：普通默认组"],
                ["business", "分组：业务组"], ["unassigned", "分组：未分组"],
                ...groupOptions.filter((group) => group.type === "business").map((group) => [group.id, `分组：${group.name}`] as const),
              ].map(([value, label]) => ({ value, label }))}
            />
            <LobeSelectField
              aria-label="筛选模型状态"
              disabled={connectionLocked}
              className="h-10 sm:w-40"
              onChange={setStatusFilter}
              value={statusFilter}
              options={STATUS_FILTERS.map(([value, label]) => ({ value, label }))}
            />
          </div>
          <span className="shrink-0 text-xs text-[var(--theme-text-secondary)]">
            显示 {visibleDraft.length} / {draft.length} 个模型
          </span>
        </div>
        {filteredGroup ? <div className="flex flex-wrap items-center gap-2 border-b border-[var(--theme-border)] p-3" data-model-admin-group-settings>
          <span className="text-sm font-medium">{filteredGroup.name}</span>
          <span className="text-xs text-[var(--theme-text-secondary)]">模型可加入多个组；各组的启用、展示顺序与默认模型独立配置。</span>
          <Button size="small" disabled={busy !== null} onClick={(event) => { groupTrigger.current = event.currentTarget; setGroupTab("models"); setEditingGroup(filteredGroup); }}>编辑组内模型</Button>
          {filteredGroup.type === "business" ? <Button size="small" disabled={busy !== null} onClick={(event) => { groupTrigger.current = event.currentTarget; setGroupTab("members"); setEditingGroup(filteredGroup); }}>管理成员</Button> : null}
        </div> : null}
        {selectedModelIds.length ? <div className="flex flex-wrap items-center gap-2 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3" data-model-admin-batch-toolbar>
          <span className="text-xs font-medium">已选 {selectedModelIds.length} 个模型</span>
          <div className="flex flex-wrap items-center gap-2" aria-label="批量目标业务组" data-model-admin-batch-groups>
            {batchGroups.map((group) => <Checkbox key={group.id} aria-label={`批量目标 ${group.name}`} checked={batchGroupIds.includes(group.id)} disabled={busy !== null} onChange={(checked) => setBatchGroupIds((current) => checked ? [...current, group.id] : current.filter((id) => id !== group.id))}>{group.name}</Checkbox>)}
          </div>
          <Button size="small" disabled={!batchGroupIds.length || busy !== null} onClick={() => { setBatchOperation("add"); setBatchPreview(null); }} htmlType="button">批量添加</Button>
          <Button size="small" disabled={!batchGroupIds.length || busy !== null} onClick={() => { setBatchOperation("replace"); setBatchPreview(null); }} htmlType="button">批量替换</Button>
          <Button size="small" danger disabled={!batchGroupIds.length || busy !== null} onClick={() => { setBatchOperation("remove"); setBatchPreview(null); }} htmlType="button">批量移出</Button>
          {batchError ? <span role="alert" className="text-xs text-[var(--theme-danger-text)]">{batchError}</span> : null}
        </div> : null}
        {batchOperation ? <div className="flex flex-wrap items-center gap-2 border-b border-[var(--theme-border)] px-3 py-2" data-model-admin-batch-confirm>
          <span className="text-xs text-[var(--theme-text-secondary)]">{batchOperation === "add" ? "添加" : batchOperation === "replace" ? "替换" : "移出"}前先预览原子影响；确认后一次提交。</span>
          <Button size="small" loading={busy === "batch"} disabled={busy !== null} onClick={() => void runBatch(true)} htmlType="button">预览影响</Button>
          {batchPreview ? <><span className="text-xs text-[var(--theme-text-secondary)]" data-model-admin-batch-preview>预览将影响 {batchPreview.groups.filter((group) => group.changed).length} 个分组，{batchPreview.changed ? "确认后原子提交" : "无需变更"}。</span><Button size="small" type="primary" loading={busy === "batch"} disabled={busy !== null || !batchPreview.changed} onClick={() => void runBatch(false)} htmlType="button">确认并保存</Button></> : null}
          <Button size="small" disabled={busy !== null} onClick={() => { setBatchOperation(null); setBatchPreview(null); }} htmlType="button">取消</Button>
        </div> : null}

        <p
          className="shrink-0 border-b border-[var(--theme-border)] px-3 pb-2 text-xs text-[var(--theme-text-secondary)]"
          id="model-admin-compaction-window-hint"
        >
          压缩窗口交给 Claude 自动压缩，实际触发时机由引擎决定，可能早于该值；留空按 100000。
        </p>
        <div className="min-w-0 overflow-x-auto lg:min-h-0 lg:flex-1 lg:overflow-auto" data-model-admin-table-scroll>
          <Table
 size="small" pagination={false} scroll={{ x: 1000 }} tableLayout="fixed" sticky
 dataSource={visibleDraft}
 rowKey={(model) => model.id}
 onRow={(model) => ({ "data-model-admin-model": model.id } as HTMLAttributes<HTMLTableRowElement>)}
 columns={[
{ key: "selected", width: 52, title: "选择",
render: (_value, model) => <Checkbox aria-label={`选择模型 ${model.label}`} data-model-admin-select={model.id} checked={selectedModelIds.includes(model.id)} disabled={busy !== null} onChange={(checked) => setSelectedModelIds((current) => checked ? [...current, model.id] : current.filter((id) => id !== model.id))} /> },
{ key: "enabled", width: 70, title: "启用",
render: (_value, model) => (<>
<label className="inline-flex cursor-pointer items-center">
                      <SwitchRoot
                        aria-label={`启用 ${model.label}`}
                        checked={model.enabled}
                        disabled={!model.available || busy !== null}
                        onCheckedChange={(checked) => updateDraft(model.id, {
                          enabled: checked,
                        })}
                      ><SwitchThumb /></SwitchRoot>

                    </label>
</>) },
{ key: "identity", width: 280, title: "平台显示名称 / 上游模型 ID",
render: (_value, model) => (<>
<div className="flex min-w-0 items-center gap-3">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--theme-info-soft)] text-[var(--theme-info-text)]" aria-hidden="true">
                        <DatabaseZap size={16} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <Input
                          aria-label={`${model.value} 平台显示名称`}
                          size="small" variant="borderless"
                          onChange={(event) => updateDraft(model.id, { label: event.target.value })}
                          disabled={busy !== null}
                          value={model.label}
                        />
                        <p className="truncate text-xs text-[var(--theme-text-secondary)]">{model.value}</p>
                        <div className="mt-1 flex flex-wrap gap-1" data-model-admin-group-tags>
                          {(modelGroupIds.get(model.id) ?? []).map((groupId) => {
                            const group = groupOptions.find((item) => item.id === groupId);
                            return group ? <Tag key={group.id} size="small">{group.type === "ordinary_default" ? "普通默认组" : group.name}</Tag> : null;
                          })}
                          {!modelGroupIds.has(model.id) ? <Tag size="small">未分组</Tag> : null}
                        </div>
                      </div>
                    </div>
</>) },
{ key: "inputTokens", width: 160, title: "压缩窗口",
render: (_value, model) => (<>
<Input
                      aria-label={`${model.value} 压缩窗口 Token`}
                      aria-describedby="model-admin-compaction-window-hint"
                      size="small"
                      disabled={!model.available || busy !== null}
                      min={AUTO_COMPACT_WINDOW_MIN}
                      max={AUTO_COMPACT_WINDOW_MAX}
                      step={1000}
                      onChange={(event) => updateDraft(model.id, { auto_compact_window: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder="默认 100000"
                      type="number"
                      value={model.auto_compact_window ?? ""}
                    />
</>) },
{ key: "outputTokens", width: 160, title: "最大输出 Token",
render: (_value, model) => (<>
<Input
                      aria-label={`${model.value} 最大输出 Token`}
                      size="small"
                      disabled={!model.available || busy !== null}
                      min={1}
                      max={10000000}
                      onChange={(event) => updateDraft(model.id, { max_output_tokens: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder={model.enabled ? "必填" : "可同时留空"}
                      aria-required={model.enabled}
                      type="number"
                      value={model.max_output_tokens ?? ""}
                    />
</>) },
{ key: "availability", width: 120, title: "状态",
render: (_value, model) => (<>
<Tag color={model.available ? "success" : "error"} size="small">{model.available ? "已发现" : "上游缺失"}</Tag>
</>) },
]}
 emptyText={draft.length ? "没有匹配的模型" : busy === "load" ? "正在加载模型配置" : "请先获取候选模型"}
/>
        </div>
      </div>
      <div className="sticky bottom-0 z-10 flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 lg:static" data-model-admin-action-bar>
        {publishError ? (
          <p className="text-xs text-[var(--theme-danger-text)]" role="alert">{publishError}</p>
        ) : (
          <p className="text-xs text-[var(--theme-text-secondary)]" aria-live="polite">
            已启用 {enabledCount} / {draft.length} · {hasChanges ? "有未保存的修改，保存后生效" : "已生效配置"}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="default"
            data-model-admin-cancel
            disabled={!state || connectionLocked || (!hasChanges && busy === null)}
            onClick={cancelDraft}
            htmlType="button"
          ><RotateCcw size={16} aria-hidden="true" />放弃修改</Button>
          <Button type="primary"
            loading={busy === "publish"}
            data-model-admin-publish
            disabled={busy !== null || !canPublish}
            onClick={() => void publish()}
            htmlType="button"
          ><Save size={16} aria-hidden="true" />{busy === "publish" ? "正在保存" : "保存并生效"}</Button>
        </div>
      </div>
      <GroupDrawer group={editingGroup} catalog={draft} initialTab={groupTab} onClose={() => setEditingGroup(null)} onChanged={refreshGroups} finalFocus={groupTrigger} />
    </section>
  );
}
