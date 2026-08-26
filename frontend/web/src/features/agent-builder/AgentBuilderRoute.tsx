import { useCallback, useMemo } from "react";

import { useAuth } from "../../hooks/useAuth";
import { useSkills } from "../../hooks/useSkills";
import { useTools } from "../../hooks/useTools";
import { AgentBuilderWorkbench } from "./AgentBuilderWorkbench";
import { AgentBuilderShell } from "./AgentBuilderShell";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
} from "./agentBuilderAdapter";

const BUILDER_CATALOG_LOAD_ERROR = "暂时无法加载授权目录，请稍后刷新后重试。";

/** Admin route bridge for the current Skill and MCP authorization catalogs. */
export function AgentBuilderRoute() {
  const { user } = useAuth();
  const {
    skills,
    catalogReadResolved,
    effectivePermissionsKnown,
    isLoading: skillsLoading,
    error: skillsError,
    fetchSkills,
  } = useSkills({ allAuthorizedCatalog: true });
  const {
    tools,
    isLoading: toolsLoading,
    error: toolsError,
    refreshTools,
  } = useTools({ enabled: true });

  const retryCatalog = useCallback(() => {
    void fetchSkills();
    void refreshTools();
  }, [fetchSkills, refreshTools]);
  const catalogError = skillsError || toolsError
    ? BUILDER_CATALOG_LOAD_ERROR
    : null;

  const catalog = useMemo(
    () => ({
      skills: mapAuthorizedBuilderSkills({
        skills,
        catalogReadResolved,
        effectivePermissionsKnown,
      }),
      tools: mapSafeBuilderMcpTools(tools),
      skillsResolved: catalogReadResolved,
      mcpToolsResolved: !toolsLoading && toolsError === null,
      effectivePermissionsKnown,
      isLoading: skillsLoading || toolsLoading,
      error: catalogError,
      retry: retryCatalog,
    }),
    [
      catalogReadResolved,
      catalogError,
      effectivePermissionsKnown,
      retryCatalog,
      skills,
      skillsLoading,
      tools,
      toolsError,
      toolsLoading,
    ],
  );

  return (
    <AgentBuilderShell>
      <AgentBuilderWorkbench
        catalog={catalog}
        canManageProfiles={user?.is_admin === true}
      />
    </AgentBuilderShell>
  );
}
