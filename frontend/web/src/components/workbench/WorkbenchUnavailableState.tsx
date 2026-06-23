import type { ElementType } from "react";
import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import { WorkbenchStateSurface } from "./WorkbenchStateSurface";

export interface WorkbenchUnavailableStateProps {
  title: string;
  description: string;
  icon?: ElementType;
  surface: string;
  state?: FrontendGovernanceState;
  details?: string[];
}

export function WorkbenchUnavailableState({
  title,
  description,
  icon,
  surface,
  state = "forbidden",
  details,
}: WorkbenchUnavailableStateProps) {
  return (
    <div data-workbench-unavailable>
      <WorkbenchStateSurface
        state={state}
        title={title}
        description={description}
        icon={icon}
        surface={surface}
        details={details}
      />
    </div>
  );
}
