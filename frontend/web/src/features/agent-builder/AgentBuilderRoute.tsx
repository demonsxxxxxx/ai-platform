import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "../../hooks/useAuth";
import { useSkills } from "../../hooks/useSkills";
import { useTools } from "../../hooks/useTools";
import { knowledgeApi } from "../../services/api/knowledge";
import type { KnowledgeBuilderCatalog } from "../../types";
import { AgentBuilderWorkbench } from "./AgentBuilderWorkbench";
import { AgentBuilderShell } from "./AgentBuilderShell";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
} from "./agentBuilderAdapter";

const BUILDER_CATALOG_LOAD_ERROR = "暂时无法加载授权目录，请稍后刷新后重试。";

/** Admin route bridge for the current Skill, MCP, and Knowledge catalogs. */
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
  const knowledgeLoadGeneration = useRef(0);
  const [knowledgeCatalog, setKnowledgeCatalog] = useState<KnowledgeBuilderCatalog>({
    sources: [],
    next_cursor: null,
    limit: 50,
    retrieval_profiles: [],
  });
  const [knowledgeLoading, setKnowledgeLoading] = useState(true);
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null);

  const loadKnowledgeCatalog = useCallback(async (params: {
    cursor?: string | null;
    q?: string;
    selectedSourceIds?: readonly string[];
    replace?: boolean;
  } = {}) => {
    const generation = ++knowledgeLoadGeneration.current;
    const blocking = params.replace === true;
    if (blocking) {
      setKnowledgeLoading(true);
      setKnowledgeError(null);
    }
    try {
      const next = await knowledgeApi.builderCatalog(params);
      if (generation !== knowledgeLoadGeneration.current) return next;
      setKnowledgeCatalog((current) => {
        if (params.replace === true) return next;
        const sources = new Map(
          current.sources.map((source) => [source.id, source]),
        );
        next.sources.forEach((source) => sources.set(source.id, source));
        return {
          ...next,
          sources: [...sources.values()],
        };
      });
      return next;
    } catch {
      if (generation !== knowledgeLoadGeneration.current) return;
      if (blocking) setKnowledgeError(BUILDER_CATALOG_LOAD_ERROR);
    } finally {
      if (blocking && generation === knowledgeLoadGeneration.current) {
        setKnowledgeLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadKnowledgeCatalog({ replace: true });
    return () => {
      knowledgeLoadGeneration.current += 1;
    };
  }, [loadKnowledgeCatalog]);

  const retryCatalog = useCallback(() => {
    void fetchSkills();
    void refreshTools();
    void loadKnowledgeCatalog({ replace: true });
  }, [fetchSkills, loadKnowledgeCatalog, refreshTools]);
  const catalogError = skillsError || toolsError || knowledgeError
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
      knowledgeSources: knowledgeCatalog.sources,
      retrievalProfiles: knowledgeCatalog.retrieval_profiles,
      loadKnowledgeSources: loadKnowledgeCatalog,
      skillsResolved: catalogReadResolved,
      mcpToolsResolved: !toolsLoading && toolsError === null,
      knowledgeResolved: !knowledgeLoading && knowledgeError === null,
      effectivePermissionsKnown,
      isLoading: skillsLoading || toolsLoading || knowledgeLoading,
      error: catalogError,
      retry: retryCatalog,
    }),
    [
      catalogReadResolved,
      catalogError,
      effectivePermissionsKnown,
      knowledgeCatalog.retrieval_profiles,
      knowledgeCatalog.sources,
      knowledgeError,
      knowledgeLoading,
      loadKnowledgeCatalog,
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
