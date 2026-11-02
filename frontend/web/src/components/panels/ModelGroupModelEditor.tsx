import {
  Button,
  Checkbox,
  Input,
  SwitchRoot,
  SwitchThumb,
} from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Boxes, Info, Save, Search } from "lucide-react";
import type {
  AdminModelGroupDetails,
  AdminModelGroupModel,
} from "../../services/api/modelGroups";
import type {
  AdminModelEntry,
  AdminModelProvider,
} from "../../services/api/modelAdmin";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";

type ModelProvider = Pick<AdminModelProvider, "id" | "name">;

function providerLabel(model: AdminModelEntry, providersById: Map<string, string>): string {
  const name = model.provider_name || (model.provider_id ? providersById.get(model.provider_id) : undefined);
  const duplicateName = name && [...providersById.values()].filter((value) => value === name).length > 1;
  if (duplicateName && model.provider_id) return `${name} · ${model.provider_id}`;
  if (name) return name;
  if (model.provider_id) return model.provider_id;
  return "供应商来源未知";
}

function providerOptions(providers: ModelProvider[], catalog: AdminModelEntry[]) {
  const byId = new Map(providers.map((provider) => [provider.id, provider.name]));
  for (const model of catalog) {
    if (model.provider_id && !byId.has(model.provider_id)) {
      byId.set(model.provider_id, model.provider_name || model.provider_id);
    }
  }
  const duplicateNames = new Set<string>();
  const countByName = new Map<string, number>();
  for (const name of byId.values()) countByName.set(name, (countByName.get(name) ?? 0) + 1);
  for (const [name, count] of countByName) if (count > 1) duplicateNames.add(name);
  return [...byId].sort(([leftId], [rightId]) => leftId.localeCompare(rightId)).map(([id, name]) => ({
    value: id,
    label: duplicateNames.has(name) ? `${name} · ${id}` : name,
  }));
}

function modelStatus(model: AdminModelEntry): string[] {
  return [
    ...(!model.enabled ? ["目录已停用"] : []),
    ...(model.available === false ? ["上游缺失"] : []),
  ];
}

