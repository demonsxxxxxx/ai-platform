// Adapted from assistant-ui Elements connection-state.tsx at 727a02dc (MIT).
import { Button } from "@lobehub/ui/base-ui";
import { CloudOff, LoaderCircle } from "lucide-react";

export function ConnectionState({ disconnected, pending, label, retryLabel, onRetry, status }: {
  disconnected: boolean; pending: boolean; label: string; retryLabel: string;
  onRetry: () => void; status: string;
}) {
  return <div data-slot="connection-state" data-chat-connection-status={status} role="status" aria-live="polite" aria-atomic="true" aria-busy={!disconnected || pending}
    className="flex w-full items-center gap-2.5 rounded-2xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3.5 py-2.5 text-sm">
    {disconnected ? <CloudOff aria-hidden className="size-4 shrink-0 text-[var(--theme-warning)]" /> :
      <LoaderCircle aria-hidden className="size-4 shrink-0 animate-spin text-[var(--theme-text-secondary)] motion-reduce:animate-none" />}
    <span className="min-w-0 flex-1">{label}</span>
    {disconnected && <Button type="text" htmlType="button" size="small" disabled={pending} onClick={onRetry}>{retryLabel}</Button>}
  </div>;
}
