import { memo, useMemo, useState, useCallback, useRef } from "react";
import {
  Bot,
  ChevronRight,
  FileText,
  MessageSquareText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserRound,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ChatInput } from "./ChatInput";
import type { ChatInputProps } from "./ChatInput";
import { ContactAdminDialog } from "../common/ContactAdminDialog";
import {
  getSelectedPersonaStarterPrompts,
  getWelcomePersonaCards,
  getWelcomePersonaCardClass,
  getWelcomePersonaSkeletonCount,
  getWelcomeSuggestionsContainerClass,
  getWelcomeSuggestionButtonClass,
} from "./welcomeLayout";
import { PersonaAvatarWithLoading } from "../persona/PersonaAvatarWithLoading";
import { useSettingsContext } from "../../contexts/SettingsContext";
import type { PersonaPreset, PersonaPresetSnapshot } from "../../types";
import { APP_NAME } from "../../constants";
import { workbenchSurface } from "../workbench/workbenchSurface";

interface WelcomePageProps {
  greeting: string;
  subtitle: string;
  refreshLabel: string;
  personasLabel?: string;
  starterPromptsLabel?: string;
  changePersonaLabel?: string;
  personaPresets: PersonaPreset[];
  selectedPersonaPresetId?: string | null;
  selectedPersonaSnapshot?: PersonaPresetSnapshot | null;
  personaPresetsLoading?: boolean;
  personaPresetsMutating?: boolean;
  canSendMessage: boolean;
  chatInputProps: ChatInputProps;
  onUsePersonaPreset?: (
    preset: PersonaPreset,
  ) => Promise<PersonaPresetSnapshot | null>;
  onClearPersonaPreset?: () => void;
}

interface WorkbenchQueueItem {
  id: string;
  icon: typeof Sparkles;
  label: string;
  value: string;
  tone?: "default" | "enabled" | "blocked";
}

