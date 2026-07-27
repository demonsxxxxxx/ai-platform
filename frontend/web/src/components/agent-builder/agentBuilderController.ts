import type {
  SubmissionOutcome,
  UseAgentReturn,
} from "../../hooks/useAgent/types";
import type { MessageAttachment, SelectedSkillRequest } from "../../types";
import { APP_ROUTE_PATHS } from "../../appRouteManifest";
import {
  prepareAgentBuilderSubmission,
  type AgentBuilderCurrentCatalog,
  type AgentBuilderDraft,
  type AgentBuilderSubmissionBlockCode,
} from "./agentBuilderAdapter";

export interface AgentBuilderChatIdentity {
  sessionId: string;
  runId: string;
}

export type AgentBuilderControllerState =
  | { phase: "ready"; generation: number }
  | { phase: "submitting"; generation: number }
  | { phase: "awaiting_chat_identity"; generation: number }
  | {
      phase: "handoff_ready";
      generation: number;
      identity: AgentBuilderChatIdentity;
      path: string;
    }
  | {
      phase: "blocked";
      generation: number;
      code: AgentBuilderSubmissionBlockCode;
      sanitizedDraft: AgentBuilderDraft;
    }
  | { phase: "error"; generation: number; code: string };

/**
 * Builder-only adapter around one existing useAgent.sendMessage call. The
 * fifth argument is captured by the workbench's existing MCP option getter;
 * useAgent remains the sole owner of the request, session, run, and recovery.
 */
export interface AgentBuilderChatSubmitSeam {
  sendMessage: (
    content: string,
    agentOptions?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
    selectedSkill?: SelectedSkillRequest | null,
    selectedMcpToolIds?: readonly string[],
  ) => ReturnType<UseAgentReturn["sendMessage"]> | Promise<SubmissionOutcome>;
}

/**
 * Headless coordination around the existing Chat controller. It never calls
 * sessionApi directly and never manufactures a session, run, or transcript.
 */
export class AgentBuilderController {
  private stateValue: AgentBuilderControllerState = {
    phase: "ready",
    generation: 0,
  };

  get state(): AgentBuilderControllerState {
    return this.stateValue;
  }

  /** Fence delayed submissions when the active local draft is replaced. */
  invalidateDraft(): AgentBuilderControllerState {
    this.stateValue = {
      phase: "ready",
      generation: this.stateValue.generation + 1,
    };
    return this.stateValue;
  }

  /** Revalidate one draft and delegate at most one admission to useAgent. */
  async submit(
    draft: AgentBuilderDraft,
    catalog: AgentBuilderCurrentCatalog,
    chat: AgentBuilderChatSubmitSeam,
  ): Promise<AgentBuilderControllerState> {
    if (
      this.stateValue.phase === "submitting" ||
      this.stateValue.phase === "awaiting_chat_identity" ||
      this.stateValue.phase === "handoff_ready"
    ) {
      return this.stateValue;
    }
    const generation = this.stateValue.generation;
    const preparation = prepareAgentBuilderSubmission(draft, catalog);
    if (preparation.kind === "blocked") {
      this.stateValue = {
        phase: "blocked",
        generation,
        code: preparation.code,
        sanitizedDraft: preparation.sanitizedDraft,
      };
      return this.stateValue;
    }

    this.stateValue = { phase: "submitting", generation };
    const outcome = await chat.sendMessage(
      preparation.submission.message,
      preparation.submission.agentOptions,
      undefined,
      preparation.submission.selectedSkill,
      preparation.submission.selectedMcpToolIds,
    );

    if (this.stateValue.generation !== generation) {
      return this.stateValue;
    }

    if (outcome.status !== "accepted") {
      this.stateValue = {
        phase: "error",
        generation,
        code: outcome.status === "recoverable_error" ? outcome.code : "chat_submit_failed",
      };
      return this.stateValue;
    }

    this.stateValue = { phase: "awaiting_chat_identity", generation };
    return this.stateValue;
  }

  /**
   * Accept only IDs observed from useAgent after its real admission response.
   * The existing Chat route owns history load, SSE reconnect, and transcript.
   */
  acceptChatIdentity(
    identity: AgentBuilderChatIdentity | null,
  ): AgentBuilderControllerState {
    if (
      this.stateValue.phase !== "awaiting_chat_identity" ||
      !identity?.sessionId.trim() ||
      !identity.runId.trim()
    ) {
      return this.stateValue;
    }

    this.stateValue = {
      phase: "handoff_ready",
      generation: this.stateValue.generation,
      identity,
      path: APP_ROUTE_PATHS.chat.replace(
        ":sessionId?",
        encodeURIComponent(identity.sessionId),
      ),
    };
    return this.stateValue;
  }
}
