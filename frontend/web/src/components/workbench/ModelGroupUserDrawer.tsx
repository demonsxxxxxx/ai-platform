import { Button, Checkbox, DrawerBackdrop, DrawerContent, DrawerHeader, DrawerPopup, DrawerPortal, DrawerRoot, DrawerTitle, Tag } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { Save, X } from "lucide-react";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { ApiRequestError } from "../../services/api/fetch";
import { modelGroupsApi, type AdminModelGroupSummary, type UserModelGroups } from "../../services/api/modelGroups";
import type { AdminUserSummary } from "../../services/api/adminUsers";

const COPY: Record<string, string> = {
  model_group_fallback_confirmation_required: "移除最后一个业务组会回到默认组，请明确确认。",
  group_not_available: "分组不存在、已停用或不属于当前租户。",
  model_group_primary_required: "请选择归属组。",
  model_group_single_membership_required: "每位用户只能归属一个模型组。",
  model_group_revision_conflict: "分组已被其他管理员更新，请重新加载。",
};

function errorText(error: unknown): string {
  if (error instanceof ApiRequestError && error.code && COPY[error.code]) return COPY[error.code];
  return error instanceof Error ? error.message : "用户模型分组操作失败，请重试。";
}

export function ModelGroupUserDrawer({ user, onClose, finalFocus }: { user: AdminUserSummary | null; onClose: () => void; finalFocus?: React.RefObject<HTMLElement | null> }) {
  const [groups, setGroups] = useState<AdminModelGroupSummary[]>([]);
  const [membership, setMembership] = useState<UserModelGroups | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [primary, setPrimary] = useState<string | null>(null);
  const [confirmFallback, setConfirmFallback] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    requestRef.current?.abort();
    setMembership(null);
    setGroups([]);
    setSelected([]);
    setPrimary(null);
    setSaving(false);
    if (!user) return;
    const controller = new AbortController();
    requestRef.current = controller;
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    void Promise.all([
      modelGroupsApi.listAdmin({ signal: controller.signal }),
      modelGroupsApi.userMemberships(user.user_id, { signal: controller.signal }),
    ]).then(([groupResult, membershipResult]) => {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      const activeGroups = groupResult.groups.filter((group) => group.type === "business"
        && (group.enabled || membershipResult.groups.some((current) => current.id === group.id)));
      setGroups(activeGroups);
      setMembership(membershipResult);
      setSelected(membershipResult.groups.map((group) => group.id));
      setPrimary(membershipResult.primary_group_id);
      setConfirmFallback(false);
    }).catch((caught) => {
      if (!controller.signal.aborted && version === requestVersion.current) setError(errorText(caught));
    }).finally(() => {
      if (!controller.signal.aborted && version === requestVersion.current) setLoading(false);
    });
    return () => { controller.abort(); requestRef.current?.abort(); requestVersion.current += 1; };
  }, [user]);

  const dirty = useMemo(() => Boolean(membership && (JSON.stringify(selected) !== JSON.stringify(membership.groups.map((group) => group.id)) || primary !== membership.primary_group_id)), [membership, primary, selected]);
  const save = async () => {
    if (!user || !membership || saving || (selected.length > 0 && !primary)) return;
    if (selected.length === 0 && !confirmFallback) {
      setError("移除最后一个业务组会让用户回到默认组并失去对话资格，请勾选明确确认。");
      return;
    }
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    const version = ++requestVersion.current;
    setSaving(true);
    setError(null);
    try {
      await modelGroupsApi.setUserMemberships(user.user_id, {
        group_ids: selected,
        primary_group_id: primary,
        expected_revision: membership.revision,
        confirm_default_fallback: confirmFallback,
      }, { signal: controller.signal });
      if (!controller.signal.aborted && version === requestVersion.current) onClose();
    } catch (caught) {
      if (!controller.signal.aborted && version === requestVersion.current) setError(errorText(caught));
    } finally {
      if (!controller.signal.aborted && version === requestVersion.current) setSaving(false);
    }
  };

  return <DrawerRoot open={Boolean(user)} onOpenChange={(open) => { if (!open && !saving) onClose(); }}><DrawerPortal><DrawerBackdrop /><DrawerPopup placement="right" width="min(34rem, 100vw)" finalFocus={finalFocus} data-user-model-groups={user?.user_id} aria-label="用户模型分组"><DrawerHeader><div className="min-w-0"><DrawerTitle>模型分组</DrawerTitle>{user ? <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">{user.display_name || user.user_id} · {user.user_id}</p> : null}</div><Button type="text" aria-label="关闭用户模型分组" disabled={saving} onClick={onClose} icon={<X size={18} />} /></DrawerHeader><DrawerContent><div className="space-y-4 p-5">{loading ? <p className="text-sm text-[var(--theme-text-secondary)]">正在加载用户分组…</p> : null}{error ? <p className="border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-sm text-[var(--theme-danger-text)]" role="alert">{error}</p> : null}{membership ? <><div><p className="text-sm font-semibold">业务组成员资格</p><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">每位用户只归属一个组。选择业务组会替换原归属；无业务组时自动归属普通默认组。</p></div><LobeSelectField aria-label="归属模型组" value={selected[0] || "__ordinary_default__"} disabled={saving} onChange={(value) => { const id = value === "__ordinary_default__" ? null : value; setSelected(id ? [id] : []); setPrimary(id); setConfirmFallback(false); }} options={[{ value: "__ordinary_default__", label: "普通默认组（自动归属）" }, ...groups.map((group) => ({ value: group.id, label: `${group.name}${!group.enabled ? "（已停用）" : ""}`, disabled: !group.enabled && !selected.includes(group.id) }))]} />{groups.length === 0 ? <p className="text-xs text-[var(--theme-text-secondary)]">当前没有可用业务组。</p> : null}{selected.length === 0 ? <label className="flex items-start gap-2 rounded-md border border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] p-3 text-xs text-[var(--theme-warning)]"><Checkbox data-model-group-fallback-confirmation checked={confirmFallback} disabled={saving} onChange={(checked) => setConfirmFallback(Boolean(checked))} /><span>我确认该用户回到普通默认组，并失去对话资格，直到重新分配业务组。</span></label> : null}<Tag color={selected.length ? "success" : "warning"}>{selected.length ? "已选择一个业务组" : "默认组自动归属 · 不可对话"}</Tag><Button type="primary" loading={saving} disabled={saving || !dirty || (selected.length > 0 && !primary)} htmlType="button" onClick={() => void save()} icon={<Save size={15} />}>保存分组</Button></> : null}</div></DrawerContent></DrawerPopup></DrawerPortal></DrawerRoot>;
}
