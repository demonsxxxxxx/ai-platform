import type { Dispatch, SetStateAction } from "react";
import type { FeaturePanel } from "../selectors/FeatureMenu";
import type { ModelOption } from "../../services/api/modelPublic";
import type {
  ToolState,
  ToolCategory,
  PublicSkillResponse,
  SelectedSkillRequest,
  AgentOption,
  MessageAttachment,
  PersonaPreset,
  PersonaPresetSnapshot,
} from "../../types";
import type { SubmissionOutcome } from "../../hooks/useAgent/types";
import type {
  SelectedSkillRecoverableCode,
  SelectedSkillTaskState,
} from "../../hooks/useSelectedSkillTask";

export interface ChatInputProps {
  draft?: string;
  onDraftChange?: Dispatch<SetStateAction<string>>;
  onSend: (
    message: string,
    options?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
    selectedSkill?: SelectedSkillRequest | null,
  ) => Promise<SubmissionOutcome>;
  onStop: () => void;
  isLoading: boolean;
  disabled?: boolean;
  canSend?: boolean;
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
  personaPresets?: PersonaPreset[];
  personaPresetsTotal?: number;
  personaPresetsPage?: number;
  onPersonaPresetsPageChange?: (page: number) => void;
  onPersonaPresetsSearchChange?: (query: string) => void;
  onPersonaPresetsTagChange?: (tag: string | null) => void;
  selectedPersonaPresetId?: string | null;
  selectedPersonaName?: string | null;
  personaPresetsLoading?: boolean;
  personaPresetsMutating?: boolean;
  onUsePersonaPreset?: (
    preset: PersonaPreset,
  ) => Promise<PersonaPresetSnapshot | null>;
  onCopyPersonaPreset?: (preset: PersonaPreset) => Promise<void>;
  onSavePersonaPreset?: (
    preset: PersonaPreset | null,
    data: {
      name: string;
      description: string;
      system_prompt: string;
      tags: string[];
      skill_names: string[];
    },
  ) => Promise<void>;
  onClearPersonaPreset?: () => void;
  canManagePersonaPresets?: boolean;
  agentOptions?: Record<string, AgentOption>;
  agentOptionValues?: Record<string, boolean | string | number>;
  onToggleAgentOption?: (key: string, value: boolean | string | number) => void;
  agents?: { id: string; name: string; description: string }[];
  currentAgent?: string;
  onSelectAgent?: (id: string) => void;
  availableModels?: ModelOption[];
  currentModelId?: string;
  onSelectModel?: (modelId: string, modelValue: string) => void;
  attachments?: MessageAttachment[];
  onAttachmentsChange?: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
  onMentionQueryChange?: (query: string | null) => void;
  pendingInput?: string | null;
  onPendingInputConsumed?: () => void;
  className?: string;

  /** INTERNAL: panel state lifted from ChatInput for ChatView layout. */
  activePanel?: FeaturePanel;
  onActivePanelChange?: (panel: FeaturePanel) => void;
}
