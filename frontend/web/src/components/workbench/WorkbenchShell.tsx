import type { ReactNode } from "react";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchShellProps {
  children: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

export function WorkbenchShell({
  children,
  composer,
  rightPanel,
}: WorkbenchShellProps) {
  return (
    <section className={workbenchSurface.root} data-phase1-closure-shell>
      <div className={workbenchSurface.workspace}>
        <div className={workbenchSurface.thread}>
          <div
            data-workbench-region="thread"
            className={workbenchSurface.threadBody}
          >
            {children}
          </div>
          {composer && (
            <div
              data-workbench-region="composer"
              className={workbenchSurface.composer}
            >
              {composer}
            </div>
          )}
        </div>

        <div
          data-workbench-region="context"
          className={workbenchSurface.context}
        >
          {rightPanel}
        </div>
      </div>
    </section>
  );
}
