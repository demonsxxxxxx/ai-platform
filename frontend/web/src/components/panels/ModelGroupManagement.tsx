import {
  Button,
  Checkbox,
  DrawerBackdrop,
  DrawerContent,
  DrawerHeader,
  DrawerPopup,
  DrawerPortal,
  DrawerRoot,
  DrawerTitle,
  Input,
  ModalRoot, ModalPortal, ModalBackdrop, ModalPopup, ModalHeader, ModalTitle, ModalContent, ModalFooter,
  SwitchRoot,
  SwitchThumb,
  Table,
  Tag,
  TabsIndicator,
  TabsList,
  TabsPanel,
  TabsRoot,
  TabsTab,
  TextArea,
} from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Boxes, Edit3, Info, Plus, Save, Search, Trash2, Users, X } from "lucide-react";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ApiRequestError } from "../../services/api/fetch";
import {
  modelGroupsApi,
  type AdminModelGroupDetails,
  type AdminModelGroupMember,
  type AdminModelGroupModel,
  type AdminModelGroupSummary,
} from "../../services/api/modelGroups";
import { modelAdminApi, type AdminModelEntry } from "../../services/api/modelAdmin";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { ModelGroupMemberPicker } from "./ModelGroupMemberPicker";
const ERROR_COPY: Record<string, string> = {
  group_has_no_models: "该组暂无可用模型。",
  group_models_unavailable: "该组模型暂时无法连接上游。",
  group_disabled: "该组已停用，不能继续分配成员。",
  model_group_impact_confirmation_required: "该组包含成员、模型或运行，请先完成迁移。",
  model_group_active_runs: "该组还有运行中任务，请等待结束后重新打开删除。",
  model_group_migration_required: "请选择成员迁入组，或明确确认回普通默认组。",
  model_group_migration_choice_conflict: "转入业务组与回默认组不能同时选择。",
  model_group_migration_target_invalid: "目标组不可用，请重新选择有效业务组。",
  model_group_target_revision_conflict: "目标组已变化，请重新打开删除并确认。",
  model_group_target_revision_required: "请重新打开删除以获取目标组最新配置。",
  model_default_group_immutable: "普通默认组不可停用、归档或删除。",
  model_group_revision_conflict: "分组已被其他管理员更新，请重新加载。",
  model_policy_changed: "分组已被其他管理员更新，请重新加载。",
  model_group_single_membership_required: "每位用户只能归属一个模型组。",
  model_group_fallback_confirmation_required: "移除最后一个业务组需要确认回到默认组并失去普通聊天资格。",
  group_not_available: "分组不存在、已停用或不属于当前租户。",
  model_group_member_unknown_user: "成员身份无效或已停用。",
};

function errorText(error: unknown): string {
  if (error instanceof ApiRequestError && error.code && ERROR_COPY[error.code]) return ERROR_COPY[error.code];
  return error instanceof Error ? error.message : "模型分组操作失败，请重试。";
}

function availabilityCopy(group: AdminModelGroupSummary): string {
  if (!group.enabled) return "已停用";
  if (group.model_count === 0) return "未配置模型";
  return "已启用";
}

