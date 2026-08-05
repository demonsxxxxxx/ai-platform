import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../../hooks/useAuth";
import { useSkills } from "../../hooks/useSkills";
import { useTools } from "../../hooks/useTools";
import { modelPublicApi, type ModelOption } from "../../services/api/modelPublic";
import { AgentBuilderWorkbench } from "./AgentBuilderWorkbench";
import { AgentBuilderShell } from "./AgentBuilderShell";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
} from "./agentBuilderAdapter";

const BUILDER_CATALOG_LOAD_ERROR = "暂时无法加载授权目录，请稍后刷新后重试。";

/** Admin route bridge for current model, Skill, and MCP catalog projections. */
export function AgentBuilderRoute() {
  const { user } = useAuth();
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelRequestRevision, setModelRequestRevision] = useState(0);
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

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setModelsLoading(true);
    setModelsError(null);

    void modelPublicApi
      .listAvailable({ signal: controller.signal })
      .then((response) => {
        if (current) setModels(response.models);
      })
      .catch(() => {
        if (!current || controller.signal.aborted) return;
        setModels([]);
        setModelsError(BUILDER_CATALOG_LOAD_ERROR);
      })
      .finally(() => {
        if (current) setModelsLoading(false);
      });

    return () => {
      current = false;
      controller.abort();
    };
  }, [modelRequestRevision]);

  const retryCatalog = useCallback(() => {
    void fetchSkills();
    void refreshTools();
    setModelRequestRevision((revision) => revision + 1);
  }, [fetchSkills, refreshTools]);
  const catalogError = skillsError || toolsError || modelsError
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
      models,
      skillsResolved: catalogReadResolved,
      mcpToolsResolved: !toolsLoading && toolsError === null,
      modelsResolved: !modelsLoading && modelsError === null,
      effectivePermissionsKnown,
      isLoading: skillsLoading || toolsLoading || modelsLoading,
      error: catalogError,
      retry: retryCatalog,
    }),
    [
      catalogReadResolved,
      catalogError,
      effectivePermissionsKnown,
      models,
      modelsError,
      modelsLoading,
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
