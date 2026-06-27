import { clsx } from "clsx";
import { libreChatSurface } from "../librechatShell/libreChatSurface";

export const workbenchSurface = {
  root: libreChatSurface.root,
  page:
    "flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]",
  statePage:
    "flex h-full min-h-0 items-center justify-center bg-[var(--theme-workbench-canvas)] px-4 text-[var(--theme-text)]",
  workspace: libreChatSurface.workspace,
  thread: libreChatSurface.thread,
  threadBody: libreChatSurface.threadBody,
  composer: libreChatSurface.composer,
  context: libreChatSurface.context,
  panel: libreChatSurface.panel,
  secondaryPanel: libreChatSurface.panel,
  sectionPanel:
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
  compactPanel: clsx(
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
  ),
  emptyState: clsx(
    "rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4",
    "shadow-[0_1px_2px_rgba(18,38,63,0.03)]",
  ),
  commandSurface: libreChatSurface.commandSurface,
  unavailable: clsx(
    "rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4 text-sm leading-6 text-[var(--theme-text-secondary)]",
  ),
  stateSurface: clsx(
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 text-center shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
  ),
  stateIcon: clsx(
    "mx-auto flex size-11 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]",
  ),
  statusTile: clsx(
    "rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3",
  ),
  catalog: {
    summaryGrid:
      "grid gap-3 px-4 pb-2 pt-3 lg:grid-cols-3",
    summaryGridFour:
      "grid gap-3 px-4 pb-2 pt-3 lg:grid-cols-3 2xl:grid-cols-4",
    summaryCard:
      "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    toolbar:
      "px-4 pb-2 pt-3",
    toolbarShell:
      "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    toolbarRow:
      "flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between",
    toolbarSearch:
      "flex min-w-0 flex-1 items-center gap-2",
    toolbarActions:
      "flex shrink-0 flex-wrap items-center gap-2 sm:justify-end",
    content: "min-h-0 flex-1 overflow-y-auto px-4 py-3",
    cardGrid: "grid gap-3 lg:grid-cols-2 2xl:grid-cols-3",
    entryCard:
      "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4 shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    interactiveEntry:
      "cursor-pointer transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-[var(--theme-border-strong)] hover:shadow-[0_8px_18px_rgba(18,38,63,0.08)]",
    metricTile: "rounded-md bg-[var(--theme-bg-sidebar)] p-2",
    iconBox:
      "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]",
    compactIconBox:
      "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]",
    title: "text-sm font-semibold text-[var(--theme-text)]",
    body: "text-xs leading-5 text-[var(--theme-text-secondary)]",
    muted: "text-[var(--theme-text-secondary)]",
    weak: "text-[var(--theme-text-tertiary)]",
    label: "text-[11px] font-medium text-[var(--theme-text-tertiary)]",
    chip:
      "rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-xs font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]",
    emptyState:
      "flex h-full min-h-72 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-6 py-10 text-center",
    emptyIcon:
      "flex size-11 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]",
    emptyTitle: "mt-3 text-sm font-semibold text-[var(--theme-text)]",
    emptyDescription:
      "mt-1 max-w-md text-xs leading-5 text-[var(--theme-text-secondary)]",
  },
  mutedText: "text-[var(--theme-text-secondary)]",
  label:
    "text-[11px] font-semibold uppercase text-[var(--theme-text-tertiary)]",
};
