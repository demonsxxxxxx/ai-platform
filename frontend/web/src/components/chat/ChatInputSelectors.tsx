import { useTranslation } from "react-i18next";
import { ToolSelector } from "../selectors/ToolSelector";
import { SkillSelector } from "../selectors/SkillSelector";
import { AgentModeSelector } from "../selectors/AgentModeSelector";
import { PersonaPresetSelector } from "../persona/PersonaPresetSelector";
import { AgentOptionButton } from "./AgentOptionButton";
import { ComposerModelPanel } from "./ComposerModelPanel";
import { ComposerUnavailablePanel } from "./ComposerUnavailablePanel";
import type { FeaturePanel } from "../selectors/FeatureMenu";
import type { ModelOption } from "../../services/api/modelPublic";
import type {
  ToolState,
  ToolCategory,
  PublicSkillResponse,
  AgentOption,
  PersonaPreset,
  PersonaPresetSnapshot,
} from "../../types";
import {
  LibreChatSelectorLayer,
  LibreChatSelectorModal,
} from "../../librechat-ui/Selector";

export interface ChatInputSelectorsProps {
  activePanel: FeaturePanel;
  onActivePanelChange: (panel: FeaturePanel) => void;
  commandSearchSeed?: { panel: FeaturePanel; query: string } | null;
  // Tools
  tools?: ToolState[];
  onToggleTool?: (toolName: string) => void;
  onToggleCategory?: (category: ToolCategory, enabled: boolean) => void;
  onToggleAll?: (enabled: boolean) => void;
  enabledToolsCount?: number;
  totalToolsCount?: number;
  // Skills
  skills?: PublicSkillResponse[];
  selectedSkill?: PublicSkillResponse | null;
  onSelectSkill?: (skill: PublicSkillResponse) => void;
  skillsLoading?: boolean;
  enableSkills?: boolean;
  // Persona presets
  personaPresets?: PersonaPreset[];
  personaPresetsTotal?: number;
  personaPresetsPage?: number;
  onPersonaPresetsPageChange?: (page: number) => void;
  onPersonaPresetsSearchChange?: (query: string) => void;
  onPersonaPresetsTagChange?: (tag: string | null) => void;
  selectedPersonaPresetId?: string | null;
  personaPresetsLoading?: boolean;
  personaPresetsMutating?: boolean;
  onUsePersonaPreset?: (
    preset: PersonaPreset,
  ) => Promise<PersonaPresetSnapshot | null>;
  onTogglePersonaPreference?: (
    preset: PersonaPreset,
    preference: { is_favorite?: boolean; is_pinned?: boolean },
  ) => Promise<void>;
  onCopyPersonaPreset?: (preset: PersonaPreset) => Promise<void>;
  onClearPersonaPreset?: () => void;
  canManagePersonaPresets?: boolean;
  // Agent mode
  agents?: { id: string; name: string; description: string }[];
  currentAgent?: string;
  onSelectAgent?: (id: string) => void;
  // Model selector
  availableModels?: ModelOption[];
  currentModelId?: string;
  onSelectModel?: (modelId: string, modelValue: string) => void;
  // Agent options
  agentOptions?: Record<string, AgentOption>;
  agentOptionValues?: Record<string, boolean | string | number>;
  onToggleAgentOption?: (key: string, value: boolean | string | number) => void;
}

