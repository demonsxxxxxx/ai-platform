// Adapted from assistant-ui Elements loading-state.tsx at 727a02dc (MIT).
import type { ComponentProps } from "react";
import { clsx } from "clsx";

/** One generation cue: a shimmering label, or an announced-only status while text streams. */
export function GenerationLoader({ label, visuallyHidden = false, className, ...props }: Omit<ComponentProps<"div">, "children"> & {
  label: string;
  visuallyHidden?: boolean;
}) {
  return <div data-slot="generation-loader" role="status" aria-live="polite"
    className={clsx(visuallyHidden ? "sr-only" : "my-2 text-sm", className)} {...props}>
    <span className={visuallyHidden ? undefined : "aui-shimmer"}>{label}</span>
  </div>;
}
