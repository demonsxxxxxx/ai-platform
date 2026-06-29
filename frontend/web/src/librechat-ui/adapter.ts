import type {
  MessageAttachment,
  PendingApproval,
  SkillResponse,
  ToolState,
} from "../types";
import type { FrontendGovernanceState } from "../components/governance/frontendGovernanceState";

export interface SessionSummary {
  id: string;
  title: string;
  updatedAt?: string | null;
  projectId?: string | null;
  unreadCount?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  createdAt?: string | null;
}

export interface ComposerChip {
  id: string;
  label: string;
  kind: "skill" | "mcp" | "file" | "model" | "agent";
  state?: "ready" | "disabled" | "degraded" | "forbidden";
}

export interface ComposerInput {
  text: string;
  attachments?: MessageAttachment[];
  skillIds?: string[];
  mcpToolIds?: string[];
}

export interface RunEventSubscription {
  close(): void;
}

export interface ChatWorkbenchAdapter {
  governanceState: FrontendGovernanceState;
  sessions: SessionSummary[];
  messages: ChatMessage[];
  selectedSkillChips: ComposerChip[];
  selectedMcpChips: ComposerChip[];
  availableSkills?: SkillResponse[];
  availableTools?: ToolState[];
  pendingApprovals?: PendingApproval[];
  sendMessage(input: ComposerInput): Promise<void>;
  subscribeRunEvents(runId: string): RunEventSubscription;
  openArtifact(artifactId: string): void;
}