export function ChatInputSelectors({
  activePanel,
  onActivePanelChange,
  commandSearchSeed,
  tools = [],
  onToggleTool,
  onToggleCategory,
  onToggleAll,
  enabledToolsCount = 0,
  totalToolsCount = 0,
  skills = [],
  selectedSkill,
  onSelectSkill,
  skillsLoading = false,
  enableSkills = true,
  personaPresets = [],
  personaPresetsTotal,
  personaPresetsPage,
  onPersonaPresetsPageChange,
  onPersonaPresetsSearchChange,
  onPersonaPresetsTagChange,
  selectedPersonaPresetId,
  personaPresetsLoading = false,
  personaPresetsMutating = false,
  onUsePersonaPreset,
  onTogglePersonaPreference,
  onCopyPersonaPreset,
  onClearPersonaPreset,
  canManagePersonaPresets = false,
  agents = [],
  currentAgent,
  onSelectAgent,
  availableModels = [],
  currentModelId,
  onSelectModel,
  agentOptions,
  agentOptionValues = {},
  onToggleAgentOption,
}: ChatInputSelectorsProps) {
  const { t } = useTranslation();

  return (
    <LibreChatSelectorLayer>
      {onToggleTool && onToggleCategory && onToggleAll && (
        <LibreChatSelectorModal panel="tools">
          <ToolSelector
            tools={tools}
            onToggleTool={onToggleTool}
            onToggleCategory={onToggleCategory}
            onToggleAll={onToggleAll}
            enabledCount={enabledToolsCount}
            totalCount={totalToolsCount}
            isOpen={activePanel === "tools"}
            onOpenChange={(open) => onActivePanelChange(open ? "tools" : null)}
            searchSeed={
              commandSearchSeed?.panel === "tools"
                ? commandSearchSeed.query
                : undefined
            }
          />
        </LibreChatSelectorModal>
      )}
      {enableSkills && onSelectSkill && (
          <LibreChatSelectorModal panel="skills">
            <SkillSelector
              skills={skills}
              selectedSkill={selectedSkill}
              onSelectSkill={onSelectSkill}
              isLoading={skillsLoading}
              isOpen={activePanel === "skills"}
              onOpenChange={(open) => onActivePanelChange(open ? "skills" : null)}
              searchSeed={
                commandSearchSeed?.panel === "skills"
                  ? commandSearchSeed.query
                  : undefined
              }
            />
          </LibreChatSelectorModal>
        )}
      {onUsePersonaPreset && onCopyPersonaPreset && onClearPersonaPreset && (
        <LibreChatSelectorModal panel="persona">
          <PersonaPresetSelector
            presets={personaPresets}
            total={personaPresetsTotal}
            page={personaPresetsPage}
            selectedPresetId={selectedPersonaPresetId}
            isOpen={activePanel === "persona"}
            isLoading={personaPresetsLoading}
            isMutating={personaPresetsMutating}
            canManagePresets={canManagePersonaPresets}
            onOpenChange={(open) => onActivePanelChange(open ? "persona" : null)}
            onPageChange={onPersonaPresetsPageChange}
            onSearchChange={onPersonaPresetsSearchChange}
            onTagChange={onPersonaPresetsTagChange}
            onUsePreset={onUsePersonaPreset}
            onTogglePreference={onTogglePersonaPreference}
            onCopyPreset={onCopyPersonaPreset}
            onClearPreset={() => {
              onClearPersonaPreset();
              onActivePanelChange(null);
            }}
          />
        </LibreChatSelectorModal>
      )}
      <LibreChatSelectorModal panel="agent">
        <AgentModeSelector
          agents={agents}
          currentAgent={currentAgent || ""}
          onSelectAgent={onSelectAgent}
          isOpen={activePanel === "agent"}
          onOpenChange={(open) => onActivePanelChange(open ? "agent" : null)}
          searchSeed={
            commandSearchSeed?.panel === "agent"
              ? commandSearchSeed.query
              : undefined
          }
        />
      </LibreChatSelectorModal>
      {onSelectModel && availableModels.length > 0 && (
        <LibreChatSelectorModal panel="model">
          <ComposerModelPanel
            models={availableModels}
            currentModelId={currentModelId}
            isOpen={activePanel === "model"}
            onOpenChange={(open) => onActivePanelChange(open ? "model" : null)}
            onSelectModel={onSelectModel}
            searchSeed={
              commandSearchSeed?.panel === "model"
                ? commandSearchSeed.query
                : undefined
            }
          />
        </LibreChatSelectorModal>
      )}
      <LibreChatSelectorModal panel="context">
        <ComposerUnavailablePanel
          isOpen={activePanel === "context"}
          onOpenChange={(open) => onActivePanelChange(open ? "context" : null)}
          surface="context-selector"
          title={t("composerCommand.contextSelector.title")}
          description={t("composerCommand.contextSelector.description")}
        />
      </LibreChatSelectorModal>
      {agentOptions &&
        onToggleAgentOption &&
        Object.keys(agentOptions).length > 0 &&
        Object.entries(agentOptions)
          .filter(([, opt]) => opt.options && opt.options.length > 0)
          .map(([key, option]) => (
            <LibreChatSelectorModal key={key} panel="thinking">
              <AgentOptionButton
                optionKey={key}
                option={option}
                value={agentOptionValues[key] ?? option.default}
                onChange={(value) => onToggleAgentOption(key, value)}
                isOpen={activePanel === "thinking"}
                onOpenChange={(open) =>
                  onActivePanelChange(open ? "thinking" : null)
                }
              />
            </LibreChatSelectorModal>
          ))}
    </LibreChatSelectorLayer>
  );
}
