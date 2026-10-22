import { Button, Input, Radio, SwitchRoot, SwitchThumb, Table, Tag } from "@lobehub/ui/base-ui";
import { RadioGroup as BaseRadioGroup } from "@base-ui/react/radio-group";
import { useCallback, useEffect, useRef, useState, type HTMLAttributes } from "react";
import { DatabaseZap, RefreshCw, Save, Search, PlugZap, RotateCcw } from "lucide-react";
import {
  modelAdminApi,
  type AdminModelEntry,
  type AdminModelState,
} from "../../services/api/modelAdmin";
import { companyEmployeesApi, type CompanyEmployee } from "../../services/api/companyEmployees";
import { ModelUserAccess, type UserDirectoryStatus } from "./ModelUserAccess";
import { ApiRequestError } from "../../services/api/fetch";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";

const STATUS_FILTERS = [
  ["all", "状态：全部"],
  ["enabled", "状态：已启用"],
  ["disabled", "状态：未启用"],
  ["unavailable", "状态：上游缺失"],
] as const;

// Matches the company employee directory API page bound (limit <= 100).
const DIRECTORY_PAGE_SIZE = 100;

/** One model's own directory query; rows never share a page or a search. */
interface ModelDirectoryState {
  query: string;
  mode: UserDirectoryStatus;
  page: CompanyEmployee[];
  names: Map<string, CompanyEmployee>;
  total: number;
  error: string | null;
}

const EMPTY_DIRECTORY: ModelDirectoryState = {
  query: "",
  mode: "idle",
  page: [],
  names: new Map(),
  total: 0,
  error: null,
};

function mergeDirectory(
  current: CompanyEmployee[],
  incoming: CompanyEmployee[],
): CompanyEmployee[] {
  const known = new Set(current.map((user) => user.work_id));
  return [...current, ...incoming.filter((user) => !known.has(user.work_id))];
}

const MODEL_ADMIN_ERROR_MESSAGES: Record<string, string> = {
  company_employee_directory_unavailable: "公司员工目录暂不可用，请重试；新增授权尚未保存。",
  company_employee_directory_forbidden: "无权访问公司员工目录。",
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
  model_user_access_invalid: "用户访问设置无效，请重新选择。",
  model_user_access_requires_users: "“仅指定用户”至少需要选择一名用户。",
  model_user_access_unknown_user: "所选工号不在有效公司员工目录中或存在歧义，请移除后重试。",
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

type ModelAdminOperation = "load" | "test" | "discover" | "publish";
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
    model.id, model.value, model.label, model.enabled, model.is_default,
    model.order, model.max_input_tokens ?? null, model.max_output_tokens ?? null,
    model.access_mode ?? "all", [...(model.allowed_user_ids ?? [])].sort(),
  ]));
}

function mergeModelCandidates(candidates: AdminModelEntry[], draft: AdminModelEntry[]): AdminModelEntry[] {
  return candidates.map((candidate) => {
    const previous = draft.find((model) => model.id === candidate.id && model.value === candidate.value);
    return previous ? {
      ...candidate,
      label: previous.label, enabled: previous.enabled, is_default: previous.is_default,
      max_input_tokens: previous.max_input_tokens, max_output_tokens: previous.max_output_tokens,
      access_mode: previous.access_mode ?? "all",
      allowed_user_ids: [...(previous.allowed_user_ids ?? [])],
    } : candidate;
  });
}

export type ModelAdminControlState = "loading" | "ready" | "degraded";

