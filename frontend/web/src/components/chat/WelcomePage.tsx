import { memo, useMemo, useState, useCallback, useRef } from "react";
import {
  ArrowRight,
  Boxes,
  FileText,
  type LucideIcon,
  MessageSquareText,
  RefreshCw,
  Sparkles,
  UserRound,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";
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

interface ComposerSummaryItem {
  id: string;
  label: string;
  value: string;
  state?: "default" | "enabled" | "unavailable";
}

interface QuickActionItem {
  id: string;
  label: string;
  description: string;
  command: string;
  state: "enabled" | "unavailable";
  icon: LucideIcon;
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
  const selectionSummary = useMemo<ComposerSummaryItem[]>(
    () => [
      {
        id: "skills",
        label: t("featureMenu.skills", "Skills"),
        value:
          totalSkillsCount > 0
            ? `${enabledSkillsCount}/${totalSkillsCount}`
            : t("workbench.unavailableShort", "Unavailable"),
        state: enabledSkillsCount > 0 ? "enabled" : "unavailable",
      },
      {
        id: "mcp",
        label: t("featureMenu.mcpTools", "MCP tools"),
        value:
          totalToolsCount > 0
            ? `${enabledToolsCount}/${totalToolsCount}`
            : t("workbench.unavailableShort", "Unavailable"),
        state: enabledToolsCount > 0 ? "enabled" : "unavailable",
      },
      {
        id: "agent",
        label: t("featureMenu.agents", "Agents"),
        value: currentAgentName || t("workbench.defaultAgent", "Default"),
        state: currentAgentName ? "enabled" : "default",
      },
      {
        id: "model",
        label: t("featureMenu.model", "Model"),
        value: chatInputProps.currentModelId || t("workbench.none", "None"),
        state: chatInputProps.currentModelId ? "enabled" : "default",
      },
      {
        id: "files",
        label: t("chat.fileReferences", "File references"),
        value:
          attachedFilesCount > 0
            ? String(attachedFilesCount)
            : t("workbench.none", "None"),
        state: attachedFilesCount > 0 ? "enabled" : "default",
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
  const quickActions = useMemo<QuickActionItem[]>(
    () => [
      {
        id: "chat",
        label: t("workbench.quickActions.chat", "Write a prompt"),
        description: t(
          "workbench.quickActions.chatDescription",
          "Start from the plain composer.",
        ),
        command: "",
        state: "enabled",
        icon: MessageSquareText,
      },
      {
        id: "skills",
        label: t("featureMenu.skills", "Skills"),
        description:
          totalSkillsCount > 0
            ? t("workbench.quickActions.skillsDescription", {
                count: enabledSkillsCount,
                total: totalSkillsCount,
                defaultValue: "{{count}}/{{total}} enabled",
              })
            : t(
                "workbench.quickActions.skillsUnavailable",
                "No readable skills for this workspace.",
              ),
        command: "$ ",
        state: totalSkillsCount > 0 ? "enabled" : "unavailable",
        icon: Boxes,
      },
      {
        id: "mcp",
        label: t("featureMenu.mcpTools", "MCP tools"),
        description:
          totalToolsCount > 0
            ? t("workbench.quickActions.mcpDescription", {
                count: enabledToolsCount,
                total: totalToolsCount,
                defaultValue: "{{count}}/{{total}} enabled",
              })
            : t(
                "workbench.quickActions.mcpUnavailable",
                "No approved MCP tools for this workspace.",
              ),
        command: "/mcp ",
        state: totalToolsCount > 0 ? "enabled" : "unavailable",
        icon: Wrench,
      },
      {
        id: "files",
        label: t("chat.fileReferences", "File references"),
        description:
          attachedFilesCount > 0
            ? t("workbench.quickActions.filesDescription", {
                count: attachedFilesCount,
                defaultValue: "{{count}} attached",
              })
            : t(
                "workbench.quickActions.filesUnavailable",
                "Attach or reference files after upload.",
              ),
        command: "/file ",
        state: "enabled",
        icon: FileText,
      },
    ],
    [
      attachedFilesCount,
      enabledSkillsCount,
      enabledToolsCount,
      t,
      totalSkillsCount,
      totalToolsCount,
    ],
  );

  const handleQuickActionClick = (action: QuickActionItem) => {
    if (!canSendMessage || action.state === "unavailable") {
      setContactAdminOpen(true);
      return;
    }
    if (action.id === "chat") {
      requestAnimationFrame(() => {
        rootRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
      });
      return;
    }
    if (action.command) {
      setPendingInput(action.command);
    }
  };

  return (
    <div
      ref={rootRef}
      data-workbench-empty-state="chat"
      className="welcome-root welcome-chat-start relative flex h-full min-h-0 flex-col overflow-y-auto px-4 py-4 sm:px-5"
    >
      <section
        data-chat-start-surface
        className="chat-start-surface mx-auto flex w-full max-w-4xl flex-col gap-3 py-4"
      >
        <div
          data-chat-start-header
          className="flex flex-col gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-3 shadow-[0_1px_2px_rgba(18,38,63,0.04)] sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase text-[var(--theme-text-tertiary)]">
              {t("workbench.newConversation", "New conversation")}
            </p>
            <h1 className="mt-1 truncate text-xl font-semibold leading-7 text-[var(--theme-text)] sm:text-2xl">
              {greeting}
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-5 text-[var(--theme-text-secondary)]">
              {subtitle}
            </p>
          </div>
          <div className="grid shrink-0 grid-cols-2 gap-2 text-xs sm:w-52">
            <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1.5 text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
              <span className="font-semibold text-[var(--theme-text)]">
                {enabledSkillsCount}
              </span>{" "}
              {t("featureMenu.skills", "Skills")}
            </span>
            <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1.5 text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
              <span className="font-semibold text-[var(--theme-text)]">
                {enabledToolsCount}
              </span>{" "}
              {t("featureMenu.mcpTools", "MCP")}
            </span>
          </div>
        </div>

        <div className="welcome-input">
          <ChatInput
            {...chatInputProps}
            onMentionQueryChange={handleMentionQueryChange}
            pendingInput={pendingInput}
            onPendingInputConsumed={() => setPendingInput(null)}
            className="mx-auto w-full max-w-4xl px-0"
          />
        </div>

        <div
          data-composer-command-dock
          className="mx-auto flex w-full max-w-4xl flex-wrap items-center gap-1.5 text-xs text-[var(--theme-text-secondary)]"
        >
          <span className="font-medium text-[var(--theme-text)]">
            {t("workbench.commandDock", "Composer")}
          </span>
          {["/", "$", "/mcp", "/model", "/file", "/context"].map((command) => (
            <span
              key={command}
              className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-1.5 py-0.5 font-semibold text-[var(--theme-text)]"
            >
              {command}
            </span>
          ))}
          <span className="min-w-0">
            {t(
              "workbench.commandDockHint",
              "Type commands directly in the input; governed entries become chips.",
            )}
          </span>
        </div>

        <div
          data-composer-selection-summary
          className="mx-auto flex w-full max-w-4xl flex-wrap gap-1.5"
        >
          {selectionSummary.map((item) => (
            <span
              key={item.id}
              className={`inline-flex max-w-[12rem] items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${
                item.state === "enabled"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
                : item.state === "unavailable"
                    ? "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"
                    : "border-[var(--theme-border)] bg-[var(--theme-bg-card)] text-stone-500 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-400"
              }`}
              title={`${item.label}: ${item.value}`}
            >
              <span className="truncate font-medium">{item.label}</span>
              <span className="shrink-0 opacity-80">{item.value}</span>
            </span>
          ))}
        </div>

        <div
          data-chat-quick-actions
          className="mx-auto grid w-full max-w-4xl gap-2 sm:grid-cols-2 xl:grid-cols-4"
        >
          {quickActions.map((action) => {
            const Icon = action.icon;
            const unavailable = action.state === "unavailable";
            return (
              <button
                key={action.id}
                type="button"
                onClick={() => handleQuickActionClick(action)}
                className={`group flex min-h-20 items-start gap-3 rounded-lg border bg-[var(--theme-bg-card)] p-3 text-left shadow-[0_1px_2px_rgba(18,38,63,0.04)] transition-colors duration-200 ${
                  unavailable
                    ? "border-dashed border-[var(--theme-border)] opacity-70 hover:opacity-100"
                    : "border-[var(--theme-border)] hover:border-[var(--theme-border-strong)] hover:bg-[var(--theme-bg-sidebar)]"
                }`}
              >
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                  <Icon size={16} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 text-sm font-semibold text-[var(--theme-text)]">
                    {action.label}
                    <ArrowRight
                      size={14}
                      className="opacity-40 transition-transform group-hover:translate-x-0.5 group-hover:opacity-70"
                    />
                  </span>
                  <span className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                    {action.description}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

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
                  className="welcome-persona-card welcome-persona-skeleton relative min-w-[15.75rem] snap-start rounded-lg border p-2.5"
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
                    <span className="welcome-persona-header relative flex items-start gap-3">
                      <PersonaAvatarWithLoading
                        preset={preset}
                        className="welcome-persona-avatar relative flex items-center justify-center size-11 rounded-lg shrink-0 overflow-hidden transition-transform duration-200 group-hover:scale-[1.02]"
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
                            className="truncate text-[14px] font-semibold leading-[1.35] transition-colors duration-200 group-hover:text-[var(--theme-text)]"
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
                    <span
                      className="relative flex items-center justify-center size-6 sm:size-7 xl:size-8 2xl:size-8 rounded-lg text-[13px] sm:text-[15px] xl:text-lg 2xl:text-lg shrink-0 transition-transform duration-200 group-hover:scale-[1.02]"
                      style={{
                        backgroundColor: "var(--theme-primary-light)",
                        color: "var(--theme-primary)",
                      }}
                    >
                      <MessageSquareText size={14} />
                    </span>
                    <span
                      className="relative text-[12.5px] sm:text-[13.5px] leading-[1.4] sm:leading-[1.45] truncate transition-colors duration-200 group-hover:text-[var(--theme-text)]"
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