function boundedInteger(value: string, minimum: number, maximum: number, previous: number): number {
  if (!value.trim()) return previous;
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) return previous;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function GroupMemberEditor({
  details,
  busy,
  onSave,
}: {
  details: AdminModelGroupDetails;
  busy: boolean;
  onSave: (members: AdminModelGroupMember[], revision: number) => Promise<void>;
}) {
  const [selected, setSelected] = useState<AdminModelGroupMember[]>(details.members);
  useEffect(() => setSelected(details.members), [details.members]);

  return (
    <section data-model-group-members className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">已选成员 · {selected.length} 人</h3>
          <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">每人只归属一个组；保存后，新增成员将从原业务组转入本组。</p>
        </div>
        <Button type="primary" loading={busy} disabled={busy} htmlType="button" onClick={() => void onSave(selected, details.revision)} icon={<Save size={15} />}>保存成员</Button>
      </div>
      <div className="overflow-hidden rounded-lg border border-[var(--theme-border)]">
        {selected.length ? selected.map((member) => {
          const id = member.work_id || member.user_id;
          return <div key={id} className="flex items-center gap-3 border-b border-[var(--theme-border)] px-3 py-3 last:border-b-0" data-model-group-selected-member={id}>
            <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{member.display_name || id}</p><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{member.work_id ? `工号：${member.work_id}` : `平台账号：${member.user_id}`}</p></div>
            <Button type="text" size="small" disabled={busy} aria-label={`移除成员 ${member.display_name || id}`} onClick={() => setSelected((current) => current.filter((item) => (item.work_id || item.user_id) !== id))} icon={<X size={15} />} />
          </div>;
        }) : <div className="flex flex-col items-center gap-2 px-4 py-8 text-center"><Users size={24} className="text-[var(--theme-text-secondary)]" /><p className="text-sm font-medium">暂无成员</p><p className="text-sm text-[var(--theme-text-secondary)]">从下方公司员工目录选择人员加入。</p></div>}
      </div>
      <ModelGroupMemberPicker members={selected} disabled={busy} onConfirm={setSelected} />
    </section>
  );
}

