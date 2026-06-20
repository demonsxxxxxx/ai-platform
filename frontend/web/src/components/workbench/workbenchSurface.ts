import { clsx } from "clsx";

export const workbenchSurface = {
  root: clsx(
    "flex min-h-0 flex-1 bg-stone-50 text-stone-900",
    "dark:bg-stone-950 dark:text-stone-100",
  ),
  workspace: clsx("mx-auto flex min-h-0 w-full max-w-[1680px] flex-1"),
  rail: clsx(
    "workbench-rail hidden w-14 shrink-0 flex-col items-center gap-2",
    "border-r border-stone-200/70 bg-white/80 py-3",
    "dark:border-stone-800/70 dark:bg-stone-950/70 xl:flex",
  ),
  railButton: clsx(
    "flex h-10 w-10 items-center justify-center rounded-lg",
    "text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-900",
    "focus:outline-none focus-visible:ring-2 focus-visible:ring-stone-400",
    "dark:text-stone-400 dark:hover:bg-stone-900 dark:hover:text-stone-100",
  ),
  thread: clsx(
    "flex min-w-0 flex-1 flex-col border-r border-stone-200/70",
    "bg-white/95 dark:border-stone-800/70 dark:bg-stone-950",
  ),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-3 sm:px-4",
  composer: clsx(
    "shrink-0 border-t border-stone-200/70 bg-white/95 px-3 py-3",
    "dark:border-stone-800/70 dark:bg-stone-950",
  ),
  context: clsx(
    "hidden min-h-0 w-80 shrink-0 flex-col bg-stone-50/80",
    "dark:bg-stone-950/80 2xl:flex",
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
