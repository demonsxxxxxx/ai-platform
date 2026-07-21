import { clsx } from "clsx";
import { LIBRECHAT_UI_SOURCE } from "./source";

export const LIBRECHAT_SHELL_REFERENCE = {
  repository: LIBRECHAT_UI_SOURCE.repository,
  commit: LIBRECHAT_UI_SOURCE.commit,
  sourcePaths: LIBRECHAT_UI_SOURCE.sourcePaths,
  license: LIBRECHAT_UI_SOURCE.license,
  intake:
    "Port shell structure and interaction geometry under a pinned MIT upstream while keeping ai-platform data authority.",
} as const;

export const LIBRECHAT_SHELL_GEOMETRY = {
  railWidthPx: 52,
  expandedMinWidthPx: 288,
  mobileMaxWidth: "min(85vw, 380px)",
} as const;

export const FORBIDDEN_LIBRECHAT_IMPORTS = [
  /librechat-data-provider/,
  /useRecoilState/,
  /~\/Providers/,
  /~\/store/,
  /useChatHelpers/,
  /useGetStartupConfig/,
] as const;

export const libreChatSurface = {
  root: clsx(
    "librechat-shell-root flex min-h-0 flex-1 bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]",
  ),
  workspace: clsx(
    "librechat-shell-workspace grid min-h-0 w-full flex-1 grid-cols-1",
  ),
  workspaceWithContext: clsx(
    "librechat-shell-workspace grid min-h-0 w-full flex-1 grid-cols-1",
    "xl:grid-cols-[minmax(0,1fr)_minmax(18rem,20rem)]",
  ),
  thread: clsx(
    "librechat-shell-thread workbench-thread-frame flex min-w-0 flex-1 flex-col bg-[var(--theme-workbench-canvas)]",
  ),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-2 sm:px-4",
  composer: "shrink-0 bg-[var(--theme-workbench-canvas)] px-3 py-2.5",
  context: clsx(
    "hidden min-h-0 min-w-0 w-full flex-col border-l border-[var(--theme-border)]",
    "bg-[var(--theme-workbench-canvas)] xl:flex",
  ),
  panel: clsx(
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)]",
    "shadow-[0_1px_2px_rgba(15,23,42,0.04)]",
  ),
  commandSurface: clsx(
    "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)]",
    "shadow-[0_18px_40px_rgba(15,23,42,0.12)]",
  ),
} as const;
