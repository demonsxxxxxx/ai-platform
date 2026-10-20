import { Button } from "@lobehub/ui/base-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { SessionThreadListItem } from "./sessionThreadListAdapter";

export interface ThreadListProps {
  children: ReactNode;
  className?: string;
}

/** Controlled list shell adapted from assistant-ui Elements thread-list.tsx. */
export function ThreadList({ children, className = "" }: ThreadListProps) {
  return (
    <div
      data-slot="thread-list"
      className={`flex w-full flex-col gap-px ${className}`.trim()}
    >
      {children}
    </div>
  );
}

export interface ThreadListRowProps {
  thread: SessionThreadListItem;
  active: boolean;
  onSelect: () => void;
  onTouchStart?: () => void;
  title: ReactNode;
  actions?: ReactNode;
  actionsVisible?: boolean;
  editing?: boolean;
}

/**
 * The row structure follows the pinned assistant-ui controlled ThreadList
 * source, while title editing and business actions stay with SessionItem.
 */
export function ThreadListRow({
  thread,
  active,
  onSelect,
  title,
  actions,
  actionsVisible = false,
  editing = false,
  onTouchStart,
}: ThreadListRowProps) {
  const hasActions = actions !== undefined;
  const contentStyle = {
    alignItems: "center",
    display: "flex",
    flex: 1,
    gap: "8px",
    height: "100%",
    minWidth: 0,
    padding: "0 9px",
    textAlign: "left" as const,
    fontSize: "13px",
    color: "var(--theme-text)",
    borderRadius: "8px",
    outline: "none",
    ...(hasActions ? { paddingRight: "40px" } : {}),
  };

  return (
    <div
      data-slot="thread-list-item"
      onTouchStart={onTouchStart}
      className={`group relative flex h-10 w-full items-center rounded-lg transition-colors ${
        active
          ? "bg-[var(--theme-sidebar-panel-muted)]"
          : "hover:bg-[var(--theme-sidebar-panel-muted)]"
      }`}
    >
      {editing ? (
        <div style={contentStyle}>{title}</div>
      ) : (
        <Button
          type="text"
          htmlType="button"
          data-slot="thread-list-item-trigger"
          aria-current={active || undefined}
          onClick={onSelect}
          className="min-w-0 flex-1 focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
          style={contentStyle}
        >
          <span className="min-w-0 flex-1 truncate">{title}</span>
          <ThreadListRowTime thread={thread} active={active} />
        </Button>
      )}
      {actions ? (
        <div
          className="absolute right-1.5 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
          style={actionsVisible ? { opacity: 1 } : undefined}
        >
          {actions}
        </div>
      ) : null}
    </div>
  );
}

function ThreadListRowTime({
  thread,
  active,
}: {
  thread: SessionThreadListItem;
  active: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span className="flex shrink-0 items-center gap-1.5 tabular-nums text-xs text-[var(--theme-text-tertiary)]">
      {thread.unread && !active ? (
        <>
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 rounded-full bg-[var(--theme-primary)]"
          />
          <span className="sr-only">{t("assistantUi.unread", { count: thread.unreadCount })}</span>
          {thread.unreadCount > 1 ? thread.unreadCount : null}
        </>
      ) : null}
      {thread.time}
    </span>
  );
}
