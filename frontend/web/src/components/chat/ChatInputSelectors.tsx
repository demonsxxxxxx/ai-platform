import { useNavigate } from "react-router-dom";
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
  SkillResponse,
  SkillSource,
  AgentOption,
  PersonaPreset,
  PersonaPresetSnapshot,
} from "../../types";

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
  skills?: SkillResponse[];
  onToggleSkill?: (name: string) => Promise<boolean>;
  onToggleSkillCategory?: (
    category: SkillSource,
    enabled: boolean,
  ) => Promise<boolean>;
  onToggleAllSkills?: (enabled: boolean) => Promise<boolean>;
  pendingSkillNames?: string[];
  skillsMutating?: boolean;
  enabledSkillsCount?: number;
  totalSkillsCount?: number;
  enableSkills?: boolean;
  personaSkillsControlled?: boolean;
  selectedPersonaName?: string | null;
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
  onToggleSkill,
  onToggleSkillCategory,
  onToggleAllSkills,
  pendingSkillNames = [],
  skillsMutating = false,
  enabledSkillsCount = 0,
  totalSkillsCount = 0,
  enableSkills = true,
  personaSkillsControlled = false,
  selectedPersonaName,
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
  const navigate = useNavigate();
  const { t } = useTranslation();

  return (
    <>
      {onToggleTool && onToggleCategory && onToggleAll && (
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
      )}
      {enableSkills &&
        onToggleSkill &&
        onToggleSkillCategory &&
        onToggleAllSkills && (
          <SkillSelector
            skills={skills}
            onToggleSkill={onToggleSkill}
            onToggleCategory={onToggleSkillCategory}
            onToggleAll={onToggleAllSkills}
            pendingSkillNames={pendingSkillNames}
            isMutating={skillsMutating}
            enabledCount={enabledSkillsCount}
            totalCount={totalSkillsCount}
            controlledByPersonaName={
              personaSkillsControlled ? selectedPersonaName : null
            }
            isOpen={activePanel === "skills"}
            onOpenChange={(open) => onActivePanelChange(open ? "skills" : null)}
            searchSeed={
              commandSearchSeed?.panel === "skills"
                ? commandSearchSeed.query
                : undefined
            }
          />
        )}
      {onUsePersonaPreset && onCopyPersonaPreset && onClearPersonaPreset && (
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
          onManagePresets={() => navigate("/persona")}
          onClearPreset={() => {
            onClearPersonaPreset();
            onActivePanelChange(null);
          }}
        />
      )}
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
      {onSelectModel && availableModels.length > 0 && (
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
      )}
      <ComposerUnavailablePanel
        isOpen={activePanel === "context"}
        onOpenChange={(open) => onActivePanelChange(open ? "context" : null)}
        surface="context-selector"
        title={t("composerCommand.contextSelector.title")}
        description={t("composerCommand.contextSelector.description")}
      />
      {agentOptions &&
        onToggleAgentOption &&
        Object.keys(agentOptions).length > 0 &&
        Object.entries(agentOptions)
          .filter(([, opt]) => opt.options && opt.options.length > 0)
          .map(([key, option]) => (
            <AgentOptionButton
              key={key}
              optionKey={key}
              option={option}
              value={agentOptionValues[key] ?? option.default}
              onChange={(value) => onToggleAgentOption(key, value)}
              isOpen={activePanel === "thinking"}
              onOpenChange={(open) =>
                onActivePanelChange(open ? "thinking" : null)
              }
            />
          ))}
    </>
  );
}
