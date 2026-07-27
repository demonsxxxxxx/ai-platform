import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../../hooks/useAuth";
import { useSkills } from "../../hooks/useSkills";
import { useTools } from "../../hooks/useTools";
import { modelPublicApi, type ModelOption } from "../../services/api/modelPublic";
import { AgentBuilderWorkbench } from "./AgentBuilderWorkbench";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
} from "./agentBuilderAdapter";

/** Activated route bridge: catalog refresh stays outside the draft submission authority. */
export function AgentBuilderRoute() {
  const navigate = useNavigate();
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
      .catch((error: unknown) => {
        if (!current || controller.signal.aborted) return;
        setModels([]);
        setModelsError(error instanceof Error ? error.message : "Unable to load models.");
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
      error: skillsError || toolsError || modelsError,
      retry: retryCatalog,
    }),
    [
      catalogReadResolved,
      effectivePermissionsKnown,
      models,
      modelsError,
      modelsLoading,
      retryCatalog,
      skills,
      skillsError,
      skillsLoading,
      tools,
      toolsError,
      toolsLoading,
    ],
  );

  return (
    <AgentBuilderWorkbench
      catalog={catalog}
      canManageProfiles={user?.is_admin === true}
      onHandoffReady={(path) => navigate(path, { replace: true })}
    />
  );
}
