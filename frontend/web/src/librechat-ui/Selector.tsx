import type { ReactNode } from "react";

/** Tags selector content as part of the pinned LibreChat UI layer. */
export function LibreChatSelectorLayer({ children }: { children: ReactNode }) {
  return (
    <div className="contents" data-librechat-selector-layer>
      {children}
    </div>
  );
}

/** Tags an active selector modal without owning its backend data source. */
export function LibreChatSelectorModal({
  panel,
  children,
}: {
  panel: string;
  children: ReactNode;
}) {
  return (
    <div className="contents" data-librechat-selector-modal={panel}>
      {children}
    </div>
  );
}
