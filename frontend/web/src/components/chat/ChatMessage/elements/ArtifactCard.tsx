import type { ReactNode } from "react";
import { clsx } from "clsx";

export type ArtifactCardStatus = "legacy" | "pending" | "ready" | "failed";

export interface ArtifactCardProps {
  label: string;
  typeLabel: string;
  sizeLabel: string;
  status: ArtifactCardStatus;
  icon: ReactNode;
  iconBackgroundClassName: string;
  statusLabel?: string;
  actions?: ReactNode;
  errorMessage?: string;
}

/** ArtifactCard adapted from the pinned element; delivery actions remain caller-owned. */
export function ArtifactCard({
  label,
  typeLabel,
  sizeLabel,
  status,
  icon,
  iconBackgroundClassName,
  statusLabel,
  actions,
  errorMessage,
}: ArtifactCardProps) {
  return (
    <div data-slot="artifact-card" data-status={status} role="group" aria-label={label} className="my-1 flex min-w-0 max-w-xl flex-wrap items-center gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2.5 text-left shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:bg-neutral-900">
      <div className={clsx("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", iconBackgroundClassName)}>
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-[var(--theme-text)]">{label}</div>
        <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-[var(--theme-text-secondary)]">
          <span className="truncate">{typeLabel}</span>
          <span className="shrink-0">{sizeLabel}</span>
          {statusLabel && <span className="shrink-0" role="status">{statusLabel}</span>}
        </div>
      </div>
      {actions}
      {errorMessage && <div role="alert" className="basis-full text-xs text-[var(--theme-danger-text)]">{errorMessage}</div>}
    </div>
  );
}
