import type { ReactNode } from "react";
import { libreChatSurface } from "./libreChatSurface";

export interface LibreChatShellProps {
  children: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

export function LibreChatShell({
  children,
  composer,
  rightPanel,
}: LibreChatShellProps) {
  return (
    <section
      className={libreChatSurface.root}
      data-librechat-shell="phase1"
      data-phase1-closure-shell
    >
      <div className={libreChatSurface.workspace}>
        <div className={libreChatSurface.thread}>
          <div
            data-workbench-region="thread"
            className={libreChatSurface.threadBody}
          >
            {children}
          </div>
          {composer && (
            <div
              data-workbench-region="composer"
              className={libreChatSurface.composer}
            >
              {composer}
            </div>
          )}
        </div>

        <div
          data-workbench-region="context"
          className={libreChatSurface.context}
        >
          {rightPanel}
        </div>
      </div>
    </section>
  );
}
