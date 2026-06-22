import { clsx } from "clsx";

export const workbenchSurface = {
  root: clsx(
    "flex min-h-0 flex-1 bg-slate-50 text-slate-950",
    "dark:bg-stone-950 dark:text-stone-100",
  ),
  workspace: clsx(
    "grid min-h-0 w-full flex-1 grid-cols-1",
  ),
  thread: clsx(
    "workbench-thread-frame flex min-w-0 flex-1 flex-col",
    "border-r border-slate-200/80 bg-white",
    "dark:border-stone-800 dark:bg-stone-950",
  ),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-2 sm:px-4",
  composer: clsx(
    "shrink-0 border-t border-slate-200/80 bg-white px-3 py-2.5",
    "dark:border-stone-800 dark:bg-stone-950",
  ),
  panel: clsx(
    "rounded-lg border border-slate-200 bg-white shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  compactPanel: clsx(
    "rounded-lg border border-slate-200 bg-white shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  commandSurface: clsx(
    "rounded-lg border border-slate-200 bg-white shadow-[0_18px_40px_rgba(15,23,42,0.12)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  unavailable: clsx(
    "rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm leading-6 text-slate-600",
    "dark:border-stone-700 dark:bg-stone-950 dark:text-stone-300",
  ),
  statusTile: clsx("rounded-md bg-slate-50 p-3", "dark:bg-stone-950/70"),
  mutedText: "text-slate-500 dark:text-stone-400",
  label:
    "text-[11px] font-semibold uppercase text-slate-400 dark:text-stone-500",
};
