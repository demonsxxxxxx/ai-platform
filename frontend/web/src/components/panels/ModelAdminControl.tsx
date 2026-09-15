import { useEffect, useState } from "react";
import { RefreshCw, Save } from "lucide-react";

import {
  modelAdminApi,
  type AdminModelEntry,
  type AdminModelState,
} from "../../services/api/modelAdmin";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "模型配置操作失败";
}

function validTokenLimit(value: string): boolean {
  const parsed = Number(value);
  return /^\d+$/.test(value) && Number.isInteger(parsed) && parsed >= 1 && parsed <= 10_000_000;
}

export function ModelAdminControl({ canManage = true }: { canManage?: boolean }) {
  const [state, setState] = useState<AdminModelState | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [limits, setLimits] = useState<Record<string, { input: string; output: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const applyState = (next: AdminModelState) => {
    setState(next);
    setBaseUrl(next.connection.base_url || "");
    setLabels(Object.fromEntries(next.models.map((model) => [model.id, model.label])));
    setLimits(Object.fromEntries(next.models.map((model) => [model.id, {
      input: model.max_input_tokens?.toString() ?? "",
      output: model.max_output_tokens?.toString() ?? "",
    }])));
  };

  useEffect(() => {
    if (!canManage) return undefined;
    let current = true;
    void modelAdminApi
      .get()
      .then((next) => {
        if (current) applyState(next);
      })
      .catch((caught) => {
        if (current) setError(errorMessage(caught));
      });
    return () => {
      current = false;
    };
  }, [canManage]);

  const configure = async () => {
    setBusy("connection");
    setError(null);
    try {
      const next = await modelAdminApi.configure(
        baseUrl.trim(),
        credential.trim() ? credential : undefined,
      );
      applyState(next);
      setCredential("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const sync = async () => {
    setBusy("sync");
    setError(null);
    try {
      applyState(await modelAdminApi.sync());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const patchModel = async (
    model: AdminModelEntry,
    patch: {
      display_name?: string;
      enabled?: boolean;
      is_default?: boolean;
      max_input_tokens?: number;
      max_output_tokens?: number;
    },
  ) => {
    setBusy(model.id);
    setError(null);
    try {
      const updated = await modelAdminApi.patch(model.id, patch);
      setState((current) =>
        current
          ? {
              ...current,
              models: current.models.map((entry) =>
                entry.id === updated.id
                  ? updated
                  : updated.is_default
                    ? { ...entry, is_default: false }
                    : entry,
              ),
            }
          : current,
      );
      setLabels((current) => ({ ...current, [updated.id]: updated.label }));
      setLimits((current) => ({ ...current, [updated.id]: {
        input: updated.max_input_tokens?.toString() ?? "",
        output: updated.max_output_tokens?.toString() ?? "",
      } }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  if (!canManage) return null;

  return (
    <section
      aria-labelledby="model-admin-heading"
      className="border-b border-[var(--theme-border)] pb-6"
      data-model-admin-control
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="model-admin-heading" className="text-base font-semibold">模型连接</h2>
          <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">
            配置一个 OpenAI-compatible 地址；平台从 /v1/models 同步公共模型。
          </p>
        </div>
        <button
          data-model-admin-sync
          className="btn-secondary inline-flex items-center gap-2"
          onClick={() => void sync()}
          type="button"
        >
          <RefreshCw size={16} aria-hidden="true" />
          同步模型
        </button>
      </div>

      <div className="grid gap-3 md:grid-cols-[minmax(16rem,1fr)_minmax(14rem,0.8fr)_auto]">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">API 地址</span>
          <input
            aria-label="模型 API 地址"
            className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 outline-none focus:border-[var(--theme-primary)]"
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://gateway.example.com"
            value={baseUrl}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">API Key</span>
          <input
            aria-label="模型 API Key"
            autoComplete="new-password"
            className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 outline-none focus:border-[var(--theme-primary)]"
            onChange={(event) => setCredential(event.target.value)}
            placeholder={state?.connection.configured ? "留空则保持当前 Key" : "输入 API Key"}
            type="password"
            value={credential}
          />
        </label>
        <button
          data-model-admin-configure
          className="btn-primary mt-auto inline-flex h-10 items-center justify-center gap-2"
          onClick={() => void configure()}
          type="button"
        >
          <Save size={16} aria-hidden="true" />
          保存并同步
        </button>
      </div>

      {state?.connection.configured ? (
        <p className="mt-3 text-xs text-[var(--theme-text-secondary)]">
          当前 revision {state.connection.revision} · Key 指纹 {state.connection.key_fingerprint}
        </p>
      ) : null}
      {error ? <p className="mt-3 text-sm text-[var(--theme-danger)]" role="alert">{error}</p> : null}

      {state?.models.length ? (
        <div className="mt-6 overflow-x-auto border-t border-[var(--theme-border)]">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead className="text-[var(--theme-text-secondary)]">
              <tr>
                <th className="py-3 pr-3 font-medium">启用</th>
                <th className="py-3 pr-3 font-medium">显示名称</th>
                <th className="py-3 pr-3 font-medium">上游模型 ID</th>
                <th className="py-3 pr-3 font-medium">输入 / 输出 token 上限</th>
                <th className="py-3 pr-3 font-medium">状态</th>
                <th className="py-3 font-medium">默认</th>
              </tr>
            </thead>
            <tbody>
              {state.models.map((model) => (
                <tr key={model.id} className="border-t border-[var(--theme-border)]">
                  <td className="py-3 pr-3">
                    <input
                      aria-label={`启用 ${model.label}`}
                      checked={model.enabled}
                      disabled={!model.available || busy !== null}
                      onChange={(event) => void patchModel(model, { enabled: event.target.checked })}
                      type="checkbox"
                    />
                  </td>
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-2">
                      <input
                        aria-label={`${model.value} 显示名称`}
                        className="h-9 min-w-48 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2"
                        onChange={(event) => setLabels((current) => ({ ...current, [model.id]: event.target.value }))}
                        value={labels[model.id] ?? model.label}
                      />
                      <button
                        aria-label={`保存 ${model.value} 显示名称`}
                        className="btn-ghost p-2"
                        disabled={busy !== null || (labels[model.id] ?? model.label).trim() === model.label}
                        onClick={() => void patchModel(model, { display_name: (labels[model.id] ?? model.label).trim() })}
                        type="button"
                      >
                        <Save size={15} aria-hidden="true" />
                      </button>
                    </div>
                  </td>
                  <td className="break-all py-3 pr-3 font-mono text-xs">{model.value}</td>
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-2">
                      <input
                        aria-label={`${model.value} 输入 token 上限`}
                        className="h-9 w-24 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2"
                        min={1}
                        max={10000000}
                        onChange={(event) => setLimits((current) => ({ ...current, [model.id]: {
                          input: event.target.value,
                          output: current[model.id]?.output ?? "",
                        } }))}
                        type="number"
                        value={limits[model.id]?.input ?? ""}
                      />
                      <span aria-hidden="true">/</span>
                      <input
                        aria-label={`${model.value} 输出 token 上限`}
                        className="h-9 w-24 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2"
                        min={1}
                        max={10000000}
                        onChange={(event) => setLimits((current) => ({ ...current, [model.id]: {
                          input: current[model.id]?.input ?? "",
                          output: event.target.value,
                        } }))}
                        type="number"
                        value={limits[model.id]?.output ?? ""}
                      />
                      <button
                        aria-label={`保存 ${model.value} token 上限`}
                        className="btn-ghost p-2"
                        disabled={busy !== null || !validTokenLimit(limits[model.id]?.input ?? "")
                          || !validTokenLimit(limits[model.id]?.output ?? "")
                          || (Number(limits[model.id]?.input) === model.max_input_tokens
                            && Number(limits[model.id]?.output) === model.max_output_tokens)}
                        onClick={() => void patchModel(model, {
                          max_input_tokens: Number(limits[model.id]?.input),
                          max_output_tokens: Number(limits[model.id]?.output),
                        })}
                        type="button"
                      >
                        <Save size={15} aria-hidden="true" />
                      </button>
                    </div>
                  </td>
                  <td className="py-3 pr-3">{model.available ? "已发现" : "上游缺失"}</td>
                  <td className="py-3">
                    <input
                      aria-label={`设为默认 ${model.label}`}
                      checked={model.is_default}
                      disabled={!model.enabled || !model.available || busy !== null}
                      name="default-model"
                      onChange={() => void patchModel(model, { is_default: true })}
                      type="radio"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
