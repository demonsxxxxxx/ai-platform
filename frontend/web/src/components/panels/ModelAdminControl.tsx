import { useEffect, useState } from "react";
import { Download, Save } from "lucide-react";

import {
  modelAdminApi,
  type AdminModelEntry,
  type AdminModelState,
} from "../../services/api/modelAdmin";
import { ApiRequestError } from "../../services/api/fetch";

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

  if (!canManage) return null;

  return (
    <section aria-labelledby="model-admin-heading" className="min-w-0 border-b border-[var(--theme-border)] px-4 pb-6 pt-3" data-model-admin-control>
      <div className="mb-4">
        <h2 id="model-admin-heading" className="text-base font-semibold">全员模型配置</h2>
        <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">
          先获取候选模型，再配置启用状态、容量和默认模型；只有发布后新 Run 才使用此配置。
        </p>
      </div>
      <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(16rem,1fr)_minmax(14rem,0.8fr)_auto]">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">API 地址</span>
          <input aria-label="模型 API 地址" className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 outline-none focus:border-[var(--theme-primary)]" onChange={(event) => { setBaseUrl(event.target.value); setDiscoveredRevision(null); setDiscovered(false); }} placeholder="https://gateway.example.com" value={baseUrl} />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">API Key</span>
          <input aria-label="模型 API Key" autoComplete="new-password" className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 outline-none focus:border-[var(--theme-primary)]" onChange={(event) => { setCredential(event.target.value); setDiscoveredRevision(null); setDiscovered(false); }} placeholder={state?.connection.configured ? "留空则保持当前 Key" : "输入 API Key"} type="password" value={credential} />
        </label>
        <button data-model-admin-discover className="btn-secondary mt-auto inline-flex h-10 w-full items-center justify-center gap-2 lg:w-auto" disabled={busy !== null || !baseUrl.trim()} onClick={() => void discover()} type="button">
          <Download size={16} aria-hidden="true" />获取模型
        </button>
      </div>
      {state?.connection.configured ? <p className="mt-3 text-xs text-[var(--theme-text-secondary)]">当前发布版本 {state.connection.revision} · Key 指纹 {state.connection.key_fingerprint}</p> : null}
      {error ? <p className="mt-3 text-sm text-[var(--theme-danger)]" role="alert">{error}</p> : null}
      {message ? <p className="mt-3 text-sm text-[var(--theme-text-secondary)]" role="status">{message}</p> : null}
      {draft.length ? <div className="mt-6 overflow-x-auto border-t border-[var(--theme-border)]">
        <table className="w-full min-w-[1050px] text-left text-sm">
          <thead className="text-[var(--theme-text-secondary)]"><tr>
            <th className="py-3 pr-3 font-medium">启用</th><th className="py-3 pr-3 font-medium">显示名称</th><th className="py-3 pr-3 font-medium">上游模型 ID</th><th className="py-3 pr-3 font-medium">最大输入 Token</th><th className="py-3 pr-3 font-medium">最大输出 Token</th><th className="py-3 pr-3 font-medium">状态</th><th className="py-3 font-medium">默认</th>
          </tr></thead>
          <tbody>{draft.map((model) => <tr key={model.id} className="border-t border-[var(--theme-border)]">
            <td className="py-3 pr-3"><input aria-label={`启用 ${model.label}`} checked={model.enabled} disabled={!model.available || busy !== null} onChange={(event) => updateDraft(model.id, { enabled: event.target.checked, ...(!event.target.checked ? { is_default: false } : {}) })} type="checkbox" /></td>
            <td className="py-3 pr-3"><input aria-label={`${model.value} 显示名称`} className="h-9 min-w-48 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2" onChange={(event) => updateDraft(model.id, { label: event.target.value })} value={model.label} /></td>
            <td className="break-all py-3 pr-3 font-mono text-xs">{model.value}</td>
            <td className="py-3 pr-3"><input aria-label={`${model.value} 最大输入 Token`} className="h-9 w-32 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2" min={1} max={10000000} onChange={(event) => updateDraft(model.id, { max_input_tokens: event.target.value ? Number(event.target.value) : undefined })} type="number" value={model.max_input_tokens ?? ""} /></td>
            <td className="py-3 pr-3"><input aria-label={`${model.value} 最大输出 Token`} className="h-9 w-32 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2" min={1} max={10000000} onChange={(event) => updateDraft(model.id, { max_output_tokens: event.target.value ? Number(event.target.value) : undefined })} type="number" value={model.max_output_tokens ?? ""} /></td>
            <td className="py-3 pr-3">{model.available ? "已发现" : "上游缺失"}</td>
            <td className="py-3"><input aria-label={`设为默认 ${model.label}`} checked={model.is_default} disabled={!model.enabled || !model.available || busy !== null} name="default-model" onChange={() => updateDraft(model.id, { is_default: true })} type="radio" /></td>
          </tr>)}</tbody>
        </table>
      </div> : null}
      {draft.length ? <div className="mt-4 flex justify-end"><button data-model-admin-publish className="btn-primary inline-flex items-center gap-2" disabled={busy !== null || !discovered} onClick={() => void publish()} type="button"><Save size={16} aria-hidden="true" />发布到全员</button></div> : null}
    </section>
  );
}
