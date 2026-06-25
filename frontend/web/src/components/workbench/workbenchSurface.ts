import { clsx } from "clsx";

export const workbenchSurface = {
  root: clsx(
    "flex min-h-0 flex-1 bg-[var(--theme-workbench-canvas)] text-slate-950",
    "dark:bg-stone-950 dark:text-stone-100",
  ),
  page:
    "flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]",
  statePage:
    "flex h-full min-h-0 items-center justify-center bg-[var(--theme-workbench-canvas)] px-4 text-[var(--theme-text)]",
  workspace: clsx(
    "grid min-h-0 w-full flex-1 grid-cols-1",
    "xl:grid-cols-[minmax(0,1fr)_20rem]",
  ),
  thread: clsx(
    "workbench-thread-frame flex min-w-0 flex-1 flex-col",
    "border-r border-slate-200/70 bg-[var(--theme-workbench-canvas)]",
    "dark:border-stone-800 dark:bg-stone-950",
  ),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-2 sm:px-4",
  composer: clsx(
    "shrink-0 border-t border-slate-200/70 bg-[var(--theme-workbench-canvas)] px-3 py-2.5",
    "dark:border-stone-800 dark:bg-stone-950",
  ),
  context: clsx(
    "hidden min-h-0 w-80 shrink-0 flex-col border-l border-slate-200/70 bg-[var(--theme-workbench-canvas)]",
    "dark:border-stone-800 dark:bg-stone-950 xl:flex",
  ),
  panel: clsx(
    "rounded-lg border border-slate-200/80 bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  secondaryPanel: clsx(
    "rounded-lg border border-slate-200/80 bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  sectionPanel:
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
  compactPanel: clsx(
    "rounded-lg border border-slate-200/80 bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  emptyState: clsx(
    "rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4",
    "shadow-[0_1px_2px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900",
  ),
  commandSurface: clsx(
    "rounded-lg border border-slate-200 bg-[var(--theme-workbench-panel)] shadow-[0_18px_40px_rgba(15,23,42,0.12)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  unavailable: clsx(
    "rounded-lg border border-dashed border-slate-300 bg-[var(--theme-workbench-panel)] p-4 text-sm leading-6 text-slate-600",
    "dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300",
  ),
  stateSurface: clsx(
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 text-center shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  stateIcon: clsx(
    "mx-auto flex size-11 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-slate-500 ring-1 ring-[var(--theme-border)]",
    "dark:bg-stone-950 dark:text-stone-300 dark:ring-stone-800",
  ),
  statusTile: clsx(
    "rounded-md border border-slate-200/70 bg-[var(--theme-workbench-panel)] p-3",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  catalog: {
    summaryGrid:
      "grid gap-3 px-4 pb-2 pt-3 lg:grid-cols-3",
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
  mutedText: "text-slate-500 dark:text-stone-400",
  label:
    "text-[11px] font-semibold uppercase text-slate-400 dark:text-stone-500",
};
