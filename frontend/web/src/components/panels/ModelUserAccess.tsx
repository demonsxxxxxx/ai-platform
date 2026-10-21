import { Button, Checkbox, Input } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, RefreshCw, Search, X } from "lucide-react";
import type { CompanyEmployee } from "../../services/api/companyEmployees";

export type UserDirectoryStatus = "idle" | "loading" | "ready" | "error";

export interface ModelUserAccessProps {
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

/** Per-model directory and selection; Lobe owns the controls' keyboard interaction. */
export function ModelUserAccess({
  accessMode, allowedUserIds, directory, directoryNames, directoryStatus,
  directoryQuery: query, directoryTotal, directoryError, disabled = false,
  onDirectoryQuery, onChange,
}: ModelUserAccessProps) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const usersById = useMemo(
    () => new Map([...directoryNames, ...directory.map((user) => [user.work_id, user] as const)]),
    [directoryNames, directory],
  );
  const toggle = (userId: string) => {
    if (disabled) return;
    onChange("selected", allowedUserIds.includes(userId)
      ? allowedUserIds.filter((item) => item !== userId) : [...allowedUserIds, userId]);
  };

  return (
    <div className="flex min-w-0 flex-col gap-2" data-model-user-access>
      <div className="flex flex-wrap gap-1">
        {([["all", "全部已认证用户"], ["selected", "仅指定用户"]] as const).map(([value, label]) => (
          <Button
            aria-pressed={accessMode === value}
            data-model-access-mode={value}
            disabled={disabled}
            key={value}
            size="small"
            type={accessMode === value ? "primary" : "default"}
            htmlType="button"
            onClick={() => {
              if (disabled) return;
              if (value === "selected" && directoryStatus === "idle") onDirectoryQuery("");
              onChange(value, value === "selected" ? allowedUserIds : []);
            }}
          >{label}</Button>
        ))}
      </div>
      {accessMode === "selected" ? (
        <>
          <div className="flex min-w-0 flex-wrap gap-1">
            {allowedUserIds.length === 0 ? (
              <span className="text-xs text-[var(--theme-danger)]">至少选择一名用户，空选不会保存为全部用户可见。</span>
            ) : null}
            {allowedUserIds.map((userId) => {
              const user = usersById.get(userId);
              return (
                <span className="inline-flex min-w-0 items-center gap-1 text-xs" data-model-access-user={userId} key={userId} title={user ? `${user.display_name} (${userId})` : userId}>
                  <span className="truncate" data-model-access-user-label>{user ? `${user.display_name} (${userId})` : userId}</span>
                  <Button aria-label={`移除 ${user?.display_name || userId}`} disabled={disabled} icon={X} size="small" type="text" htmlType="button" onClick={() => toggle(userId)} />
                </span>
              );
            })}
          </div>
          <Button
            aria-expanded={open} data-model-access-picker disabled={disabled}
            icon={ChevronDown} size="small" htmlType="button" style={{ alignSelf: "flex-start" }}
            onClick={() => {
              if (disabled) return;
              setOpen(!open);
              if (!open && directoryStatus === "idle") onDirectoryQuery(query.trim());
            }}
          >选择用户</Button>
          {open ? (
            <div className="flex min-w-0 flex-col gap-2">
              <Input aria-label="搜索用户" disabled={disabled} prefix={<Search size={14} />} size="small"
                placeholder="搜索姓名或工号" value={query}
                onChange={(event) => { if (!disabled) onDirectoryQuery(event.target.value.trim()); }} />
              <div aria-label="用户目录" aria-busy={directoryStatus === "loading"} className="flex max-h-44 flex-col gap-2 overflow-auto" data-model-access-directory role="group">
                {directory.map((user) => (
                  <Checkbox key={user.work_id} checked={allowedUserIds.includes(user.work_id)} disabled={disabled}
                    tabIndex={disabled ? -1 : 0}
                    onChange={() => toggle(user.work_id)}>
                    <span>{user.display_name} <span className="text-xs text-[var(--theme-text-secondary)]">{user.work_id}</span></span>
                  </Checkbox>
                ))}
                {directory.length === 0 && directoryStatus !== "error" ? (
                  <p className="text-xs text-[var(--theme-text-secondary)]" data-model-access-empty role="status">
                    {directoryStatus === "loading" ? "正在加载用户目录" : query.trim() ? "没有匹配的用户" : "用户目录为空"}
                  </p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-[var(--theme-text-secondary)]">{directoryStatus === "loading" ? "加载中…" : `已加载 ${directory.length} / ${directoryTotal}`}</span>
                {directory.length > 0 && directory.length < directoryTotal && directoryStatus !== "loading" ? (
                  <Button data-model-access-load-more disabled={disabled} size="small" htmlType="button"
                    onClick={() => { if (!disabled) onDirectoryQuery(query.trim(), { append: true }); }}>加载更多</Button>
                ) : null}
              </div>
            </div>
          ) : null}
        </>
      ) : <span className="text-xs text-[var(--theme-text-secondary)]">当前模型对所有已认证用户可用。</span>}
      {directoryError ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs text-[var(--theme-danger)]" role="alert">{directoryError}</p>
          <Button data-model-access-retry disabled={disabled} icon={RefreshCw} size="small" htmlType="button"
            onClick={() => { if (!disabled) onDirectoryQuery(query.trim()); }}>重试</Button>
        </div>
      ) : null}
    </div>
  );
}
