import type { ReactNode } from "react";

/** Ordered child composition adapted from the pinned SubagentList element. */
export function SubagentList({ children }: { children: ReactNode }) {
  return (
    <ol
      data-slot="subagent-list"
      className="ml-4 flex list-none flex-col gap-1 border-l-2 border-neutral-200/60 pl-2 dark:border-neutral-700/50"
    >
      {children}
    </ol>
  );
}
