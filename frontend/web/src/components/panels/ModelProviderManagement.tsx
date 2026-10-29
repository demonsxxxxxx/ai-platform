import { Button, Input } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { modelAdminApi, type AdminModelProvider, type ModelProviderImpact } from "../../services/api/modelAdmin";
import { ApiRequestError } from "../../services/api/fetch";
import { useModelCatalogContext } from "../../contexts/ModelCatalogContext";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
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
    setSelectedId((current) => next.some((item) => item.id === current) ? current : next[0]?.id ?? "");
  }, []);
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
  const inspectDelete = async (onlyMissing = false) => {
    if (busy || !selectedId) return;
    setBusy(true); setError(null);
    try {
      const result = await modelAdminApi.providerImpact(selectedId, { signal: owner.current?.signal, missingOnly: onlyMissing });
      if (!owner.current?.signal.aborted) { setImpact(result); setMissingOnly(onlyMissing); }
    } catch (caught) { if (!owner.current?.signal.aborted) report(caught); }
    finally { if (!owner.current?.signal.aborted) setBusy(false); }
  };
  const remove = async () => {
    if (!impact || busy) return;
    setBusy(true); setError(null);
    try {
      await modelAdminApi.retireProvider(impact.id, impact.revision, { signal: owner.current?.signal, missingOnly });
      if (owner.current?.signal.aborted) return;
      setImpact(null); setDirty(false); setEditorVersion((version) => version + 1);
      await reload(owner.current?.signal);
      await refreshModels({ force: true });
    } catch (caught) { if (!owner.current?.signal.aborted) { setImpact(null); report(caught); } }
    finally { if (!owner.current?.signal.aborted) setBusy(false); }
  };
  const blocked = impact && (impact.group_count > 0 || impact.default_group_count > 0 || impact.active_run_count > 0);
  const providerChanged = useCallback(() => { void reload(owner.current?.signal).catch(report); }, [reload]);
  if (!canManage) return null;
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col lg:h-full" data-model-provider-management>
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-[var(--theme-border)] p-4">
      <LobeSelectField aria-label="模型供应商" className="w-full sm:w-64" value={selectedId} disabled={busy || editorBusy} options={providers.map((item) => ({ value: item.id, label: item.name }))}
        onChange={(id) => { if (dirty) setSwitchTo(id); else { setSelectedId(id); setDirty(false); setImpact(null); setMissingOnly(false); setError(null); } }} />
      <Button onClick={() => setAdding(!adding)} disabled={busy || editorBusy || dirty}>新增供应商</Button>
      <Button danger onClick={() => void inspectDelete()} disabled={busy || editorBusy || !selectedId}>删除供应商</Button>
      <Button onClick={() => void inspectDelete(true)} disabled={busy || editorBusy || !selectedId}>清理上游已移除模型</Button>
      {adding ? <div className="flex w-full flex-wrap items-center gap-2">
        <Input aria-label="供应商名称" placeholder="供应商名称" value={name} maxLength={160} onChange={(event) => setName(event.target.value)} disabled={busy} />
        <Button type="primary" disabled={busy || !name.trim()} onClick={() => void add()}>创建供应商</Button>
        <Button disabled={busy} onClick={() => { setAdding(false); setName(""); }}>取消新增</Button>
      </div> : null}
      {error ? <p role="alert" className="w-full text-sm text-[var(--theme-danger-text)]">{error} <Button size="small" onClick={() => void reload(owner.current?.signal).catch(report)}>重新读取</Button></p> : null}
      {impact && blocked ? <div role="alert" className="w-full text-sm" data-provider-delete-blocked>
        {impact.name}：{impact.model_count} 个模型，关联 {impact.group_count} 个分组、{impact.default_group_count} 个默认模型，{impact.active_run_count} 个未完成任务。
        请先在分组管理中移出模型并重新指定默认模型，等待任务结束；不会自动切换供应商。
        <Button size="small" onClick={() => setImpact(null)}>关闭检查结果</Button>
      </div> : null}
    </div>
    {selectedId ? <ModelAdminControl key={`${selectedId}:${editorVersion}`} providerId={selectedId} onDirtyChange={setDirty} onBusyChange={setEditorBusy} onProviderChanged={providerChanged} groupRevision={groupRevision} onGroupsChanged={onGroupsChanged} onStateChange={onStateChange} />
      : <p className="p-4 text-sm">暂无供应商，请新增供应商后填写连接并获取模型。</p>}
    <ConfirmDialog isOpen={Boolean(impact && !blocked)} title={missingOnly ? "清理旧模型配置" : "删除供应商"} message={missingOnly ? `清理 ${impact?.name ?? ""} 中上游已移除的 ${impact?.model_count ?? 0} 个模型？连接与其它模型不变，历史任务保留；再次从该供应商发现时可重新添加。` : `删除 ${impact?.name ?? ""} 及其 ${impact?.model_count ?? 0} 个模型配置？历史任务与用量记录保留；当前页面未保存的修改将放弃。`} confirmText={missingOnly ? "确认清理" : "删除供应商"} loading={busy} onCancel={() => setImpact(null)} onConfirm={() => void remove()} />
    <ConfirmDialog isOpen={switchTo !== null} title="切换供应商" message="切换会放弃当前供应商未保存的修改，是否继续？" confirmText="放弃修改并切换" onCancel={() => setSwitchTo(null)} onConfirm={() => { setSelectedId(switchTo!); setSwitchTo(null); setDirty(false); setImpact(null); setMissingOnly(false); setError(null); }} />
  </div>;
}
