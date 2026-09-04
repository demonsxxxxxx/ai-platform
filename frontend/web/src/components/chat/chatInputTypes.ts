import type { FeaturePanel } from "../selectors/FeatureMenu";
import type { ModelOption } from "../../services/api/modelPublic";
import type {
  ToolState,
  ToolCategory,
  PublicSkillResponse,
  SelectedSkillRequest,
  AgentOption,
  MessageAttachment,
} from "../../types";
import type {
  StopGenerationResult,
  SubmissionOutcome,
} from "../../hooks/useAgent/types";
import type {
  SelectedSkillRecoverableCode,
  SelectedSkillTaskState,
} from "../../hooks/useSelectedSkillTask";

export interface ChatInputDraftSnapshot {
  value: string;
  appliedInitialDraftKey: string | null;
  scopeKey: string | null | undefined;
  revision: number;
  selectedSkillState: SelectedSkillTaskState | undefined;
  selectedSkillRevision: number;
  pendingScopeHandoff: boolean;
  apply?: (value: string) => void;
}

export interface ChatInputProps {
  /** One-time draft for a route/session identity; does not replace typed text. */
  initialDraft?: string;
  initialDraftKey?: string;
  /** INTERNAL: preserves local draft across existing layout remounts. */
  draftSnapshotRef?: { current: ChatInputDraftSnapshot };
  draftScopeKey?: string | null;
  draftScopeHandoffKey?: string | null;
  onSend: (
    message: string,
    options?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
    selectedSkill?: SelectedSkillRequest | null,
  ) => Promise<SubmissionOutcome>;
  onStop: () => Promise<StopGenerationResult>;
  isLoading: boolean;
  disabled?: boolean;
  canSend?: boolean;
  /** Optional product-specific prompt for a locked composer surface. */
  placeholder?: string;
  /** Undefined preserves ordinary Chat uploads; an array scopes Agent uploads. */
  acceptedFileTypes?: string[];
  /** Agent workspaces use plain task input and do not expose composer commands. */
  disableSlashCommands?: boolean;
  tools?: ToolState[];
  onToggleTool?: (toolName: string) => void;
  onToggleCategory?: (category: ToolCategory, enabled: boolean) => void;
  onToggleAll?: (enabled: boolean) => void;
  toolsLoading?: boolean;
  enabledToolsCount?: number;
  totalToolsCount?: number;
  skills?: PublicSkillResponse[];
  selectedSkillState?: SelectedSkillTaskState;
  onSelectSkill?: (skill: PublicSkillResponse) => void;
  onClearSelectedSkill?: () => void;
  onSelectedSkillRecoverable?: (
    code: SelectedSkillRecoverableCode,
  ) => Promise<unknown>;
  onSelectedSkillFilesReady?: () => void;
  skillsLoading?: boolean;
  enabledSkillsCount?: number;
  totalSkillsCount?: number;
  enableSkills?: boolean;
  agentOptions?: Record<string, AgentOption>;
  agentOptionValues?: Record<string, boolean | string | number>;
  onToggleAgentOption?: (key: string, value: boolean | string | number) => void;
  availableModels?: ModelOption[];
  currentModelId?: string;
  onSelectModel?: (modelId: string, modelValue: string) => void;
  attachments?: MessageAttachment[];
  onAttachmentsChange?: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
  pendingInput?: string | null;
  onPendingInputConsumed?: () => void;
  className?: string;

  /** INTERNAL: panel state lifted from ChatInput for ChatView layout. */
  activePanel?: FeaturePanel;
  onActivePanelChange?: (panel: FeaturePanel) => void;
}