export function ModelGroupModelEditor({
  details,
  catalog,
  providers,
  busy,
  onSave,
  isDefault,
}: {
  details: AdminModelGroupDetails;
  catalog: AdminModelEntry[];
  providers: ModelProvider[];
  busy: boolean;
  onSave: (models: AdminModelGroupModel[], revision: number, defaultModelId: string | null) => Promise<void>;
  isDefault: boolean;
}) {
  const [models, setModels] = useState(details.models);
  const [defaultModelId, setDefaultModelId] = useState(details.default_model_id || "");
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [providerId, setProviderId] = useState("all");
  useEffect(() => {
    setModels(details.models);
    setDefaultModelId(details.default_model_id || "");
  }, [details.default_model_id, details.models]);

  const orderedModels = useMemo(() => [...models].sort((left, right) => left.display_order - right.display_order), [models]);
  const catalogById = useMemo(() => new Map(catalog.map((model) => [model.id, model])), [catalog]);
  const providersById = useMemo(() => new Map(providers.map((provider) => [provider.id, provider.name])), [providers]);
  const modelById = useMemo(() => new Map(models.map((model) => [model.model_id, model])), [models]);
  const unassigned = catalog.filter((model) => !modelById.has(model.id));
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchingModels = unassigned.filter((model) => {
    if (providerId !== "all" && model.provider_id !== providerId) return false;
    if (!normalizedQuery) return true;
    const source = providerLabel(model, providersById);
    const searchable = [model.id, model.value, model.label, model.provider_id, model.provider_name, model.provider, source]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(normalizedQuery);
  });
  const providerFilterOptions = useMemo(() => [
    { value: "all", label: "全部供应商" },
    ...providerOptions(providers, catalog),
  ], [providers, catalog]);
  const hasEnabledModels = orderedModels.some((model) => model.enabled);
  const hasValidDefault = orderedModels.some((model) => model.model_id === defaultModelId && model.enabled);
  const defaultProblem = hasEnabledModels && !hasValidDefault;
  const defaultMessage = defaultModelId && modelById.has(defaultModelId)
    ? "当前默认模型已停用，请手动选择一个启用的组内模型。"
    : "请选择一个启用的组内模型作为默认模型。";
  const renumber = (items: AdminModelGroupModel[]) => items.map((item, index) => ({ ...item, display_order: index }));
  const remove = (modelId: string) => {
    if (defaultModelId === modelId) setDefaultModelId("");
    setModels((current) => current.filter((entry) => entry.model_id !== modelId));
  };
  const toggle = (model: AdminModelEntry) => {
    setModels((current) => {
      if (current.some((entry) => entry.model_id === model.id)) {
        if (defaultModelId === model.id) setDefaultModelId("");
        return current.filter((entry) => entry.model_id !== model.id);
      }
      return [...current, {
        model_id: model.id,
        model_value: model.value,
        label: model.label,
        enabled: true,
        display_order: current.length,
        priority: 0,
        weight: 100,
      }];
    });
  };
  const move = (modelId: string, direction: -1 | 1) => {
    setModels((current) => {
      const ordered = [...current].sort((left, right) => left.display_order - right.display_order);
      const index = ordered.findIndex((item) => item.model_id === modelId);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= ordered.length) return current;
      [ordered[index], ordered[nextIndex]] = [ordered[nextIndex], ordered[index]];
      return renumber(ordered);
    });
  };
  const moveByDrop = (targetId: string) => {
    if (!draggedId || draggedId === targetId) return;
    setModels((current) => {
      const ordered = [...current].sort((left, right) => left.display_order - right.display_order);
      const from = ordered.findIndex((item) => item.model_id === draggedId);
      const to = ordered.findIndex((item) => item.model_id === targetId);
      if (from < 0 || to < 0) return current;
      const [moved] = ordered.splice(from, 1);
      ordered.splice(to, 0, moved);
      return renumber(ordered);
    });
    setDraggedId(null);
  };
  const update = (modelId: string, patch: Partial<AdminModelGroupModel>) => {
    if (patch.enabled === false && defaultModelId === modelId) setDefaultModelId("");
    setModels((current) => current.map((item) => item.model_id === modelId ? { ...item, ...patch } : item));
  };
  const renderAssigned = (entry: AdminModelGroupModel, index: number) => {
    const catalogEntry = catalogById.get(entry.model_id);
    const sourceLabel = catalogEntry ? providerLabel(catalogEntry, providersById) : "供应商来源不可用";
    return (
      <div
        key={entry.model_id}
        draggable={!busy}
        onDragStart={() => setDraggedId(entry.model_id)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={() => moveByDrop(entry.model_id)}
        className="grid items-center gap-2 border-b border-[var(--theme-border)] py-2 sm:grid-cols-[minmax(0,1fr)_6rem_8rem_5rem]"
        data-model-group-model={entry.model_id}
      >
        <div className="flex min-w-0 items-center gap-2">
          <Checkbox checked disabled={busy} aria-label={`移除 ${entry.label} ${entry.model_value} 供应商 ${sourceLabel}`} onChange={() => remove(entry.model_id)}>
            <span className="min-w-0"><span className="block truncate text-sm">{entry.label}</span><span className="block truncate text-xs text-[var(--theme-text-secondary)]" data-model-group-upstream-id>{entry.model_value} · 供应商 {sourceLabel}</span>
              {!catalogEntry ? <span className="block text-xs text-[var(--theme-warning-text)]" data-group-model-missing>目录中不可用 · 取消勾选并保存可移除</span> : null}
              {catalogEntry?.enabled === false ? <span className="block text-xs text-[var(--theme-warning-text)]" data-group-model-catalog-disabled>目录已停用</span> : null}
              {catalogEntry?.available === false ? <span className="block text-xs text-[var(--theme-warning-text)]" data-group-model-missing data-group-model-upstream-missing>上游缺失 · 取消勾选并保存可移除</span> : null}
            </span>
          </Checkbox>
        </div>
        <div className="flex items-center gap-1" aria-label={`调整 ${entry.label} 展示顺序`}>
          <Button type="text" size="small" aria-label={`上移 ${entry.label} ${entry.model_value} 供应商 ${sourceLabel}`} disabled={busy || index === 0} onClick={() => move(entry.model_id, -1)} icon={<ArrowUp size={14} />} />
          <Button type="text" size="small" aria-label={`下移 ${entry.label} ${entry.model_value} 供应商 ${sourceLabel}`} disabled={busy || index === orderedModels.length - 1} onClick={() => move(entry.model_id, 1)} icon={<ArrowDown size={14} />} />
        </div>
        <label className="flex items-center gap-2 text-xs text-[var(--theme-text-secondary)]"><input type="radio" name="model-group-default-model" aria-label={`设为默认模型 ${entry.label} ${entry.model_value} 供应商 ${sourceLabel}`} checked={defaultModelId === entry.model_id} disabled={busy || !entry.enabled} onChange={() => setDefaultModelId(entry.model_id)} />默认模型</label>
        <div className="flex items-center gap-2"><span className="text-xs sm:hidden">启用</span><SwitchRoot aria-label={`启用 ${entry.label} ${entry.model_value} 供应商 ${sourceLabel}`} checked={entry.enabled} disabled={busy} onCheckedChange={(enabled) => update(entry.model_id, { enabled })}><SwitchThumb /></SwitchRoot>{!entry.enabled ? <span className="text-xs text-[var(--theme-warning-text)]" data-group-model-disabled>组内已停用</span> : null}</div>
      </div>
    );
  };

  return (
    <section data-model-group-models className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 className="text-sm font-semibold">模型</h3><p className="text-xs text-[var(--theme-text-secondary)]">选择组内可用模型，并指定一个默认模型；用户仍可在聊天中手动切换。</p></div>
        <div className="flex flex-col items-end gap-1">
          <Button type="primary" loading={busy} disabled={busy || defaultProblem} htmlType="button" onClick={() => void onSave(renumber(orderedModels), details.revision, hasEnabledModels ? defaultModelId || null : null)} icon={<Save size={15} />}>保存模型</Button>
          {defaultProblem ? <p className="text-xs text-[var(--theme-warning-text)]" role="alert" data-model-group-default-error>{defaultMessage}</p> : null}
        </div>
      </div>
      {isDefault ? <div className="flex items-start gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-3 text-sm text-[var(--theme-text-secondary)]"><Info size={16} className="mt-0.5 shrink-0" /><span>普通默认组模型可供专家及独立业务入口使用；不可用于普通聊天，专家权限仍单独校验。</span></div> : null}
      <div className="min-h-0 overflow-auto rounded-lg border border-[var(--theme-border)]">
        {orderedModels.length ? <>
          <div className="hidden grid-cols-[minmax(0,1fr)_6rem_8rem_5rem] gap-2 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-2 text-xs text-[var(--theme-text-secondary)] sm:grid"><span>已加入模型</span><span>展示顺序</span><span>默认模型</span><span>启用</span></div>
          <div className="px-3">{orderedModels.map(renderAssigned)}</div>
        </> : <div className="flex min-h-64 flex-col items-center justify-center gap-3 px-6 py-10 text-center"><div className="flex size-12 items-center justify-center rounded-full bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"><Boxes size={24} /></div><div><p className="text-sm font-semibold">暂无已加入模型</p><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">展开下方“添加模型”选择模型，保存后生效。</p></div></div>}
        <details className="border-t border-[var(--theme-border)] px-3 py-3" data-model-group-add-models>
          <summary className="cursor-pointer text-sm font-medium">添加模型{catalog.length === 0 ? " · 目录为空" : unassigned.length === 0 ? " · 全部已加入" : ` · ${unassigned.length} 个可选`}</summary>
          {catalog.length === 0 ? <p className="mt-3 text-sm text-[var(--theme-text-secondary)]" data-model-group-catalog-empty>模型目录为空。</p> : null}
          {catalog.length > 0 && unassigned.length === 0 ? <p className="mt-3 text-sm text-[var(--theme-text-secondary)]" data-model-group-all-assigned>全部目录模型已加入本组。</p> : null}
          {unassigned.length > 0 ? <>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-1"><Input aria-label="搜索可添加模型" disabled={busy} prefix={<Search size={15} />} placeholder="名称、上游 ID 或供应商" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
              <LobeSelectField aria-label="筛选供应商" disabled={busy} className="w-full sm:w-48" value={providerId} onChange={setProviderId} options={providerFilterOptions} />
            </div>
            {matchingModels.length ? <div className="mt-3 max-h-56 space-y-1 overflow-auto">{matchingModels.map((model) => {
              const statuses = modelStatus(model);
              return <div key={model.id} className="flex items-center gap-2 border-b border-[var(--theme-border)] py-2 last:border-b-0" data-model-group-catalog-model={model.id}>
                <Checkbox checked={false} disabled={busy} onChange={() => toggle(model)} aria-label={`加入 ${model.label} ${model.value} 供应商 ${providerLabel(model, providersById)}`}><span className="min-w-0"><span className="block truncate text-sm">{model.label}</span><span className="block truncate text-xs text-[var(--theme-text-secondary)]">上游 ID：{model.value} · 供应商 {providerLabel(model, providersById)}</span>{statuses.map((status) => <span key={status} className="block text-xs text-[var(--theme-warning-text)]" data-group-model-catalog-status={status}>{status}</span>)}</span></Checkbox>
              </div>;
            })}</div> : <p className="mt-3 text-sm text-[var(--theme-text-secondary)]" data-model-group-no-match>没有匹配的可添加模型。</p>}
          </> : null}
        </details>
      </div>
    </section>
  );
}
