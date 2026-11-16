// Adapted from assistant-ui Elements loading-state.tsx at 727a02dc (MIT).
import type { ComponentProps } from "react";
import { clsx } from "clsx";

/** One generation cue: a shimmering label, or an announced-only status while text streams. */
export function GenerationLoader({ label, elapsed, visuallyHidden = false, className, ...props }: Omit<ComponentProps<"div">, "children"> & {
  label: string;
  /** Elapsed time since the turn started, shown after the label. */
  elapsed?: string;
  visuallyHidden?: boolean;
}) {
  if (visuallyHidden) {
    return <div data-slot="generation-loader" role="status" aria-live="polite" className={clsx("sr-only", className)} {...props}>
      <span>{label}</span>
    </div>;
  }
  // Adapted from the pinned thinking-indicator element: status dot, shimmering
  // current action and a quiet elapsed counter.
  return <div data-slot="generation-loader" role="status" aria-live="polite"
    className={clsx("my-2 flex min-w-0 items-center gap-2.5 text-sm", className)} {...props}>
    <span aria-hidden className="aui-thinking-dot size-1.5 shrink-0 rounded-full bg-[var(--theme-text-secondary)]" />
    <span key={label} className="aui-shimmer aui-thinking-label min-w-0 truncate">{label}</span>
    {elapsed ? <span className="shrink-0 tabular-nums text-xs text-[var(--theme-text-tertiary)]" data-generation-elapsed>{elapsed}</span> : null}
  </div>;
}
