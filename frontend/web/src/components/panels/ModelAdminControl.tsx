import { useCallback, useEffect, useRef, useState } from "react";
import { DatabaseZap, RefreshCw, Save, Search, PlugZap, RotateCcw } from "lucide-react";
import {
  modelAdminApi,
  type AdminModelEntry,
  type AdminModelState,
} from "../../services/api/modelAdmin";
import { ApiRequestError } from "../../services/api/fetch";

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
  model_catalog_revision_conflict: "模型配置已被更新，请重新获取后再发布。",
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
  ]));
}

function mergeModelCandidates(candidates: AdminModelEntry[], draft: AdminModelEntry[]): AdminModelEntry[] {
  return candidates.map((candidate) => {
    const previous = draft.find((model) => model.id === candidate.id && model.value === candidate.value);
    return previous ? {
      ...candidate,
      label: previous.label, enabled: previous.enabled, is_default: previous.is_default,
      max_input_tokens: previous.max_input_tokens, max_output_tokens: previous.max_output_tokens,
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
    return () => { abandonRequest(); connectionVersion.current += 1; };
  }, [canManage, load, abandonRequest]);

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
        setDraft(mergeModelCandidates(result.models, draft));
        setCatalogReceipt({ connectionVersion: owner.connectionVersion, revision: result.connection.revision });
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
    const invalid = enabled.find((model) => !validTokenLimit(model.max_input_tokens)
      || !validTokenLimit(model.max_output_tokens));
    if (invalid) {
      setPublishError(`请填写 ${invalid.label} 的最大输入和输出 Token（1–10,000,000）。`); return;
    }
    if (enabled.filter((model) => model.is_default).length !== 1) {
      setPublishError("请在已启用模型中指定唯一默认模型。"); return;
    }
    if (available.some((model) => !model.label.trim() || model.label.trim().length > 160)) {
      setPublishError("模型显示名称需为 1–160 个字符。"); return;
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
          <button
            className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 text-xs text-[var(--theme-text-secondary)] hover:bg-[var(--theme-hover)] disabled:opacity-50"
            data-model-admin-reload
            disabled={connectionLocked}
            onClick={() => void load()}
            type="button"
          ><RefreshCw size={14} aria-hidden="true" />重新加载已保存配置</button>
        </div>
        <div className="grid min-w-0 gap-3 md:grid-cols-2">
          <label className="flex min-w-0 flex-col gap-1.5 text-sm">
            <span className="font-medium">API 地址</span>
            <input
              aria-label="模型 API 地址"
              aria-describedby="model-address-help"
              className="h-10 min-w-0 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-3 outline-none focus:border-[var(--theme-primary)] disabled:opacity-60"
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
            <input
              aria-label="模型 API Key"
              aria-describedby="model-key-help"
              autoComplete="new-password"
              className="h-10 min-w-0 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-3 outline-none focus:border-[var(--theme-primary)] disabled:opacity-60"
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
          <span className={`inline-flex items-center gap-2 rounded-md px-2 py-1 text-xs ${
            testState === "passed" ? "bg-[var(--theme-success-soft)] text-[var(--theme-success)]"
              : testState === "failed" ? "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)]"
                : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"
          }`} role="status" data-model-admin-test-state={testState}>
            {busy === "load" ? "正在加载配置" : testState === "testing" ? "正在测试当前连接"
              : testState === "passed" ? "当前连接测试通过" : testState === "failed" ? "当前连接测试失败"
                : state?.connection.configured && reuseSavedCredential && !credential ? "已配置 · 尚未测试" : "连接草稿 · 尚未测试"}
          </span>
          <div className="flex flex-wrap gap-2">
            <button
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-[var(--theme-border)] px-3 text-sm hover:bg-[var(--theme-hover)] disabled:opacity-50"
              data-model-admin-test
              disabled={busy !== null || !connectionReady}
              onClick={() => void discover("test")}
              type="button"
            ><PlugZap size={16} aria-hidden="true" />测试连接</button>
            <button
              className="btn-primary inline-flex h-10 items-center justify-center gap-2"
              data-model-admin-discover
              disabled={busy !== null || !connectionReady}
              onClick={() => void discover("discover")}
              type="button"
            ><RefreshCw className={busy === "discover" ? "animate-spin" : ""} size={16} aria-hidden="true" />获取候选模型</button>
          </div>
        </div>
        {error ? <p className="mt-3 text-sm text-[var(--theme-danger)]" role="alert">{error}</p> : null}
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
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]" size={16} aria-hidden="true" />
              <input
                aria-label="搜索模型"
                className="h-10 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] pl-9 pr-3 text-sm outline-none focus:border-[var(--theme-primary)]"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索模型名称或模型 ID"
                value={query}
              />
            </label>
            <select
              aria-label="筛选模型状态"
              className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-3 text-sm outline-none focus:border-[var(--theme-primary)] sm:w-40"
              onChange={(event) => setStatusFilter(event.target.value)}
              value={statusFilter}
            >
              {STATUS_FILTERS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
          <span className="shrink-0 text-xs text-[var(--theme-text-secondary)]">
            显示 {visibleDraft.length} / {draft.length} 个模型
          </span>
        </div>

        <div className="min-w-0 overflow-x-auto lg:min-h-0 lg:flex-1 lg:overflow-auto" data-model-admin-table-scroll>
          <table className="w-full min-w-[880px] table-fixed text-left text-sm">
            <thead className="sticky top-0 z-10 bg-[var(--theme-workbench-panel)] text-xs text-[var(--theme-text-secondary)]">
              <tr>
                <th className="w-24 px-4 py-3 font-medium">启用</th>
                <th className="w-[28%] px-4 py-3 font-medium">显示名称 / 上游模型 ID</th>
                <th className="w-40 px-4 py-3 font-medium">最大输入 Token</th>
                <th className="w-40 px-4 py-3 font-medium">最大输出 Token</th>
                <th className="w-28 px-4 py-3 font-medium">状态</th>
                <th className="w-20 px-4 py-3 text-center font-medium">默认</th>
              </tr>
            </thead>
            <tbody>
              {visibleDraft.map((model) => (
                <tr key={model.id} className="border-t border-[var(--theme-border)] first:border-t-0">
                  <td className="px-4 py-3">
                    <label className="inline-flex cursor-pointer items-center">
                      <input
                        aria-label={`启用 ${model.label}`}
                        checked={model.enabled}
                        className="peer sr-only"
                        disabled={!model.available || busy !== null}
                        onChange={(event) => updateDraft(model.id, {
                          enabled: event.target.checked,
                          ...(!event.target.checked ? { is_default: false } : {}),
                        })}
                        type="checkbox"
                      />
                      <span className="relative h-5 w-9 rounded-full bg-[var(--theme-border)] transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition-transform peer-checked:bg-[var(--theme-primary)] peer-checked:after:translate-x-4 peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--theme-primary)] peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-[var(--theme-workbench-panel)] peer-disabled:cursor-not-allowed peer-disabled:opacity-50" />
                    </label>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[#eef2ff] text-[#5967e8]" aria-hidden="true">
                        <DatabaseZap size={16} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <input
                          aria-label={`${model.value} 显示名称`}
                          className="h-7 w-full truncate border-0 bg-transparent p-0 font-medium outline-none focus:text-[var(--theme-primary)]"
                          onChange={(event) => updateDraft(model.id, { label: event.target.value })}
                          disabled={busy !== null}
                          value={model.label}
                        />
                        <p className="truncate text-xs text-[var(--theme-text-secondary)]">{model.value}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <input
                      aria-label={`${model.value} 最大输入 Token`}
                      className="h-9 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2 disabled:opacity-60"
                      disabled={!model.enabled || busy !== null}
                      min={1}
                      max={10000000}
                      onChange={(event) => updateDraft(model.id, { max_input_tokens: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder={model.enabled ? "必填" : "—"}
                      aria-required={model.enabled}
                      type="number"
                      value={model.max_input_tokens ?? ""}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <input
                      aria-label={`${model.value} 最大输出 Token`}
                      className="h-9 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2 disabled:opacity-60"
                      disabled={!model.enabled || busy !== null}
                      min={1}
                      max={10000000}
                      onChange={(event) => updateDraft(model.id, { max_output_tokens: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder={model.enabled ? "必填" : "—"}
                      aria-required={model.enabled}
                      type="number"
                      value={model.max_output_tokens ?? ""}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs ${
                      model.available
                        ? "bg-[var(--theme-success-soft)] text-[var(--theme-success)]"
                        : "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)]"
                    }`}>
                      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
                      {model.available ? "已发现" : "上游缺失"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <input
                      aria-label={`设为默认 ${model.label}`}
                      checked={model.is_default}
                      disabled={!model.enabled || !model.available || busy !== null}
                      name="default-model"
                      onChange={() => updateDraft(model.id, { is_default: true })}
                      type="radio"
                    />
                  </td>
                </tr>
              ))}
              {!visibleDraft.length ? (
                <tr>
                  <td className="px-4 py-12 text-center text-sm text-[var(--theme-text-secondary)]" colSpan={6}>
                    {draft.length ? "没有匹配的模型" : busy === "load" ? "正在加载模型配置" : "请先获取候选模型"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
      <div className="sticky bottom-0 z-10 flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 lg:static" data-model-admin-action-bar>
        {publishError ? (
          <p className="text-xs text-[var(--theme-danger)]" role="alert">{publishError}</p>
        ) : (
          <p className="text-xs text-[var(--theme-text-secondary)]" aria-live="polite">
            已启用 {enabledCount} / {draft.length} · {hasChanges ? "有未保存的修改，保存后生效" : "已生效配置"}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-[var(--theme-border)] px-3 text-sm hover:bg-[var(--theme-hover)] disabled:opacity-50"
            data-model-admin-cancel
            disabled={!state || connectionLocked || (!hasChanges && busy === null)}
            onClick={cancelDraft}
            type="button"
          ><RotateCcw size={16} aria-hidden="true" />放弃修改</button>
          <button
            className="btn-primary inline-flex h-10 items-center justify-center gap-2"
            data-model-admin-publish
            disabled={busy !== null || !canPublish}
            onClick={() => void publish()}
            type="button"
          ><Save size={16} aria-hidden="true" />{busy === "publish" ? "正在保存" : "保存并生效"}</button>
        </div>
      </div>
    </section>
  );
}
