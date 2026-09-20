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
  const knowledgeRequestGeneration = useRef(0);
  const knowledgeBlockingGeneration = useRef(0);
  const knowledgeSourceGenerations = useRef(new Map<string, number>());
  const knowledgeProfileGeneration = useRef(0);
  const knowledgeRouteMounted = useRef(true);
  const [knowledgeCatalog, setKnowledgeCatalog] = useState<KnowledgeBuilderCatalog>({
    sources: [],
    next_cursor: null,
    limit: 50,
    retrieval_profiles: [],
  });
  const [knowledgeLoaded, setKnowledgeLoaded] = useState(false);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null);

  const loadKnowledgeCatalog = useCallback(async (params: {
    cursor?: string | null;
    q?: string;
    selectedSourceIds?: readonly string[];
    replace?: boolean;
  } = {}) => {
    const generation = ++knowledgeRequestGeneration.current;
    const blocking = params.replace === true;
    const blockingGeneration = blocking
      ? ++knowledgeBlockingGeneration.current
      : null;
    if (blocking) {
      setKnowledgeLoading(true);
      setKnowledgeError(null);
    }
    try {
      const next = await knowledgeApi.builderCatalog(params);
      if (!knowledgeRouteMounted.current) return next;
      setKnowledgeLoaded(true);
      setKnowledgeCatalog((current) => {
        const sources = new Map(
          current.sources.map((source) => [source.id, source]),
        );
        next.sources.forEach((source) => {
          const appliedGeneration = knowledgeSourceGenerations.current.get(source.id) ?? 0;
          if (generation < appliedGeneration) return;
          sources.set(source.id, source);
          knowledgeSourceGenerations.current.set(source.id, generation);
        });
        const retrievalProfiles = generation >= knowledgeProfileGeneration.current
          ? next.retrieval_profiles
          : current.retrieval_profiles;
        if (generation >= knowledgeProfileGeneration.current) {
          knowledgeProfileGeneration.current = generation;
        }
        return {
          ...current,
          sources: [...sources.values()],
          retrieval_profiles: retrievalProfiles,
        };
      });
      return next;
    } catch {
      if (
        knowledgeRouteMounted.current &&
        blocking &&
        blockingGeneration === knowledgeBlockingGeneration.current
      ) {
        setKnowledgeError(BUILDER_CATALOG_LOAD_ERROR);
      }
    } finally {
      if (
        knowledgeRouteMounted.current &&
        blocking &&
        blockingGeneration === knowledgeBlockingGeneration.current
      ) {
        setKnowledgeLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    knowledgeRouteMounted.current = true;
    return () => {
      knowledgeRouteMounted.current = false;
    };
  }, []);

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
      knowledgeSources: knowledgeCatalog.sources,
      retrievalProfiles: knowledgeCatalog.retrieval_profiles,
      loadKnowledgeSources: loadKnowledgeCatalog,
      skillsResolved: catalogReadResolved,
      mcpToolsResolved: !toolsLoading && toolsError === null,
      knowledgeResolved: knowledgeLoaded && !knowledgeLoading && knowledgeError === null,
      effectivePermissionsKnown,
      isLoading: skillsLoading || toolsLoading,
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
      knowledgeLoaded,
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
