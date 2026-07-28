import { useMemo, useState } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { PublicSkillResponse, ToolState } from "../../../types";
import { AgentBuilderWorkbenchHarness } from "../AgentBuilderWorkbench";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
} from "../agentBuilderAdapter";

interface MountedHarness {
  calls: Array<{
    message: string;
    options: Record<string, boolean | string | number> | undefined;
    selectedSkill: unknown;
    selectedMcpToolIds: readonly string[];
  }>;
  handoffs: Array<{ path: string; sessionId: string; runId: string }>;
  unmount: () => void;
}

function publicSkill(): PublicSkillResponse {
  return {
    name: "document-review",
    expected_version: "2026.07.27",
    input_modes: [],
    requires_file: false,
    description: "Review an authorized document.",
    tags: [],
    enabled: true,
    source: "manual",
    files: {},
    file_count: 0,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: false,
  };
}

export function mountAgentBuilderHarness(container: HTMLElement): MountedHarness {
  const calls: MountedHarness["calls"] = [];
  const handoffs: MountedHarness["handoffs"] = [];

  function Harness() {
    const [identity, setIdentity] = useState<{ sessionId: string; runId: string } | null>(null);
    const catalog = useMemo(() => {
      const tools: Array<ToolState & { label?: string }> = [
        {
          name: "mcp:knowledge:search",
          label: "Knowledge search",
          description: "Search the authorized knowledge base.",
          category: "mcp",
          parameters: [],
          enabled: true,
        },
      ];
      return {
        skills: mapAuthorizedBuilderSkills({
          skills: [publicSkill()],
          catalogReadResolved: true,
          effectivePermissionsKnown: true,
        }),
        tools: mapSafeBuilderMcpTools(tools),
        models: [
          {
            id: "platform-model",
            value: "platform/model",
            label: "Platform model",
          },
        ],
        skillsResolved: true,
        mcpToolsResolved: true,
        modelsResolved: true,
        effectivePermissionsKnown: true,
        isLoading: false,
        error: null,
        retry: () => {},
      };
    }, []);

    return (
      <AgentBuilderWorkbenchHarness
        catalog={catalog}
        chat={{
          sendMessage: async (
            message,
            options,
            _attachments,
            selectedSkill,
            selectedMcpToolIds = [],
          ) => {
            calls.push({ message, options, selectedSkill, selectedMcpToolIds });
            setIdentity({ sessionId: "session-mounted", runId: "run-mounted" });
            return { status: "accepted" };
          },
        }}
        chatIdentity={identity}
        onHandoffReady={(path, nextIdentity) => {
          handoffs.push({
            path,
            sessionId: nextIdentity.sessionId,
            runId: nextIdentity.runId,
          });
        }}
      />
    );
  }

  const root: Root = createRoot(container);
  root.render(<Harness />);
  return { calls, handoffs, unmount: () => root.unmount() };
}