function GroupModelEditor({
  details,
  catalog,
  busy,
  onSave,
  isDefault,
}: {
  details: AdminModelGroupDetails;
  catalog: AdminModelEntry[];
  busy: boolean;
  onSave: (models: AdminModelGroupModel[], revision: number, defaultModelId: string | null) => Promise<void>;
  isDefault: boolean;
}) {
  const [models, setModels] = useState(details.models);
  const [defaultModelId, setDefaultModelId] = useState(details.default_model_id || "");
  const [draggedId, setDraggedId] = useState<string | null>(null);
  useEffect(() => { setModels(details.models); setDefaultModelId(details.default_model_id || ""); }, [details.default_model_id, details.models]);

  const orderedModels = useMemo(() => [...models].sort((left, right) => left.display_order - right.display_order), [models]);
  const modelById = useMemo(() => new Map(models.map((model) => [model.model_id, model])), [models]);
  const unassigned = catalog.filter((model) => !modelById.has(model.id));
  const renumber = (items: AdminModelGroupModel[]) => items.map((item, index) => ({ ...item, display_order: index }));
  const toggle = (model: AdminModelEntry) => {
    setModels((current) => {
      if (current.some((entry) => entry.model_id === model.id)) {
        if (defaultModelId === model.id) setDefaultModelId("");
        return current.filter((entry) => entry.model_id !== model.id);
      }
      if (!defaultModelId) setDefaultModelId(model.id);
      return [...current, {
        model_id: model.id,
        model_value: model.value,
        label: model.provider_name ? `${model.label} · ${model.provider_name}` : model.label,
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
  const update = (modelId: string, patch: Partial<AdminModelGroupModel>) => setModels((current) => current.map((item) => item.model_id === modelId ? { ...item, ...patch } : item));
  const renderAssigned = (entry: AdminModelGroupModel, index: number) => (
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
        <Checkbox checked disabled={busy} onChange={() => toggle({ id: entry.model_id, value: entry.model_value, label: entry.label } as AdminModelEntry)}>
          <span className="min-w-0"><span className="block truncate text-sm">{entry.label}</span><span className="block truncate text-xs text-[var(--theme-text-secondary)]">{entry.model_value}</span>{catalog.find((model) => model.id === entry.model_id)?.available === false ? <span className="block text-xs text-[var(--theme-warning-text)]" data-group-model-missing>上游缺失 · 分组关联仍保留，取消勾选并保存可移除</span> : null}</span>
        </Checkbox>
      </div>
      <div className="flex items-center gap-1" aria-label={`调整 ${entry.label} 展示顺序`}>
        <Button type="text" size="small" aria-label={`上移 ${entry.label}`} disabled={busy || index === 0} onClick={() => move(entry.model_id, -1)} icon={<ArrowUp size={14} />} />
        <Button type="text" size="small" aria-label={`下移 ${entry.label}`} disabled={busy || index === orderedModels.length - 1} onClick={() => move(entry.model_id, 1)} icon={<ArrowDown size={14} />} />
      </div>
      <label className="flex items-center gap-2 text-xs text-[var(--theme-text-secondary)]"><input type="radio" name="model-group-default-model" aria-label={`设为默认模型 ${entry.label}`} checked={defaultModelId === entry.model_id} disabled={busy || !entry.enabled} onChange={() => setDefaultModelId(entry.model_id)} />默认模型</label>
      <div className="flex items-center gap-2"><span className="text-xs sm:hidden">启用</span><SwitchRoot aria-label={`启用 ${entry.label}`} checked={entry.enabled} disabled={busy} onCheckedChange={(enabled) => update(entry.model_id, { enabled })}><SwitchThumb /></SwitchRoot></div>
    </div>
  );

  return (
    <section data-model-group-models className="flex min-h-0 flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div><h3 className="text-sm font-semibold">模型</h3><p className="text-xs text-[var(--theme-text-secondary)]">选择组内可用模型，并指定一个默认模型；用户仍可在聊天中手动切换。</p></div>
        <Button type="primary" loading={busy} disabled={busy || (orderedModels.length > 0 && !defaultModelId)} htmlType="button" onClick={() => void onSave(renumber(orderedModels), details.revision, defaultModelId || null)} icon={<Save size={15} />}>保存模型</Button>
      </div>
      {isDefault ? <div className="flex items-start gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-3 text-sm text-[var(--theme-text-secondary)]"><Info size={16} className="mt-0.5 shrink-0" /><span>普通默认组模型可供专家及独立业务入口使用；不可用于普通聊天，专家权限仍单独校验。</span></div> : null}
      <div className="min-h-0 overflow-auto rounded-lg border border-[var(--theme-border)]">
        {orderedModels.length ? <>
          <div className="hidden grid-cols-[minmax(0,1fr)_6rem_8rem_5rem] gap-2 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-2 text-xs text-[var(--theme-text-secondary)] sm:grid"><span>已加入模型</span><span>展示顺序</span><span>默认模型</span><span>启用</span></div>
          <div className="px-3">{orderedModels.map(renderAssigned)}</div>
        </> : <div className="flex min-h-64 flex-col items-center justify-center gap-3 px-6 py-10 text-center"><div className="flex size-12 items-center justify-center rounded-full bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"><Boxes size={24} /></div><div><p className="text-sm font-semibold">暂无已加入模型</p><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">展开下方“添加模型”选择模型，保存后生效。</p></div></div>}
        {unassigned.length ? <details className="border-t border-[var(--theme-border)] px-3 py-3" data-model-group-add-models><summary className="cursor-pointer text-sm font-medium">添加模型 · {unassigned.length} 个可选</summary><div className="mt-3 max-h-56 space-y-1 overflow-auto">{unassigned.map((model) => <div key={model.id} className="flex items-center gap-2 border-b border-[var(--theme-border)] py-2 last:border-b-0"><Checkbox checked={false} disabled={busy} onChange={() => toggle(model)}><span className="min-w-0"><span className="block truncate text-sm">{model.label}</span><span className="block truncate text-xs text-[var(--theme-text-secondary)]">{model.provider_name ? `${model.value} · ${model.provider_name}` : model.value}</span></span></Checkbox></div>)}</div></details> : null}
      </div>
    </section>
  );
}

export function GroupDrawer({
  group,
  catalog,
  onClose,
  onChanged,
  initialTab = "basic",
  finalFocus,
}: {
  group: AdminModelGroupSummary | null;
  catalog: AdminModelEntry[];
  onClose: () => void;
  onChanged: (signal?: AbortSignal) => Promise<void>;
  initialTab?: "basic" | "models" | "members";
  finalFocus?: React.RefObject<HTMLElement | null>;
}) {
  const [details, setDetails] = useState<AdminModelGroupDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"details" | "meta" | "models" | "members" | "delete" | null>(null);
  const [remark, setRemark] = useState("");
  const [name, setName] = useState("");
  const [sortOrder, setSortOrder] = useState(0);
  const [enabled, setEnabled] = useState(true);
  const [activeTab, setActiveTab] = useState<"basic" | "models" | "members">("basic");
  const [confirmAction, setConfirmAction] = useState<"disable" | "delete" | null>(null);
  const [deleteDetails, setDeleteDetails] = useState<AdminModelGroupDetails | null>(null);
  const [deleteTargets, setDeleteTargets] = useState<AdminModelGroupSummary[]>([]);
  const [memberDestination, setMemberDestination] = useState("");
  const deleteTrigger = useRef<HTMLButtonElement>(null);
  const [pendingMembers, setPendingMembers] = useState<AdminModelGroupMember[] | null>(null);
  const loadController = useRef<AbortController | null>(null);
  const mutationController = useRef<AbortController | null>(null);
  const versionRef = useRef(0);

  useEffect(() => {
    loadController.current?.abort();
    mutationController.current?.abort();
    const version = ++versionRef.current;
    if (!group) {
      setDetails(null);
      return undefined;
    }
    const controller = new AbortController();
    loadController.current = controller;
    setDetails(null);
    setDeleteDetails(null);
    setPendingMembers(null);
    setConfirmAction(null);
    setBusy("details");
    setError(null);
    setActiveTab(initialTab);
    void modelGroupsApi.details(group.id, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted || version !== versionRef.current) return;
      setDetails(value);
      setRemark(value.remark || "");
      setName(value.name);
      setSortOrder(value.sort_order);
      setEnabled(value.enabled);
    }).catch((caught) => {
      if (!controller.signal.aborted && version === versionRef.current) setError(errorText(caught));
    }).finally(() => {
      if (!controller.signal.aborted && version === versionRef.current) setBusy(null);
    });
    return () => { controller.abort(); mutationController.current?.abort(); };
  }, [group, initialTab]);

  const close = () => {
    loadController.current?.abort();
    mutationController.current?.abort();
    versionRef.current += 1;
    setConfirmAction(null);
    onClose();
  };
  const runMutation = async <T,>(operation: Exclude<typeof busy, null>, action: (signal: AbortSignal) => Promise<T>) => {
    if (!details || !group || busy) return;
    const version = ++versionRef.current;
    const controller = new AbortController();
    mutationController.current = controller;
    setBusy(operation);
    setError(null);
    try {
      const result = await action(controller.signal);
      if (controller.signal.aborted || version !== versionRef.current) return null;
      if (operation !== "details") await onChanged(controller.signal);
      if (controller.signal.aborted || version !== versionRef.current) return null;
      return result;
    } catch (caught) {
      if (!controller.signal.aborted && version === versionRef.current) setError(errorText(caught));
      return null;
    } finally {
      if (!controller.signal.aborted && version === versionRef.current) setBusy(null);
    }
  };
  const saveMeta = async () => {
    if (!details || !group) return;
    const impact = !enabled && (details.member_count > 0 || details.models.length > 0);
    if (impact && confirmAction !== "disable") {
      setConfirmAction("disable");
      return;
    }
    setConfirmAction(null);
    const result = await runMutation("meta", (signal) => modelGroupsApi.update(group.id, {
      ...(group.type === "business" ? { name: name.trim(), enabled, sort_order: sortOrder } : {}),
      remark,
      revision: details.revision,
      ...(impact ? { confirm_impact: true } : {}),
    }, { signal }));
    if (result) {
      setDetails((current) => current ? { ...current, ...result } : current);
    }
  };
  const saveModels = async (models: AdminModelGroupModel[], revision: number, defaultModelId: string | null) => {
    if (!group) return;
    const result = await runMutation("models", (signal) => modelGroupsApi.setModels(group.id, { revision, default_model_id: defaultModelId, models: models.map(({ model_id, enabled: isEnabled, display_order, priority, weight }) => ({ model_id, enabled: isEnabled, display_order, priority, weight })) }, { signal }));
    if (result) {
      setDetails((current) => current ? { ...current, models, default_model_id: result.default_model_id ?? defaultModelId, model_count: models.length, revision: result.revision } : current);
    }
  };
  const saveMembers = async (members: AdminModelGroupMember[], revision: number, confirmed = false) => {
    if (!group || !details) return;
    const ids = new Set(members.map((m) => m.work_id || m.user_id));
    if (!confirmed && details.members.some((m) => !ids.has(m.work_id || m.user_id))) {
      setPendingMembers(members);
      return;
    }
    setPendingMembers(null);
    const result = await runMutation("members", async (signal) => {
      await modelGroupsApi.setMembers(group.id, {
        revision, confirm_default_fallback: confirmed,
        members: members.map((member) => ({ ...(member.work_id ? { work_id: member.work_id } : { user_id: member.user_id }), is_primary: member.is_primary })),
      }, { signal });
      return modelGroupsApi.details(group.id, { signal });
    });
    if (result) {
      setDetails((current) => current ? { ...current, members: result.members, member_count: result.member_count, revision: result.revision } : current);
    }
  };
  const openDelete = async () => {
    if (!group || busy) return;
    const result = await runMutation("details", async (signal) => {
      const [current, available] = await Promise.all([modelGroupsApi.details(group.id, { signal }), modelGroupsApi.listAdmin({ signal })]);
      return { current, available };
    });
    if (result) {
      setDeleteDetails(result.current);
      setDeleteTargets(result.available.groups.filter((item) => item.id !== group.id && item.type === "business" && item.enabled));
      setMemberDestination("");
      setConfirmAction("delete");
    }
  };
  const confirmDelete = async () => {
    if (!deleteDetails || !group || group.type !== "business" || deleteDetails.active_run_count || (deleteDetails.member_count > 0 && !memberDestination)) return;
    const target = deleteTargets.find((item) => item.id === memberDestination);
    const result = await runMutation("delete", async (signal) => {
      try {
        return await modelGroupsApi.retire(group.id, {
          revision: deleteDetails.revision, confirm_impact: true, delete: true,
          ...(deleteDetails.member_count > 0 ? target ? { target_group_id: target.id, target_group_revision: target.revision } : { confirm_default_fallback: true } : {}),
        }, { signal });
      } catch (caught) {
        if (caught instanceof ApiRequestError && caught.code === "model_group_active_runs") {
          const current = await modelGroupsApi.details(group.id, { signal });
          if (!signal.aborted) setDeleteDetails(current);
        }
        throw caught;
      }
    });
    if (result) close();
  };

  if (!group) return null;
  return (
    <>
      <DrawerRoot open onOpenChange={(open) => { if (!open && !busy) close(); }}>
        <DrawerPortal><DrawerBackdrop /><DrawerPopup placement="right" width="min(48rem, 100vw)" finalFocus={finalFocus} data-model-group-drawer aria-label={`${group.name} 分组`}>
          <DrawerHeader><div className="min-w-0"><DrawerTitle>{group.name}</DrawerTitle><p className="mt-1 font-mono text-xs text-[var(--theme-text-secondary)]">{group.code} · {group.type === "ordinary_default" ? "普通默认组 · 隐式归属" : "业务组"}</p></div><Button type="text" aria-label="关闭分组抽屉" disabled={Boolean(busy)} onClick={close} icon={<X size={18} />} /></DrawerHeader>
          <DrawerContent>
            <div className="min-w-0 flex-1 p-5">
              {busy === "details" ? <p className="text-sm text-[var(--theme-text-secondary)]">正在加载分组…</p> : null}
              {error ? <p className="mb-3 border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-sm text-[var(--theme-danger-text)]" role="alert">{error}</p> : null}
              {details ? <TabsRoot value={activeTab} onValueChange={(value) => setActiveTab(value as typeof activeTab)}>
                <TabsList aria-label="分组编辑标签" variant="point"><TabsTab value="basic" variant="point">基本信息</TabsTab><TabsTab value="models" variant="point">模型</TabsTab>{group.type === "business" ? <TabsTab value="members" variant="point">成员</TabsTab> : null}<TabsIndicator variant="point" /></TabsList>
                <TabsPanel value="basic" keepMounted className="pt-5"><section className="space-y-3"><h3 className="text-sm font-semibold">基本信息</h3>{group.type === "ordinary_default" ? <p className="text-xs text-[var(--theme-warning)]">普通默认组固定身份，不可停用或删除；该组模型不可用于普通聊天。</p> : <label className="block text-sm">名称<Input value={name} disabled={Boolean(busy)} onChange={(event) => setName(event.target.value)} /></label>}<label className="block text-sm">备注<TextArea value={remark} disabled={Boolean(busy)} onChange={(event) => setRemark(event.target.value)} /></label>{group.type === "business" ? <><label className="block text-sm">展示排序<Input type="number" min={0} value={sortOrder} disabled={Boolean(busy)} onChange={(event) => setSortOrder(boundedInteger(event.target.value, 0, 100000, sortOrder))} /></label><div className="flex items-center justify-between"><span className="text-sm">启用该组</span><SwitchRoot aria-label="启用业务组" checked={enabled} disabled={Boolean(busy)} onCheckedChange={setEnabled}><SwitchThumb /></SwitchRoot></div></> : null}<Button type="primary" disabled={Boolean(busy) || (group.type === "business" && !name.trim())} loading={busy === "meta"} onClick={() => void saveMeta()} htmlType="button">保存基本信息</Button></section></TabsPanel>
                <TabsPanel value="models" keepMounted className="min-h-0 pt-5"><GroupModelEditor details={details} catalog={catalog} busy={Boolean(busy)} onSave={saveModels} isDefault={group.type === "ordinary_default"} /></TabsPanel>
                {group.type === "business" ? <TabsPanel value="members" keepMounted className="min-h-0 pt-5"><GroupMemberEditor details={details} busy={Boolean(busy)} onSave={saveMembers} /></TabsPanel> : null}
              </TabsRoot> : null}
              {details && group.type === "ordinary_default" ? <div className="mt-5 flex items-center gap-2 rounded-lg border border-[var(--theme-border)] px-3 py-2 text-xs text-[var(--theme-text-secondary)]"><Users size={15} className="shrink-0" /><span>自动归属 {details.member_count} 人：平台内尚未加入业务组的有效账号，不是钉钉全员或历史登录人数。</span></div> : null}
              {details && group.type === "business" ? <section className="mt-6 space-y-2 border-t border-[var(--theme-border)] pt-4"><p className="text-xs text-[var(--theme-text-secondary)]">删除会处理成员去向并解除本组模型关联，保留历史任务；不会删除模型。</p><Button ref={deleteTrigger} type="text" danger disabled={Boolean(busy)} loading={busy === "delete"} onClick={() => void openDelete()} icon={<Trash2 size={15} />} htmlType="button">删除分组</Button></section> : null}
            </div>
          </DrawerContent>
        </DrawerPopup></DrawerPortal>
      </DrawerRoot>
      <ConfirmDialog isOpen={pendingMembers !== null} title="确认移除成员" message="这些用户将失去本组权限；移除最后一个业务组的用户会回到普通默认组并失去普通聊天资格。需要保留对话资格的用户，请先在用户配置中转入其他业务组。" confirmText="确认移除" onCancel={() => setPendingMembers(null)} onConfirm={() => { if (pendingMembers && details) void saveMembers(pendingMembers, details.revision, true); }} loading={busy === "members"} variant="warning" />
      <ConfirmDialog isOpen={confirmAction === "disable"} title="确认停用业务组" message={`停用后该组不再用于新的分配。当前包含 ${details?.member_count ?? 0} 名成员和 ${details?.models.length ?? 0} 个模型，确认继续？`} confirmText="确认停用" onCancel={() => setConfirmAction(null)} onConfirm={() => void saveMeta()} loading={busy === "meta"} variant="warning" />
      <ModalRoot open={confirmAction === "delete"} onOpenChange={(open) => { if (!open && !busy) setConfirmAction(null); }}><ModalPortal><ModalBackdrop /><ModalPopup width={440} className="workbench-modal" finalFocus={deleteTrigger} aria-label="确认删除分组"><ModalHeader><ModalTitle>删除 {group.name}</ModalTitle></ModalHeader><ModalContent><div className="space-y-4">
        <p className="text-sm">本组包含 {deleteDetails?.member_count ?? 0} 名成员、{deleteDetails?.model_count ?? 0} 个模型。删除后从管理列表隐藏，历史任务保留；模型本身及其他组的关联不受影响。</p>
        {deleteDetails?.active_run_count ? <p role="alert" className="text-sm text-[var(--theme-danger-text)]">当前有 {deleteDetails?.active_run_count} 个未完成任务，请等待结束后重新打开删除。</p> : null}
        {deleteDetails?.member_count ? <><LobeSelectField aria-label="删除后成员去向" value={memberDestination || "__choose__"} disabled={Boolean(busy)} onChange={(value) => setMemberDestination(value === "__choose__" ? "" : value)} options={[{ value: "__choose__", label: "请选择成员去向", disabled: true }, ...deleteTargets.map((item) => ({ value: item.id, label: `全部转入：${item.name}` })), { value: "__default__", label: "确认全部回普通默认组，失去普通聊天资格" }]} />{memberDestination === "__default__" ? <p className="text-sm text-[var(--theme-warning-text)]">确认删除即确认这些成员回到普通默认组，并失去普通聊天资格。</p> : null}</> : null}
        {error ? <p role="alert" className="text-sm text-[var(--theme-danger-text)]">{error}</p> : null}
      </div></ModalContent><ModalFooter><Button disabled={Boolean(busy)} onClick={() => setConfirmAction(null)}>取消</Button><Button type="primary" danger loading={busy === "delete"} disabled={Boolean(busy) || Boolean(deleteDetails?.active_run_count) || Boolean(deleteDetails?.member_count && !memberDestination)} onClick={() => void confirmDelete()}>确认删除</Button></ModalFooter></ModalPopup></ModalPortal></ModalRoot>
    </>
  );
}

export function ModelGroupManagement({ groupRevision = 0, onGroupsChanged }: { groupRevision?: number; onGroupsChanged?: () => void } = {}) {
  const [groups, setGroups] = useState<AdminModelGroupSummary[]>([]);
  const [catalog, setCatalog] = useState<AdminModelEntry[]>([]);
  const [selectedGroup, setSelectedGroup] = useState<AdminModelGroupSummary | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [newRemark, setNewRemark] = useState("");
  const [newSortOrder, setNewSortOrder] = useState(0);
  const [creating, setCreating] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const drawerTrigger = useRef<HTMLElement | null>(null);
  const openGroup = (group: AdminModelGroupSummary, trigger: HTMLElement) => {
    drawerTrigger.current = trigger;
    setSelectedGroup(group);
  };

  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    try {
      const [groupResult, modelResult] = await Promise.all([modelGroupsApi.listAdmin({ signal }), modelAdminApi.get({ signal })]);
      if (signal?.aborted || version !== requestVersion.current) return;
      setGroups(groupResult.groups);
      setCatalog(modelResult.models);
    } catch (caught) {
      if (!signal?.aborted && version === requestVersion.current) setError(errorText(caught));
    } finally {
      if (!signal?.aborted && version === requestVersion.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    void load(controller.signal);
    return () => {
      controller.abort();
      requestVersion.current += 1;
    };
  }, [load, groupRevision]);
  const filtered = groups.filter((group) => {
    const value = `${group.name} ${group.code}`.toLowerCase();
    return (!query.trim() || value.includes(query.trim().toLowerCase())) && (status === "all" || (status === "enabled" ? group.enabled : !group.enabled));
  });
  const create = async () => {
    if (!newCode.trim() || !newName.trim() || creating) return;
    setCreating(true);
    setError(null);
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    try {
      await modelGroupsApi.create({ code: newCode.trim(), name: newName.trim(), remark: newRemark, sort_order: newSortOrder }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setCreateOpen(false);
      setNewCode(""); setNewName(""); setNewRemark(""); setNewSortOrder(0);
      await load(controller.signal);
      if (!controller.signal.aborted) onGroupsChanged?.();
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorText(caught));
    } finally {
      if (!controller.signal.aborted) setCreating(false);
    }
  };
  const refresh = useCallback(async (signal?: AbortSignal) => {
    await load(signal);
    if (!signal?.aborted) onGroupsChanged?.();
  }, [load, onGroupsChanged]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden p-4" data-model-group-management>
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-base font-semibold">分组管理</h2><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">模型和用户成员通过分组统一授权；普通默认组不可用于普通聊天。</p></div><Button type="primary" htmlType="button" onClick={() => setCreateOpen(true)} icon={<Plus size={16} />}>新建业务组</Button></div>
      <div className="flex flex-wrap items-center gap-2"><Input aria-label="搜索模型分组" prefix={<Search size={15} />} placeholder="搜索名称或 code" value={query} onChange={(event) => setQuery(event.target.value)} /><LobeSelectField aria-label="筛选分组状态" value={status} onChange={setStatus} options={[{ value: "all", label: "状态：全部" }, { value: "enabled", label: "状态：已启用" }, { value: "disabled", label: "状态：已停用" }]} /><span className="text-xs text-[var(--theme-text-secondary)]">{filtered.length} / {groups.length} 组</span></div>
      {error ? <p className="text-sm text-[var(--theme-danger-text)]" role="alert">{error}</p> : null}
      <div className="min-h-0 flex-1 overflow-auto border-y border-[var(--theme-border)]" data-model-group-table>{loading ? <p className="p-4 text-sm text-[var(--theme-text-secondary)]">正在加载分组…</p> : <Table size="small" pagination={false} dataSource={filtered} rowKey="id" columns={[{ key: "name", title: "名称", render: (_value, group) => <button type="button" className="text-left font-medium text-[var(--theme-primary)] hover:underline" onClick={(event) => openGroup(group, event.currentTarget)}>{group.name}<span className="ml-2 font-mono text-xs text-[var(--theme-text-secondary)]">{group.code}</span></button> }, { key: "type", title: "类型", render: (_value, group) => group.type === "ordinary_default" ? "普通默认组" : "业务组" }, { key: "sort", title: "展示排序", render: (_value, group) => group.sort_order }, { key: "models", title: "模型", render: (_value, group) => group.model_count }, { key: "members", title: "成员", render: (_value, group) => <span className="inline-flex items-center gap-2"><span>{group.member_count}</span>{group.type === "ordinary_default" ? <span className="text-xs text-[var(--theme-text-secondary)]">自动归属</span> : null}</span> }, { key: "status", title: "状态", render: (_value, group) => <Tag color={group.enabled ? "success" : "warning"}>{availabilityCopy(group)}</Tag> }, { key: "actions", title: "操作", render: (_value, group) => <Button type="text" size="small" htmlType="button" aria-label={`编辑 ${group.name}`} onClick={(event) => openGroup(group, event.currentTarget)} icon={<Edit3 size={15} />} /> }]} emptyText="暂无模型分组" />}</div>
      {createOpen ? <div className="border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-4" data-model-group-create><div className="flex items-center justify-between"><h3 className="text-sm font-semibold">新建业务组</h3><Button type="text" aria-label="取消新建分组" onClick={() => setCreateOpen(false)} icon={<X size={16} />} /></div><div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-sm">Code<Input aria-label="新分组 code" value={newCode} onChange={(event) => setNewCode(event.target.value)} /></label><label className="text-sm">名称<Input aria-label="新分组名称" value={newName} onChange={(event) => setNewName(event.target.value)} /></label><label className="text-sm">展示排序<Input aria-label="新分组展示排序" type="number" min={0} value={newSortOrder} onChange={(event) => setNewSortOrder(boundedInteger(event.target.value, 0, 100000, newSortOrder))} /></label><label className="text-sm sm:col-span-2">备注<TextArea aria-label="新分组备注" value={newRemark} onChange={(event) => setNewRemark(event.target.value)} /></label></div><div className="mt-3 flex justify-end gap-2"><Button htmlType="button" onClick={() => setCreateOpen(false)}>取消</Button><Button type="primary" loading={creating} disabled={creating || !newCode.trim() || !newName.trim()} htmlType="button" onClick={() => void create()}>创建</Button></div></div> : null}
      <GroupDrawer group={selectedGroup} catalog={catalog} onClose={() => setSelectedGroup(null)} onChanged={refresh} finalFocus={drawerTrigger} />
    </div>
  );
}
