import { Button, Checkbox, Input, PopoverPopup, PopoverPortal, PopoverPositioner, PopoverRoot, PopoverTriggerElement, Radio } from "@lobehub/ui/base-ui";
import { RadioGroup as BaseRadioGroup } from "@base-ui/react/radio-group";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, RefreshCw, Search, Users, X } from "lucide-react";
import type { CompanyEmployee } from "../../services/api/companyEmployees";

export type UserDirectoryStatus = "idle" | "loading" | "ready" | "error";

export interface ModelUserAccessProps {
  modelId?: string;
  accessMode: "all" | "selected";
  allowedUserIds: string[];
  directory: CompanyEmployee[];
  directoryNames: Map<string, CompanyEmployee>;
  directoryStatus: UserDirectoryStatus;
  directoryQuery: string;
  directoryTotal: number;
  directoryError: string | null;
  disabled?: boolean;
  onDirectoryQuery: (search: string, options?: { append?: boolean }) => void;
  onChange: (mode: "all" | "selected", userIds: string[]) => void;
}

/** Compact per-model employee access trigger; the Lobe popover owns focus and portal behavior. */
export function ModelUserAccess({
  modelId, accessMode, allowedUserIds, directory, directoryNames, directoryStatus,
  directoryQuery: query, directoryTotal, directoryError, disabled = false,
  onDirectoryQuery, onChange,
}: ModelUserAccessProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);

  const usersById = useMemo(
    () => new Map([...directoryNames, ...directory.map((user) => [user.work_id, user] as const)]),
    [directoryNames, directory],
  );
  const selectedUsers = allowedUserIds.map((userId) => ({
    id: userId,
    user: usersById.get(userId),
  }));

  const toggle = (userId: string) => {
    if (disabled) return;
    onChange("selected", allowedUserIds.includes(userId)
      ? allowedUserIds.filter((item) => item !== userId) : [...allowedUserIds, userId]);
  };

  const openPicker = () => {
    if (disabled) return;
    setOpen(true);
    if (accessMode === "selected" && directoryStatus === "idle") onDirectoryQuery(query.trim());
  };

  return (
    <PopoverRoot
      open={open}
      onOpenChange={(next) => setOpen(next)}
    >
      <div
        className="model-user-access flex min-h-10 min-w-0 items-center"
        data-model-user-access
        data-model-user-access-model={modelId}
      >
        <PopoverTriggerElement disabled={disabled}>
          <Button
            ref={triggerRef}
            aria-controls={modelId ? `model-user-access-picker-${modelId}` : "model-user-access-picker"}
            aria-expanded={open}
            aria-label={accessMode === "all" ? "模型使用权限：全部用户" : allowedUserIds.length
              ? `模型使用权限：指定 ${allowedUserIds.length} 人`
              : "模型使用权限：请选择用户"}
            data-model-access-picker
            disabled={disabled}
            icon={accessMode === "all" ? Users : allowedUserIds.length ? Users : AlertTriangle}
            size="small"
            type="default"
            htmlType="button"
            onClick={openPicker}
          >
            {accessMode === "all" ? "全部用户" : allowedUserIds.length ? `指定 ${allowedUserIds.length} 人` : "请选择用户"}
            <ChevronDown size={14} aria-hidden="true" />
          </Button>
        </PopoverTriggerElement>
      </div>
      <PopoverPortal>
        <PopoverPositioner placement="bottomLeft" sideOffset={6}>
          <PopoverPopup
            id={modelId ? `model-user-access-picker-${modelId}` : "model-user-access-picker"}
            data-model-access-popover={modelId}
            aria-label="模型使用权限"
            finalFocus={triggerRef}
            className="model-user-access__popover"
            style={{ width: "min(420px, calc(100vw - 32px))", padding: 12 }}
          >
            <div className="flex flex-col gap-3">
              <div>
                <h3 className="text-sm font-semibold">模型使用权限</h3>
                <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">保存后对新 Run 生效</p>
              </div>
              <BaseRadioGroup
                aria-label="模型使用权限范围"
                name="model-user-access-mode"
                value={accessMode}
                onValueChange={(value) => {
                  if (disabled) return;
                  const nextMode = value as "all" | "selected";
                  if (nextMode === "all") onChange("all", []);
                  else {
                    onChange("selected", allowedUserIds);
                    if (directoryStatus === "idle") onDirectoryQuery(query.trim());
                  }
                }}
                className="flex flex-col gap-1"
              >
                <Radio aria-label="全部已认证用户" data-model-access-mode="all" value="all">全部已认证用户</Radio>
                <Radio aria-label="指定用户" data-model-access-mode="selected" value="selected">指定用户</Radio>
              </BaseRadioGroup>

              {accessMode === "selected" ? (
                <>
                  <div className="flex min-h-6 max-h-20 flex-wrap gap-1 overflow-y-auto" data-model-access-selected-summary aria-label="已选员工">
                    {selectedUsers.length === 0 ? (
                      <span className="inline-flex items-center gap-1 text-xs text-[var(--theme-text-secondary)]">
                        <AlertTriangle size={13} aria-hidden="true" />请选择用户
                      </span>
                    ) : selectedUsers.map(({ id, user }) => (
                      <span className="inline-flex max-w-full items-center gap-1 rounded-md bg-[var(--theme-fill-tertiary)] px-1.5 py-0.5 text-xs" data-model-access-user={id} key={id} title={user ? `${user.display_name}（${id}）` : `工号：${id}`}>
                        <span className="max-w-44 truncate">{user?.display_name ?? `工号：${id}`}</span>
                        <Button
                          aria-label={`移除 ${user?.display_name ?? id}`}
                          data-model-access-remove={id}
                          disabled={disabled}
                          icon={X}
                          size="small"
                          type="text"
                          htmlType="button"
                          onClick={() => toggle(id)}
                        />
                      </span>
                    ))}
                  </div>
                  <Input
                    aria-label="搜索用户"
                    disabled={disabled}
                    prefix={<Search size={14} aria-hidden="true" />}
                    size="small"
                    placeholder="搜索姓名或工号"
                    value={query}
                    onChange={(event) => { if (!disabled) onDirectoryQuery(event.target.value); }}
                  />
                  <div aria-label="用户目录" aria-busy={directoryStatus === "loading"} className="flex max-h-64 flex-col gap-1 overflow-auto" data-model-access-directory role="group">
                    {directory.map((user) => (
                      <label className="flex min-h-9 items-center gap-2 rounded-md px-2 py-1 hover:bg-[var(--theme-fill-tertiary)]" data-model-access-user-row={user.work_id} key={user.work_id}>
                        <Checkbox checked={allowedUserIds.includes(user.work_id)} disabled={disabled} tabIndex={disabled ? -1 : 0} onChange={() => toggle(user.work_id)}>
                          <span className="flex min-w-0 flex-col">
                            <span className="truncate text-sm">{user.display_name}</span>
                            <span className="text-xs text-[var(--theme-text-secondary)]">工号：{user.work_id}</span>
                          </span>
                        </Checkbox>
                        {allowedUserIds.includes(user.work_id) ? <Check size={14} aria-hidden="true" className="ml-auto text-[var(--theme-text-secondary)]" /> : null}
                      </label>
                    ))}
                    {directory.length === 0 && directoryStatus !== "error" ? (
                      <p className="py-3 text-center text-xs text-[var(--theme-text-secondary)]" data-model-access-empty role="status">
                        {directoryStatus === "loading" ? "正在加载用户目录" : query.trim() ? "没有匹配的用户" : "用户目录为空"}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--theme-text-secondary)]">
                    <span>{allowedUserIds.length ? `已选 ${allowedUserIds.length} 人` : "尚未选择员工"}</span>
                    <span>{directoryStatus === "loading" ? "加载中…" : directory.length >= directoryTotal ? "已加载全部员工" : `已加载 ${directory.length} / ${directoryTotal}`}</span>
                    {directory.length > 0 && directory.length < directoryTotal && directoryStatus !== "loading" ? (
                      <Button data-model-access-load-more disabled={disabled} size="small" htmlType="button" onClick={() => { if (!disabled) onDirectoryQuery(query.trim(), { append: true }); }}>加载更多</Button>
                    ) : null}
                  </div>
                  {directoryError ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-xs text-[var(--theme-danger-text)]" role="alert">{directoryError}</p>
                      <Button data-model-access-retry disabled={disabled} icon={RefreshCw} size="small" htmlType="button" onClick={() => { if (!disabled) onDirectoryQuery(query.trim()); }}>重试</Button>
                    </div>
                  ) : null}
                </>
              ) : null}
              <div className="flex justify-end border-t border-[var(--theme-border)] pt-2">
                <Button data-model-access-done disabled={disabled} size="small" type="primary" htmlType="button" onClick={() => setOpen(false)}>完成</Button>
              </div>
            </div>
          </PopoverPopup>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
