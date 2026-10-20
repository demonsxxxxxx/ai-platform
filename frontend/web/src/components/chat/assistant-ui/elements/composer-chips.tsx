// Adapted from assistant-ui Elements composer.tsx attachment chip at 727a02dc (MIT).
import { Button } from "@lobehub/ui/base-ui";
import { X } from "lucide-react";
import type { ComponentType } from "react";
import { clsx } from "clsx";

export function ComposerChip({ id, kind, label, state, description, referenceId, icon: Icon, statusLabel, removeLabel, onRemove }: {
  id: string; kind: string; label: string; state: string; description?: string; referenceId?: string;
  icon: ComponentType<{ size?: number; className?: string }>; statusLabel: string; removeLabel: string; onRemove: (id: string) => void;
}) {
  const unavailable = state === "unavailable" || state === "admin-only" || state === "denied";
  return <span data-slot="composer-context-chip" data-composer-chip-kind={kind} data-composer-chip-state={state}
    data-composer-chip-reference={referenceId ?? id} data-task-selected-skill-chip={kind === "skill" ? "" : undefined}
    title={description ?? label} className={clsx("inline-flex max-w-[220px] items-center gap-1.5 rounded-xl border pl-2.5 text-xs",
      unavailable ? "border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] text-[var(--theme-warning)]" :
        "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]")}>
    <Icon size={13} className="shrink-0" />
    <span className="truncate font-medium">{label}</span>
    {state !== "enabled" && <span className="shrink-0 text-xs opacity-80">{statusLabel}</span>}
    <Button type="text" htmlType="button" aria-label={removeLabel} onClick={() => onRemove(id)} icon={<X size={13} />}
      style={{ width: 44, height: 44, padding: 0, flexShrink: 0 }} data-task-selected-skill-remove={kind === "skill" ? "" : undefined} />
  </span>;
}
