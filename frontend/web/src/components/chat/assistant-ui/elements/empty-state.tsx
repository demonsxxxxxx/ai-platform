// Adapted from assistant-ui Elements empty-state.tsx at 727a02dc (MIT).
import type { ComponentProps } from "react";
import { clsx } from "clsx";

export function EmptyState({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="empty-state" className={clsx("flex w-full flex-col items-center gap-7", className)} {...props} />;
}

export function EmptyStateGreeting({ className, ...props }: ComponentProps<"h1">) {
  return <h1 data-slot="empty-state-greeting" className={clsx("text-center text-2xl font-medium tracking-tight text-[var(--theme-text)]", className)} {...props} />;
}
