// Adapted from assistant-ui Elements loading-state.tsx at 727a02dc (MIT).
import type { ComponentProps } from "react";
import { clsx } from "clsx";

export function GenerationLoader({ label, className, ...props }: Omit<ComponentProps<"div">, "children"> & { label: string }) {
  return <div data-slot="generation-loader" role="status" aria-live="polite"
    className={clsx("my-3 flex items-center gap-2 text-xs text-[var(--theme-text-secondary)]", className)} {...props}>
    <div aria-hidden className="grid grid-cols-3 gap-0.5">
      {Array.from({ length: 9 }, (_, index) => <span key={index}
        className="size-1 animate-pulse rounded-full bg-current motion-reduce:animate-none"
        style={{ animationDelay: `${index * 120}ms` }} />)}
    </div>
    <span>{label}</span>
  </div>;
}
