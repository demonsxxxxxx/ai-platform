import { WorkbenchStateSurface } from "../../workbench/WorkbenchStateSurface";

export function QuarantinedLegacyPanel() {
  return (
    <div className="flex h-full items-center justify-center bg-[var(--theme-bg)] p-6">
      <WorkbenchStateSurface
        state="degraded"
        surface="legacy-route-quarantine"
        title="Legacy surface quarantined"
        description="This admin surface is disabled until it is remapped to ai-platform public or same-tenant admin projections."
      />
    </div>
  );
}
