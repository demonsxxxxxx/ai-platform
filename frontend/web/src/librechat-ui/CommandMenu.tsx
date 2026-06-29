import type { ComponentType, ReactNode } from "react";
import { clsx } from "clsx";

export interface LibreChatCommandMenuProps {
  label: string;
  closeLabel: string;
  children: ReactNode;
  onClose: () => void;
}

/** Provides the floating slash-command menu shell for the ai-platform composer. */
export function LibreChatCommandMenu({
  label,
  closeLabel,
  children,
  onClose,
}: LibreChatCommandMenuProps) {
  return (
    <div
      className="composer-command-surface absolute bottom-full left-2 right-2 z-40 mb-2 border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_12px_28px_rgba(15,23,42,0.12)] dark:bg-stone-900"
      role="listbox"
      data-composer-command-menu
      data-librechat-command-menu
      aria-label={label}
    >
      <div className="border-b border-[var(--theme-border)] px-3 py-2 text-[11px] font-semibold uppercase text-[var(--theme-text-secondary)]">
        {label}
      </div>
      <div className="composer-command-list p-1.5">{children}</div>
      <div className="border-t border-[var(--theme-border)] px-3 py-2 text-xs text-[var(--theme-text-secondary)]">
        <button
          type="button"
          onClick={onClose}
          className="rounded px-1.5 py-1 hover:bg-[var(--theme-bg-sidebar)]"
        >
          {closeLabel}
        </button>
      </div>
    </div>
  );
}

export interface LibreChatCommandMenuItemProps {
  id: string;
  label: ReactNode;
  description: ReactNode;
  command: string;
  alias?: string;
  unavailable?: boolean;
  unavailableLabel?: ReactNode;
  active?: boolean;
  icon: ComponentType<{ size?: number }>;
  onHighlight: () => void;
  onSelect: () => void;
}

/** Renders one selectable command row inside the LibreChat-style command menu. */
export function LibreChatCommandMenuItem({
  id,
  label,
  description,
  command,
  alias,
  unavailable,
  unavailableLabel,
  active,
  icon: Icon,
  onHighlight,
  onSelect,
}: LibreChatCommandMenuItemProps) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onMouseEnter={onHighlight}
      onClick={onSelect}
      className="flex min-h-12 w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-[var(--theme-bg-sidebar)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]"
      data-composer-command-item={id}
      data-active={active ? "" : undefined}
    >
      <span
        className={clsx(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
          active
            ? "bg-[var(--theme-primary)] text-white dark:text-stone-950"
            : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]",
        )}
      >
        <Icon size={16} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="font-medium text-[var(--theme-text)]">{label}</span>
          <span className="rounded bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--theme-text-secondary)]">
            /{command}
          </span>
          {alias ? (
            <span className="rounded bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--theme-text-secondary)]">
              {alias}
            </span>
          ) : null}
          {unavailable ? (
            <span className="rounded bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--theme-text-secondary)]">
              {unavailableLabel}
            </span>
          ) : null}
        </span>
        <span className="mt-0.5 block truncate text-xs text-[var(--theme-text-secondary)]">
          {description}
        </span>
      </span>
    </button>
  );
}
