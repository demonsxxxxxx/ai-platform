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
    "rounded-lg border border-slate-200/80 bg-[var(--theme-bg-card)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  secondaryPanel: clsx(
    "rounded-lg border border-slate-200/80 bg-[var(--theme-bg-card)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  sectionPanel:
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
  compactPanel: clsx(
    "rounded-lg border border-slate-200/80 bg-[var(--theme-bg-card)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  commandSurface: clsx(
    "rounded-lg border border-slate-200 bg-[var(--theme-bg-card)] shadow-[0_18px_40px_rgba(15,23,42,0.12)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  unavailable: clsx(
    "rounded-lg border border-dashed border-slate-300 bg-[var(--theme-bg-card)] p-4 text-sm leading-6 text-slate-600",
    "dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300",
  ),
  stateSurface: clsx(
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-5 text-center shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  stateIcon: clsx(
    "mx-auto flex size-11 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-slate-500 ring-1 ring-[var(--theme-border)]",
    "dark:bg-stone-950 dark:text-stone-300 dark:ring-stone-800",
  ),
  statusTile: clsx(
    "rounded-md border border-slate-200/70 bg-[var(--theme-bg-card)] p-3",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  mutedText: "text-slate-500 dark:text-stone-400",
  label:
    "text-[11px] font-semibold uppercase text-slate-400 dark:text-stone-500",
};