function WorkbenchQueueList({ items }: { items: WorkbenchQueueItem[] }) {
  return (
    <div className="space-y-2">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div
            key={item.id}
            className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900"
          >
            <span className="flex min-w-0 items-center gap-2">
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                  item.tone === "enabled"
                    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
                    : item.tone === "blocked"
                      ? "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"
                      : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300"
                }`}
              >
                <Icon size={14} />
              </span>
              <span className="truncate font-medium text-stone-700 dark:text-stone-200">
                {item.label}
              </span>
            </span>
            <span className="shrink-0 rounded-md bg-stone-100 px-2 py-1 font-medium text-stone-600 dark:bg-stone-800 dark:text-stone-300">
              {item.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export const WelcomePage = memo(function WelcomePage({
  greeting,
  subtitle,
  refreshLabel,
  personasLabel,
  starterPromptsLabel,
  changePersonaLabel,
  personaPresets,
  selectedPersonaPresetId,
  selectedPersonaSnapshot,
  personaPresetsLoading = false,
  personaPresetsMutating = false,
  canSendMessage,
  chatInputProps,
  onUsePersonaPreset,
  onClearPersonaPreset,
}: WelcomePageProps) {
  const { i18n, t } = useTranslation();
  const navigate = useNavigate();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [animKey, setAnimKey] = useState(0);
  const [contactAdminOpen, setContactAdminOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [pendingInput, setPendingInput] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const promptSources = useMemo(() => {
    if (
      selectedPersonaSnapshot &&
      !personaPresets.some(
        (persona) => persona.id === selectedPersonaSnapshot.preset_id,
      )
    ) {
      return [
        ...personaPresets,
        {
          id: selectedPersonaSnapshot.preset_id,
          name: selectedPersonaSnapshot.name,
          starter_prompts: selectedPersonaSnapshot.starter_prompts ?? [],
        },
      ];
    }
    return personaPresets;
  }, [personaPresets, selectedPersonaSnapshot]);

  const roleCards = useMemo(
    () => getWelcomePersonaCards(personaPresets, selectedPersonaPresetId),
    [personaPresets, selectedPersonaPresetId],
  );

  const filteredCards = useMemo(() => {
    if (!mentionQuery) return roleCards;
    const q = mentionQuery.toLowerCase();
    return roleCards.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.description?.toLowerCase().includes(q) ||
        p.tags?.some((tag) => tag.toLowerCase().includes(q)),
    );
  }, [roleCards, mentionQuery]);

  const handleMentionQueryChange = useCallback(
    (query: string | null) => setMentionQuery(query),
    [],
  );

  const { settings } = useSettingsContext();

  const defaultSuggestions = useMemo(() => {
    const rawValue = settings?.settings?.frontend?.find(
      (s) => s.key === "WELCOME_SUGGESTIONS",
    )?.value;
    const currentLang = i18n.language?.split("-")[0] || "en";
    if (Array.isArray(rawValue)) return rawValue;
    if (rawValue && typeof rawValue === "object") {
      const langMap = rawValue as Record<
        string,
        Array<{ icon: string; text: string }>
      >;
      return langMap[currentLang] || langMap["en"];
    }
    return [];
  }, [settings, i18n.language]);

  const starterPrompts = useMemo(
    () =>
      getSelectedPersonaStarterPrompts(
        promptSources,
        selectedPersonaPresetId,
        i18n.language,
        selectedPersonaPresetId ? defaultSuggestions : [],
      ),
    [promptSources, selectedPersonaPresetId, i18n.language, defaultSuggestions],
  );

  const handleSuggestionClick = (text: string) => {
    if (!canSendMessage) {
      setContactAdminOpen(true);
      return;
    }
    setPendingInput(text);
  };

  const handleRefresh = useCallback(() => {
    if (!onClearPersonaPreset) return;
    setIsRefreshing(true);
    onClearPersonaPreset();
    setAnimKey((k) => k + 1);
    setTimeout(() => setIsRefreshing(false), 400);
  }, [onClearPersonaPreset]);

  const handlePersonaClick = useCallback(
    async (preset: PersonaPreset) => {
      if (personaPresetsMutating) return;
      await onUsePersonaPreset?.(preset);
    },
    [onUsePersonaPreset, personaPresetsMutating],
  );

  const showPersonaCards =
    !selectedPersonaPresetId &&
    (mentionQuery !== null || roleCards.length > 0 || personaPresetsLoading);
  const showStarterPrompts =
    !!selectedPersonaPresetId && starterPrompts.length > 0;
  const displayCards = mentionQuery ? filteredCards : roleCards;
  const personaSkeletonCount = getWelcomePersonaSkeletonCount(
    personaPresetsLoading,
    displayCards.length,
  );
  const enabledSkillsCount = chatInputProps.enabledSkillsCount ?? 0;
  const totalSkillsCount = chatInputProps.totalSkillsCount ?? 0;
  const enabledToolsCount = chatInputProps.enabledToolsCount ?? 0;
  const totalToolsCount = chatInputProps.totalToolsCount ?? 0;
  const attachedFilesCount = chatInputProps.attachments?.length ?? 0;
  const currentAgentName =
    chatInputProps.agents?.find(
      (agent) => agent.id === chatInputProps.currentAgent,
    )?.name ?? chatInputProps.currentAgent;
  const queueItems = useMemo<WorkbenchQueueItem[]>(
    () => [
      {
        id: "skills",
        icon: Sparkles,
        label: t("featureMenu.skills", "Skills"),
        value:
          totalSkillsCount > 0
            ? `${enabledSkillsCount}/${totalSkillsCount}`
            : t("workbench.unavailableShort", "Unavailable"),
        tone: enabledSkillsCount > 0 ? "enabled" : "blocked",
      },
      {
        id: "mcp",
        icon: Wrench,
        label: t("featureMenu.mcpTools", "MCP tools"),
        value:
          totalToolsCount > 0
            ? `${enabledToolsCount}/${totalToolsCount}`
            : t("workbench.unavailableShort", "Unavailable"),
        tone: enabledToolsCount > 0 ? "enabled" : "blocked",
      },
      {
        id: "agent",
        icon: Bot,
        label: t("featureMenu.agents", "Agents"),
        value: currentAgentName || t("workbench.defaultAgent", "Default"),
        tone: currentAgentName ? "enabled" : "default",
      },
      {
        id: "model",
        icon: Sparkles,
        label: t("featureMenu.model", "Model"),
        value: chatInputProps.currentModelId || t("workbench.none", "None"),
        tone: chatInputProps.currentModelId ? "enabled" : "default",
      },
      {
        id: "files",
        icon: FileText,
        label: t("chat.fileReferences", "File references"),
        value:
          attachedFilesCount > 0
            ? String(attachedFilesCount)
            : t("workbench.none", "None"),
      },
    ],
    [
      attachedFilesCount,
      currentAgentName,
      enabledSkillsCount,
      enabledToolsCount,
      chatInputProps.currentModelId,
      t,
      totalSkillsCount,
      totalToolsCount,
    ],
  );

  return (
    <div
      ref={rootRef}
      data-workbench-empty-state="chat"
      className="welcome-root welcome-workbench-cockpit relative flex h-full min-h-0 flex-col overflow-y-auto px-3 py-3 sm:px-4"
    >
      <div className={workbenchSurface.cockpit}>
        <aside className="space-y-3">
          <section className={`${workbenchSurface.compactPanel} p-4`}>
            <div className="flex items-center gap-3">
              <img
                src="/icons/icon.svg"
                alt={APP_NAME}
                className="h-9 w-9 rounded-lg ring-1 ring-stone-200 dark:ring-stone-800"
              />
              <div className="min-w-0">
                <p className={workbenchSurface.label}>
                  {t("workbench.cockpit", "Workbench")}
                </p>
                <h1 className="truncate text-base font-semibold text-stone-900 dark:text-stone-50">
                  {greeting}
                </h1>
              </div>
            </div>
            <p className="mt-3 text-sm leading-6 text-stone-600 dark:text-stone-300">
              {subtitle}
            </p>
          </section>

          <section className={`${workbenchSurface.compactPanel} p-3`}>
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className={workbenchSurface.label}>
                {t("workbench.selectionState", "Selection state")}
              </p>
              <ShieldCheck size={15} className="text-stone-400" />
            </div>
            <WorkbenchQueueList items={queueItems} />
          </section>
        </aside>

        <section className={`${workbenchSurface.compactPanel} min-w-0 p-3 sm:p-4`}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <p className={workbenchSurface.label}>
                {t("workbench.newConversation", "New conversation")}
              </p>
              <h2 className="mt-1 text-xl font-semibold leading-7 text-stone-900 dark:text-stone-50">
                {t(
                  "workbench.commandFirstTitle",
                  "Start with a prompt, Skill, MCP tool, file, or context.",
                )}
              </h2>
            </div>
          </div>

          <div
            data-composer-command-dock
            className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-xs text-stone-600 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-300"
          >
            <span className="font-medium text-stone-800 dark:text-stone-100">
              {t("workbench.commandDock", "Composer")}
            </span>
            {["/", "$", "/mcp", "/model", "/file", "/context"].map((command) => (
              <span
                key={command}
                className="rounded-md bg-white px-2 py-1 font-semibold shadow-sm dark:bg-stone-900"
              >
                {command}
              </span>
            ))}
            <span className="min-w-0 text-stone-500 dark:text-stone-400">
              {t(
                "workbench.commandDockHint",
                "Type commands directly in the input; governed entries become chips.",
              )}
            </span>
          </div>

          <div className="welcome-input mt-3">
            <ChatInput
              {...chatInputProps}
              onMentionQueryChange={handleMentionQueryChange}
              pendingInput={pendingInput}
              onPendingInputConsumed={() => setPendingInput(null)}
              className="mx-auto w-full px-0"
            />
          </div>
        </section>
      </div>

      {(showPersonaCards || showStarterPrompts) && (
        <div
          className={getWelcomeSuggestionsContainerClass(
            showPersonaCards ? "personas" : "prompts",
          )}
        >
          <div className="welcome-suggestions-header flex items-center justify-between mb-2 sm:mb-3 md:mb-3 xl:mb-4 2xl:mb-4 px-2 sm:px-0">
            <div
              className="flex items-center gap-1 text-xs sm:text-sm md:text-sm font-medium"
              style={{ color: "var(--theme-text-secondary)" }}
            >
              <Sparkles
                size={11}
                className="opacity-60 sm:w-3.5 sm:h-3.5 xl:w-4 xl:h-4 2xl:w-4 2xl:h-4"
              />
              <span>
                {selectedPersonaPresetId
                  ? starterPromptsLabel ||
                    t("personaPresets.starterPrompts", "开始对话")
                  : personasLabel || t("personaPresets.title", "角色")}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {showPersonaCards && (
                <button
                  onClick={() => navigate("/persona")}
                  className="flex items-center gap-0.5 px-2 py-1 rounded-lg text-[11px] sm:text-[12px] md:text-[12px] font-medium transition-all duration-300 cursor-pointer"
                  style={{
                    color: "var(--theme-text-secondary)",
                    backgroundColor: "transparent",
                  }}
                >
                  <span>{t("common.manage", "管理")}</span>
                  <ChevronRight size={12} />
                </button>
              )}
              {selectedPersonaPresetId && onClearPersonaPreset && (
                <button
                  onClick={handleRefresh}
                  className="welcome-refresh-btn flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] sm:text-[12px] md:text-[12px] font-medium transition-all duration-300 cursor-pointer"
                  style={{
                    color: "var(--theme-text-secondary)",
                    backgroundColor: "transparent",
                  }}
                >
                  <RefreshCw
                    size={12}
                    className={
                      isRefreshing
                        ? "animate-spin"
                        : "xl:w-3.5 xl:h-3.5 2xl:w-3.5 2xl:h-3.5"
                    }
                  />
                  <span>
                    {changePersonaLabel ||
                      refreshLabel ||
                      t("personaPresets.change", "更换角色")}
                  </span>
                </button>
              )}
            </div>
          </div>
          <div
            key={animKey}
            className={
              showPersonaCards
                ? "welcome-persona-gallery px-2 pb-1 sm:px-0 sm:pb-0"
                : "welcome-suggestions-grid-wrapper"
            }
          >
            {showPersonaCards &&
              Array.from({ length: personaSkeletonCount }).map((_, i) => (
                <div
                  key={`persona-skeleton-${i}`}
                  className="welcome-persona-card welcome-persona-skeleton relative min-w-[15.75rem] snap-start rounded-2xl border p-2.5"
                  style={{
                    backgroundColor: "var(--theme-bg-card)",
                    borderColor: "var(--theme-border)",
                  }}
                  aria-hidden="true"
                >
                  <span className="welcome-skeleton-avatar" />
                  <span className="welcome-skeleton-line welcome-skeleton-title" />
                  <span className="welcome-skeleton-line welcome-skeleton-tag" />
                  <span className="welcome-skeleton-line" />
                  <span className="welcome-skeleton-line welcome-skeleton-line-short" />
                </div>
              ))}
            {showPersonaCards &&
              displayCards.map((preset, i) => {
                const primaryTag = preset.tags[0] || "";
                return (
                  <button
                    key={preset.id}
                    onClick={() => handlePersonaClick(preset)}
                    disabled={personaPresetsMutating}
                    className={getWelcomePersonaCardClass(i)}
                    style={{
                      backgroundColor: "var(--theme-bg-card)",
                      borderColor: "var(--theme-border)",
                      animationDelay: `${i * 60}ms`,
                    }}
                  >
                    <span className="welcome-card-shimmer" aria-hidden="true" />
                    <span className="welcome-persona-header relative flex items-start gap-3">
                      <PersonaAvatarWithLoading
                        preset={preset}
                        className="welcome-persona-avatar relative flex items-center justify-center size-11 rounded-xl shrink-0 overflow-hidden transition-transform duration-300 group-hover:scale-105"
                        imgClassName="h-full w-full object-cover"
                        iconSize={22}
                        fallbackIcon={<UserRound size={22} />}
                        style={{
                          backgroundColor: "var(--theme-primary-light)",
                          color: "var(--theme-primary)",
                        }}
                      />
                      <span className="welcome-persona-info min-w-0 flex-1 pt-0.5">
                        <span className="welcome-persona-name-row relative flex items-center gap-1.5">
                          <span
                            className="truncate text-[14px] font-semibold leading-[1.35] transition-colors duration-300 group-hover:text-[var(--theme-text)]"
                            style={{ color: "var(--theme-text)" }}
                          >
                            {preset.name}
                          </span>
                          {primaryTag && (
                            <span
                              className="welcome-persona-tag shrink-0 inline-flex rounded-full px-1.5 py-[1px] text-[10px] leading-none font-medium"
                              style={{
                                backgroundColor: "var(--theme-primary-light)",
                                color: "var(--theme-primary)",
                              }}
                            >
                              {primaryTag}
                            </span>
                          )}
                        </span>
                        {preset.description && (
                          <span
                            className="welcome-persona-description block mt-1 text-[12px] leading-[1.5]"
                            style={{
                              color:
                                "var(--theme-text-tertiary, var(--theme-text-secondary))",
                            }}
                          >
                            {preset.description}
                          </span>
                        )}
                      </span>
                    </span>
                  </button>
                );
              })}
            <div
              className={
                showStarterPrompts
                  ? "welcome-suggestions-grid grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-2.5 md:gap-2.5 xl:gap-3 2xl:gap-3 px-2 sm:px-0"
                  : undefined
              }
            >
              {showStarterPrompts &&
                starterPrompts.map((suggestion, i) => (
                  <button
                    key={suggestion.text}
                    onClick={() => handleSuggestionClick(suggestion.text)}
                    className={getWelcomeSuggestionButtonClass(i)}
                    style={{
                      backgroundColor: "var(--theme-bg-card)",
                      borderColor: "var(--theme-border)",
                      animationDelay: `${i * 60}ms`,
                    }}
                  >
                    {/* Hover shimmer layer */}
                    <span className="welcome-card-shimmer" aria-hidden="true" />
                    <span
                      className="relative flex items-center justify-center size-6 sm:size-7 xl:size-8 2xl:size-8 rounded-lg text-[13px] sm:text-[15px] xl:text-lg 2xl:text-lg shrink-0 transition-transform duration-300 group-hover:scale-110"
                      style={{
                        backgroundColor: "var(--theme-primary-light)",
                        color: "var(--theme-primary)",
                      }}
                    >
                      <MessageSquareText size={14} />
                    </span>
                    <span
                      className="relative text-[12.5px] sm:text-[13.5px] leading-[1.4] sm:leading-[1.45] truncate transition-colors duration-300 group-hover:text-[var(--theme-text)]"
                      style={{ color: "var(--theme-text-secondary)" }}
                    >
                      {suggestion.text}
                    </span>
                  </button>
                ))}
            </div>
          </div>
        </div>
      )}

      <ContactAdminDialog
        isOpen={contactAdminOpen}
        onClose={() => setContactAdminOpen(false)}
        reason="noPermission"
      />
    </div>
  );
});
