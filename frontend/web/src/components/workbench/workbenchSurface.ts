import { clsx } from "clsx";

export const workbenchSurface = {
  root: clsx(
    "flex min-h-0 flex-1 bg-stone-50 text-stone-900",
    "dark:bg-stone-950 dark:text-stone-100",
  ),
  workspace: clsx(
    "mx-auto grid min-h-0 w-full max-w-[1680px] flex-1 grid-cols-1",
    "xl:grid-cols-[minmax(0,1fr)_20rem]",
  ),
  cockpit: clsx(
    "grid min-h-0 grid-cols-1 gap-3",
    "xl:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]",
  ),
  thread: clsx(
    "workbench-thread-frame flex min-w-0 flex-1 flex-col border-r border-stone-200/70",
    "bg-white/95 dark:border-stone-800/70 dark:bg-stone-950",
  ),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-3 sm:px-4",
  composer: clsx(
    "shrink-0 border-t border-stone-200/70 bg-white/95 px-3 py-3",
    "dark:border-stone-800/70 dark:bg-stone-950",
  ),
  context: clsx(
    "hidden min-h-0 w-80 shrink-0 flex-col bg-stone-50/80",
    "dark:bg-stone-950/80 xl:flex",
  ),
  panel: clsx(
    "rounded-lg border border-stone-200 bg-white shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  compactPanel: clsx(
    "rounded-lg border border-stone-200 bg-white shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  mutedText: "text-stone-500 dark:text-stone-400",
  label: "text-[11px] font-semibold uppercase text-stone-400 dark:text-stone-500",
};
