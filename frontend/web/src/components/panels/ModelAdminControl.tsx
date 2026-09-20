import { useEffect, useState } from "react";
import { DatabaseZap, RefreshCw, Save, Search } from "lucide-react";
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
  model_connection_endpoint_must_be_origin: "API 地址只能包含协议、主机和端口。",
  model_connection_endpoint_forbidden: "该 API 地址未获平台网络策略授权。",
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

export function ModelAdminControl({ canManage = true }: { canManage?: boolean }) {
  const [state, setState] = useState<AdminModelState | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [draft, setDraft] = useState<AdminModelEntry[]>([]);
  const [discoveredRevision, setDiscoveredRevision] = useState<number | null>(null);
  const [discovered, setDiscovered] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const applyState = (next: AdminModelState) => {
    setState(next);
    setBaseUrl(next.connection.base_url || "");
    setDraft(next.models);
  };

  useEffect(() => {
    if (!canManage) return undefined;
    let current = true;
    void modelAdminApi.get().then((next) => {
      if (current) applyState(next);
    }).catch((caught) => {
      if (current) setError(errorMessage(caught));
    });
    return () => { current = false; };
  }, [canManage]);

  const discover = async () => {
    setBusy("discover");
    setDiscovered(false);
    setDiscoveredRevision(null);
    setError(null);
    setMessage(null);
    try {
      const result = await modelAdminApi.discover(baseUrl.trim(), credential || undefined);
      setBaseUrl(result.base_url);
      setDraft(result.models);
      setDiscoveredRevision(result.connection.revision);
      setDiscovered(true);
      setMessage(`已获取 ${result.models.length} 个模型；尚未发布给用户。`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const updateDraft = (id: string, change: Partial<AdminModelEntry>) => {
    setDraft((current) => current.map((model) => model.id === id
      ? { ...model, ...change }
      : change.is_default ? { ...model, is_default: false } : model));
    setMessage(null);
  };

  const publish = async () => {
    const enabled = draft.filter((model) => model.enabled);
    if (!discovered) {
      setError("请先获取当前上游模型，再发布配置。");
      return;
    }
    if (!enabled.length || enabled.filter((model) => model.is_default).length !== 1
      || enabled.some((model) => !validTokenLimit(model.max_input_tokens)
        || !validTokenLimit(model.max_output_tokens))) {
      setError("请启用至少一个模型，为启用模型填写输入和输出 Token 上限，并指定唯一默认模型。");
      return;
    }
    setBusy("publish");
    setError(null);
    try {
      const next = await modelAdminApi.publish(
        baseUrl.trim(), credential || undefined, discoveredRevision,
        draft.map((model) => ({ ...model, display_name: model.label.trim() })),
      );
      applyState(next);
      setCredential("");
      setDiscoveredRevision(null);
      setDiscovered(false);
      setMessage("已发布模型配置；用户重新加载聊天页面后获取最新列表。");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

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
      className="min-w-0 space-y-4 p-4"
      data-model-admin-control
    >
      <div className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4">
        <h2 className="mb-3 text-sm font-semibold">连接配置</h2>
        <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(16rem,1fr)_minmax(14rem,1fr)_auto_auto]">
          <label className="flex min-w-0 flex-col gap-1.5 text-sm">
            <span className="font-medium">API 地址</span>
            <input
              aria-label="模型 API 地址"
              className="h-10 min-w-0 rounded-md border border-[var(--theme-border)] bg-[var(--theme-background)] px-3 outline-none focus:border-[var(--theme-primary)]"
              onChange={(event) => {
                setBaseUrl(event.target.value);
                setDiscoveredRevision(null);
                setDiscovered(false);
              }}
              placeholder="https://gateway.example.com"
              value={baseUrl}
            />
          </label>
          <label className="flex min-w-0 flex-col gap-1.5 text-sm">
            <span className="font-medium">API Key</span>
            <input
              aria-label="模型 API Key"
              autoComplete="new-password"
              className="h-10 min-w-0 rounded-md border border-[var(--theme-border)] bg-[var(--theme-background)] px-3 outline-none focus:border-[var(--theme-primary)]"
              onChange={(event) => {
                setCredential(event.target.value);
                setDiscoveredRevision(null);
                setDiscovered(false);
              }}
              placeholder={state?.connection.configured ? "留空则保持当前 Key" : "输入 API Key"}
              type="password"
              value={credential}
            />
          </label>
          <div className="flex items-end">
            <span
              className={`inline-flex h-8 items-center gap-2 rounded-md px-3 text-xs font-medium ${
                discovered || state?.connection.configured
                  ? "bg-[var(--theme-success-soft)] text-[var(--theme-success)]"
                  : "bg-[var(--theme-background)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
              }`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
              {discovered ? "连接正常" : state?.connection.configured ? "已配置" : "未配置"}
            </span>
          </div>
          <button
            className="btn-primary mt-auto inline-flex h-10 items-center justify-center gap-2"
            data-model-admin-discover
            disabled={busy !== null || !baseUrl.trim()}
            onClick={() => void discover()}
            type="button"
          >
            <RefreshCw className={busy === "discover" ? "animate-spin" : ""} size={16} aria-hidden="true" />
            同步模型
          </button>
        </div>
        {error ? <p className="mt-3 text-sm text-[var(--theme-danger)]" role="alert">{error}</p> : null}
        {message ? <p className="mt-3 text-sm text-[var(--theme-text-secondary)]" role="status">{message}</p> : null}
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)]">
        <div className="flex flex-col gap-3 border-b border-[var(--theme-border)] p-3 md:flex-row md:items-center md:justify-between">
          <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row">
            <label className="relative min-w-0 sm:max-w-sm sm:flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]" size={16} aria-hidden="true" />
              <input
                aria-label="搜索模型"
                className="h-10 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-background)] pl-9 pr-3 text-sm outline-none focus:border-[var(--theme-primary)]"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索模型名称或模型 ID"
                value={query}
              />
            </label>
            <select
              aria-label="筛选模型状态"
              className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-background)] px-3 text-sm outline-none focus:border-[var(--theme-primary)] sm:w-40"
              onChange={(event) => setStatusFilter(event.target.value)}
              value={statusFilter}
            >
              {STATUS_FILTERS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
          <button
            className="btn-primary inline-flex h-10 items-center justify-center gap-2"
            data-model-admin-publish
            disabled={busy !== null || !discovered}
            onClick={() => void publish()}
            type="button"
          >
            <Save size={16} aria-hidden="true" />
            保存配置
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] table-fixed text-left text-sm">
            <thead className="bg-[var(--theme-background)] text-xs text-[var(--theme-text-secondary)]">
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
                      <span className="relative h-5 w-9 rounded-full bg-[var(--theme-border)] transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition-transform peer-checked:bg-[var(--theme-primary)] peer-checked:after:translate-x-4 peer-disabled:cursor-not-allowed peer-disabled:opacity-50" />
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
                          value={model.label}
                        />
                        <p className="truncate text-xs text-[var(--theme-text-secondary)]">{model.value}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <input
                      aria-label={`${model.value} 最大输入 Token`}
                      className="h-9 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-background)] px-2 disabled:opacity-60"
                      disabled={!model.enabled || busy !== null}
                      min={1}
                      max={10000000}
                      onChange={(event) => updateDraft(model.id, { max_input_tokens: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder="—"
                      type="number"
                      value={model.max_input_tokens ?? ""}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <input
                      aria-label={`${model.value} 最大输出 Token`}
                      className="h-9 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-background)] px-2 disabled:opacity-60"
                      disabled={!model.enabled || busy !== null}
                      min={1}
                      max={10000000}
                      onChange={(event) => updateDraft(model.id, { max_output_tokens: event.target.value ? Number(event.target.value) : undefined })}
                      placeholder="—"
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
                    {draft.length ? "没有匹配的模型" : "请先同步模型"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
