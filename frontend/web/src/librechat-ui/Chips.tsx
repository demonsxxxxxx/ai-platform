import type { ComponentType, ReactNode } from "react";
import { X } from "lucide-react";
import { clsx } from "clsx";

export interface LibreChatComposerChipProps {
  id: string;
  kind: string;
  label: string;
  state: string;
  description?: string;
  referenceId?: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  statusLabel?: ReactNode;
  removeLabel: string;
  onRemove: (id: string) => void;
}

/** Renders a removable composer chip using the pinned LibreChat-style surface. */
export function LibreChatComposerChip({
  id,
  kind,
  label,
  state,
  description,
  referenceId,
  icon: Icon,
  statusLabel,
  removeLabel,
  onRemove,
}: LibreChatComposerChipProps) {
  const unavailable =
    state === "unavailable" || state === "admin-only" || state === "denied";

  return (
    <span
      className={clsx(
        "inline-flex max-w-[220px] items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs",
        unavailable
          ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
          : "border-stone-200 bg-stone-50 text-stone-700 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-200",
      )}
      data-librechat-composer-chip
      data-composer-chip-kind={kind}
      data-composer-chip-reference={referenceId ?? id}
      data-composer-chip-state={state}
      data-state={state}
      title={description ?? `${kind}: ${label}`}
    >
      <Icon size={13} className="shrink-0" />
      <span className="truncate font-medium">{label}</span>
      {state !== "enabled" && statusLabel ? (
        <span className="shrink-0 text-[10px] opacity-80">{statusLabel}</span>
      ) : null}
      <button
        type="button"
        onClick={() => onRemove(id)}
        className="ml-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10"
        aria-label={removeLabel}
      >
        <X size={11} />
      </button>
    </span>
  );
}