export function ModelAdminControl({
  canManage = true,
  onStateChange,
}: {
  canManage?: boolean;
  onStateChange?: (state: ModelAdminControlState) => void;
}) {
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
  const [statusFilter, setStatusFilter] = useState("all");
  const [directoryByModel, setDirectoryByModel] = useState<Record<string, ModelDirectoryState>>({});
  const directoryOwners = useRef(new Map<string, AbortController>());
  const directoryVersions = useRef(new Map<string, number>());
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

  const patchDirectory = useCallback((modelId: string, patch: Partial<ModelDirectoryState>) => {
    setDirectoryByModel((current) => ({
      ...current,
      [modelId]: { ...(current[modelId] ?? EMPTY_DIRECTORY), ...patch },
    }));
  }, []);

  // Each model owns its own query, page and owner. Rows never mix results, so a
  // page append always continues the search this row is actually showing.
  const loadDirectory = useCallback(async (
    modelId: string,
    search: string,
    options: { append?: boolean } = {},
  ) => {
    const append = options.append === true;
    if (activeRequest.current) return;
    directoryOwners.current.get(modelId)?.abort();
    const controller = new AbortController();
    directoryOwners.current.set(modelId, controller);
    const version = (directoryVersions.current.get(modelId) ?? 0) + 1;
    directoryVersions.current.set(modelId, version);
    const owned = () => directoryVersions.current.get(modelId) === version
      && !controller.signal.aborted;
    const pageBefore = directoryByModel[modelId]?.page ?? [];
    patchDirectory(modelId, {
      query: search, mode: "loading", error: null,
      ...(!append ? { page: [], total: 0 } : {}),
    });
    try {
      const page = await companyEmployeesApi.list({
        search: search || undefined,
        offset: append ? pageBefore.length : 0,
        limit: DIRECTORY_PAGE_SIZE,
        signal: controller.signal,
      });
      if (!owned()) return;
      const incoming = page.employees ?? [];
      setDirectoryByModel((current) => {
        const previous = current[modelId] ?? EMPTY_DIRECTORY;
        const nextPage = append ? mergeDirectory(previous.page, incoming) : incoming;
        const nextNames = new Map(previous.names);
        for (const user of incoming) nextNames.set(user.work_id, user);
        return {
          ...current,
          [modelId]: {
            query: search,
            mode: "ready",
            page: nextPage,
            names: nextNames,
            total: page.total ?? incoming.length,
            error: null,
          },
        };
      });
    } catch (caught) {
      if (!owned()) return;
      // A failure stays retryable; it is never cached as a successful empty page.
      patchDirectory(modelId, { mode: "error", error: errorMessage(caught) });
    }
  }, [directoryByModel, patchDirectory]);

  const resetDirectoryOwners = useCallback(() => {
    for (const controller of directoryOwners.current.values()) controller.abort();
    directoryOwners.current.clear();
    directoryVersions.current.clear();
  }, []);

  useEffect(() => {
    if (canManage) void load();
    else {
      setState(null); setBaseUrl(""); setCredential(""); setDraft([]);
      setCatalogReceipt(null); setTestState("untested"); setBusy(null);
      setDirectoryByModel({});
      resetDirectoryOwners();
    }
    return () => {
      abandonRequest();
      connectionVersion.current += 1;
      resetDirectoryOwners();
    };
  }, [canManage, load, abandonRequest, resetDirectoryOwners]);

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
    setDraft((current) => current.map((model) => model.id === id
      ? { ...model, ...change }
      : change.is_default ? { ...model, is_default: false } : model));
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
      if (!model.enabled && model.max_input_tokens === undefined
        && model.max_output_tokens === undefined) return false;
      return !validTokenLimit(model.max_input_tokens) || !validTokenLimit(model.max_output_tokens);
    });
    if (invalid) {
      setPublishError(invalid.enabled
        ? `请填写 ${invalid.label} 的最大输入和输出 Token（1–10,000,000）。`
        : `请填写 ${invalid.label}（${invalid.value}）的最大输入和输出 Token（1–10,000,000 的整数），或将两项同时留空。`);
      return;
    }
    if (enabled.filter((model) => model.is_default).length !== 1) {
      setPublishError("请在已启用模型中指定唯一默认模型。"); return;
    }
    if (available.some((model) => model.label.trim().length > 160)) {
      setPublishError("平台显示名称最多 160 个字符。"); return;
    }
    // Every published row is sent, so the server's non-empty rule applies to
    // disabled rows too; validate the same set here instead of only enabled ones.
    const emptySelection = available.find((model) =>
      (model.access_mode ?? "all") === "selected"
      && (model.allowed_user_ids ?? []).length === 0);
    if (emptySelection) {
      setPublishError(
        `请为 ${emptySelection.label} 至少选择一名用户，或改为全部已认证用户。`,
      );
      return;
    }
    const owner = startRequest("publish");
    if (!owner) return;
    setError(null); setMessage(null);
    try {
      const next = await modelAdminApi.publish(
        baseUrl.trim(), credential.trim() || undefined, catalogReceipt.revision,
        available.map((model) => ({
          ...model,
          display_name: model.label.trim(),
          access_mode: model.access_mode ?? "all",
          allowed_user_ids:
            (model.access_mode ?? "all") === "selected" ? model.allowed_user_ids ?? [] : [],
        })),
        { signal: owner.controller.signal },
      );
      if (!ownsRequest(owner)) return;
      applyState(next);
      setTestState("passed");
      setMessage("模型配置已保存并生效；用户重新加载聊天页面后获取最新列表。");
    } catch (caught) {
      if (!ownsRequest(owner)) return;
      if (caught instanceof ApiRequestError && caught.status === 409) setCatalogReceipt(null);
      setPublishError(errorMessage(caught));
    } finally { finishRequest(owner); }
  };

  const enabledCount = draft.filter((model) => model.enabled).length;
  const visibleDraft = draft.filter((model) => {
    const normalizedQuery = query.trim().toLowerCase();
    const matchesQuery = !normalizedQuery
      || model.label.toLowerCase().includes(normalizedQuery)
      || model.value.toLowerCase().includes(normalizedQuery);
    const matchesStatus = statusFilter === "all"
      || (statusFilter === "enabled" && model.enabled)
      || (statusFilter === "disabled" && !model.enabled)
      || (statusFilter === "unavailable" && !model.available);
    return matchesQuery && matchesStatus;
  });

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
          <h2 className="text-sm font-semibold">模型与默认项</h2>
          <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{catalogReceipt ? "编辑启用状态、默认模型和容量；获取候选会保留已有编辑。" : "连接已修改，请获取当前地址的候选模型后再保存。"}</p>
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

        <div className="min-w-0 overflow-x-auto lg:min-h-0 lg:flex-1 lg:overflow-auto" data-model-admin-table-scroll>
          <BaseRadioGroup
          aria-label="默认模型" name="default-model"
          value={draft.find((model) => model.is_default)?.id ?? null}
          onValueChange={(id) => updateDraft(id as string, { is_default: true })}
        >
        <Table
 size="small" pagination={false} scroll={{ x: 1200 }} tableLayout="fixed" sticky
 dataSource={visibleDraft}
 rowKey={(model) => model.id}
 onRow={(model) => ({ "data-model-admin-model": model.id } as HTMLAttributes<HTMLTableRowElement>)}
 columns={[
{ key: "enabled", width: 70, title: "启用",
render: (_value, model) => (<>
<label className="inline-flex cursor-pointer items-center">
                      <SwitchRoot
                        aria-label={`启用 ${model.label}`}
                        checked={model.enabled}
                        disabled={!model.available || busy !== null}
                        onCheckedChange={(checked) => updateDraft(model.id, {
                          enabled: checked,
                          ...(!checked ? { is_default: false } : {}),
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
                      </div>
                    </div>
</>) },
{ key: "access", width: 260, title: "可用用户",
render: (_value, model) => (
  <ModelUserAccess
    modelId={model.id}
    accessMode={model.access_mode ?? "all"}
    allowedUserIds={model.allowed_user_ids ?? []}
    directory={directoryByModel[model.id]?.page ?? []}
    directoryNames={directoryByModel[model.id]?.names ?? EMPTY_DIRECTORY.names}
    directoryStatus={directoryByModel[model.id]?.mode ?? "idle"}
    directoryQuery={directoryByModel[model.id]?.query ?? ""}
    directoryTotal={directoryByModel[model.id]?.total ?? 0}
    directoryError={directoryByModel[model.id]?.error ?? null}
    disabled={busy !== null}
    onDirectoryQuery={(search, options) => { void loadDirectory(model.id, search, options); }}
    onChange={(mode, userIds) => updateDraft(model.id, {
      access_mode: mode, allowed_user_ids: mode === "selected" ? userIds : [],
    })}
  />
) },
{ key: "inputTokens", width: 160, title: "最大输入 Token",
render: (_value, model) => (<>
<Input
                      aria-label={`${model.value} 最大输入 Token`}
                      size="small"
                      disabled={!model.available || busy !== null}
                      min={1}
                      max={10000000}
                      onChange={(event) => updateDraft(model.id, { max_input_tokens: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder={model.enabled ? "必填" : "可同时留空"}
                      aria-required={model.enabled}
                      type="number"
                      value={model.max_input_tokens ?? ""}
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
{ key: "default", width: 70, title: "默认",
render: (_value, model) => (<>
<Radio
                      aria-label={`设为默认 ${model.label}`}
                      value={model.id}
                      disabled={!model.enabled || !model.available || busy !== null}
                    />
</>) }
]}
 emptyText={draft.length ? "没有匹配的模型" : busy === "load" ? "正在加载模型配置" : "请先获取候选模型"}
/></BaseRadioGroup>
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
    </section>
  );
}
