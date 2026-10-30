import { Button, Input, toast } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { modelAdminApi, type AdminModelProvider, type ModelProviderImpact } from "../../services/api/modelAdmin";
import { ApiRequestError } from "../../services/api/fetch";
import { useModelCatalogContext } from "../../contexts/ModelCatalogContext";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { ModelAdminControl, type ModelAdminControlState } from "./ModelAdminControl";

export function ModelProviderManagement({ canManage = true, groupRevision, onGroupsChanged, onStateChange }: {
  canManage?: boolean; groupRevision: number; onGroupsChanged: () => void;
  onStateChange: (state: ModelAdminControlState) => void;
}) {
  const { refreshModels } = useModelCatalogContext();
  const [providers, setProviders] = useState<AdminModelProvider[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [editorBusy, setEditorBusy] = useState(false);
  const [switchTo, setSwitchTo] = useState<string | null>(null);
  const [impact, setImpact] = useState<ModelProviderImpact | null>(null);
  const [missingOnly, setMissingOnly] = useState(false);
  const [editorVersion, setEditorVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const owner = useRef<AbortController | null>(null);
  const reload = useCallback(async (signal?: AbortSignal) => {
    const state = await modelAdminApi.get({ signal });
    if (signal?.aborted) return;
    const next = state.providers ?? [];
    setProviders(next);
    setSelectedId((current) => next.some((item) => item.id === current) ? current : "");
    onStateChange("ready");
  }, [onStateChange]);
  useEffect(() => {
    const controller = new AbortController(); owner.current = controller;
    void reload(controller.signal).catch(() => { if (!controller.signal.aborted) setError("无法读取供应商，请重试。"); });
    return () => controller.abort();
  }, [reload]);
  const report = (caught: unknown) => setError(caught instanceof ApiRequestError && caught.code === "model_provider_in_use"
    ? "供应商仍有关联分组、默认模型或未完成任务，请先处理后重新检查。"
    : caught instanceof ApiRequestError && caught.status === 409
      ? "配置已变化，请重新读取影响后再删除。" : "操作失败，请重试。");
  const add = async () => {
    if (!name.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      const created = await modelAdminApi.createProvider(name.trim(), { signal: owner.current?.signal });
      if (owner.current?.signal.aborted) return;
      await reload(owner.current?.signal);
      setSelectedId(created.id); setDirty(false); setName(""); setAdding(false);
    } catch (caught) { if (!owner.current?.signal.aborted) report(caught); }
    finally { if (!owner.current?.signal.aborted) setBusy(false); }
  };
  const inspectDelete = async (onlyMissing = false, providerId = selectedId) => {
    if (busy || !providerId) return;
    setBusy(true); setError(null);
    try {
      const result = await modelAdminApi.providerImpact(providerId, { signal: owner.current?.signal, missingOnly: onlyMissing });
      if (!owner.current?.signal.aborted) { setImpact(result); setMissingOnly(onlyMissing); }
    } catch (caught) { if (!owner.current?.signal.aborted) report(caught); }
    finally { if (!owner.current?.signal.aborted) setBusy(false); }
  };
  const remove = async () => {
    if (!impact || busy) return;
    setBusy(true); setError(null);
    try {
      const result = await modelAdminApi.retireProvider(impact.id, impact.revision, { signal: owner.current?.signal, missingOnly });
      if (owner.current?.signal.aborted) return;
      toast.success(missingOnly ? `已清理 ${result.model_count ?? 0} 个模型，保留 ${result.retained_count ?? 0} 个被引用模型。` : `已删除供应商 ${impact.name}。`);
      setImpact(null); setDirty(false);
      if (missingOnly && selectedId === impact.id) setEditorVersion((version) => version + 1);
      await reload(owner.current?.signal);
      await refreshModels({ force: true });
    } catch (caught) { if (!owner.current?.signal.aborted) { setImpact(null); report(caught); } }
    finally { if (!owner.current?.signal.aborted) setBusy(false); }
  };
  const blocked = impact && (missingOnly ? impact.cleanable_count === 0 : impact.group_count > 0 || impact.default_group_count > 0 || impact.active_run_count > 0);
  const switchProvider = (id: string) => {
    if (dirty) { setSwitchTo(id); return; }
    setSelectedId(id); setDirty(false); setImpact(null); setMissingOnly(false); setError(null);
  };
  const providerChanged = useCallback(() => { void reload(owner.current?.signal).catch(report); }, [reload]);
  if (!canManage) return null;
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4 lg:h-full lg:overflow-y-auto" data-model-provider-management>
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
      <h2 className="text-sm text-[var(--theme-text-secondary)]">{providers.length} 个供应商</h2>
      <Button onClick={() => setAdding(!adding)} disabled={busy || editorBusy || dirty}>新增供应商</Button>
      {adding ? <div className="flex w-full flex-wrap items-center gap-2">
        <Input aria-label="供应商名称" placeholder="供应商名称" value={name} maxLength={160} onChange={(event) => setName(event.target.value)} disabled={busy} />
        <Button type="primary" disabled={busy || !name.trim()} onClick={() => void add()}>创建供应商</Button>
        <Button disabled={busy} onClick={() => { setAdding(false); setName(""); }}>取消新增</Button>
      </div> : null}
      {error ? <p role="alert" className="w-full text-sm text-[var(--theme-danger-text)]">{error} <Button size="small" onClick={() => void reload(owner.current?.signal).catch(report)}>重新读取</Button></p> : null}
    </div>
    {impact ? <div role={blocked ? "alert" : "status"} className="shrink-0 rounded-lg border border-[var(--theme-border)] p-3 text-sm" data-provider-delete-blocked={blocked ? "" : undefined}>
      <p>{impact.name}：{impact.model_count} 个模型，关联 {impact.group_count} 个分组、{impact.default_group_count} 个默认模型，{impact.active_run_count} 个未完成任务。</p>
      {missingOnly ? <p>可清理 {impact.cleanable_count} 个；保留 {impact.blocked_models.length} 个被引用模型。连接与其它模型不变。</p> : null}
      {impact.blocked_models.map((model) => <p key={model.id} className="mt-2">{model.label}：{model.groups.length ? `分组 ${model.groups.map((group) => group.name).join("、")}` : model.group_count ? "其它租户分组引用" : ""}{model.default_group_count ? ` · ${model.default_group_count} 个默认模型引用` : ""}{model.active_run_count ? ` · ${model.active_run_count} 个未完成任务` : ""}</p>)}
      {blocked ? <p className="mt-2">{missingOnly && !impact.model_count ? "没有需要清理的模型。" : "请先处理以上引用；不会自动移除分组授权或切换供应商。"}</p> : null}
      <Button size="small" onClick={() => setImpact(null)}>关闭检查结果</Button>
    </div> : null}
    {providers.map((provider) => <section key={provider.id} className={`flex shrink-0 flex-col overflow-hidden rounded-xl border border-[var(--theme-border)] ${selectedId === provider.id ? "min-h-[32rem] lg:flex-1" : ""}`} data-model-provider-row={provider.id}>
      <div className="flex shrink-0 flex-wrap items-center gap-2 bg-[var(--theme-bg-sidebar)] px-3 py-2" data-provider-header>
        <Button type="text" className="min-w-0 basis-full sm:flex-1 sm:basis-auto" aria-label={`供应商 ${provider.name}`} aria-expanded={selectedId === provider.id} aria-controls={selectedId === provider.id ? `provider-editor-${provider.id}` : undefined} disabled={busy || editorBusy} onClick={() => switchProvider(selectedId === provider.id ? "" : provider.id)}>
          <span className="flex min-w-0 flex-1 flex-col gap-1 text-left sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
            <span className="truncate font-medium" title={provider.name}>{provider.name}</span>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--theme-text-secondary)]" data-provider-summary>
              <span>{(provider.configured ?? provider.revision !== null) ? "连接已保存" : "待配置连接"}</span>
              <span>{provider.model_count} 模型</span>
              {provider.enabled_model_count !== undefined ? <span>启用 {provider.enabled_model_count}</span> : null}
              {provider.missing_model_count ? <span className="text-[var(--theme-warning-text)]">上游缺失 {provider.missing_model_count}</span> : null}
            </span>
          </span>
        </Button>
        <div className="ml-auto flex shrink-0 items-center gap-2">
        <Button size="small" disabled={busy || editorBusy || dirty} onClick={() => void inspectDelete(true, provider.id)}>清理上游已移除模型</Button>
        <Button danger type="text" aria-label={`删除供应商 ${provider.name}`} title="删除供应商" icon={<Trash2 size={16} />} disabled={busy || editorBusy || dirty} onClick={() => void inspectDelete(false, provider.id)} />
        <Button type="text" aria-label={`${selectedId === provider.id ? "收起" : "展开"}供应商 ${provider.name}`} aria-expanded={selectedId === provider.id} disabled={busy || editorBusy} onClick={() => switchProvider(selectedId === provider.id ? "" : provider.id)} icon={selectedId === provider.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />} />
        </div>
      </div>
      {selectedId === provider.id ? <div id={`provider-editor-${provider.id}`} className="flex min-h-0 flex-1 flex-col" data-provider-expanded>
        <ModelAdminControl key={`${selectedId}:${editorVersion}`} providerId={selectedId} onDirtyChange={setDirty} onBusyChange={setEditorBusy} onProviderChanged={providerChanged} groupRevision={groupRevision} onGroupsChanged={onGroupsChanged} onStateChange={onStateChange} />
      </div> : null}
    </section>)}
    {!providers.length ? <p className="text-sm">暂无供应商，请新增供应商后填写连接并获取模型。</p> : null}
    <ConfirmDialog isOpen={Boolean(impact && !blocked)} title={missingOnly ? "清理旧模型配置" : "删除供应商"} message={missingOnly ? `清理 ${impact?.name ?? ""} 中 ${impact?.cleanable_count ?? 0} 个无引用的缺失模型，保留 ${impact?.blocked_models.length ?? 0} 个被引用模型？${impact?.blocked_models.map((model) => `${model.label}：${model.groups.length ? `分组 ${model.groups.map((group) => group.name).join("、")}` : "其它引用"}${model.active_run_count ? `，${model.active_run_count} 个未完成任务` : ""}`).join("；") ?? ""}。历史任务保留；再次从该供应商发现时可重新添加。` : `删除 ${impact?.name ?? ""} 及其 ${impact?.model_count ?? 0} 个模型配置？历史任务与用量记录保留；当前页面未保存的修改将放弃。`} confirmText={missingOnly ? "确认清理" : "删除供应商"} loading={busy} onCancel={() => setImpact(null)} onConfirm={() => void remove()} />
    <ConfirmDialog isOpen={switchTo !== null} title="切换供应商" message="切换或收起会放弃当前供应商未保存的修改，是否继续？" confirmText="放弃修改并切换" onCancel={() => setSwitchTo(null)} onConfirm={() => { setSelectedId(switchTo!); setSwitchTo(null); setDirty(false); setImpact(null); setMissingOnly(false); setError(null); }} />
  </div>;
}
