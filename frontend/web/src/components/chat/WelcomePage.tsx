import { memo, useMemo, useState, useCallback, useRef } from "react";
import {
  ChevronRight,
  MessageSquareText,
  RefreshCw,
  Sparkles,
  UserRound,
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
  return (
    <div
      ref={rootRef}
      data-workbench-empty-state="chat"
      className="welcome-root welcome-chat-start relative flex h-full min-h-0 flex-col overflow-y-auto px-4 py-6 sm:px-6"
    >
      <section
        data-chat-start-surface
        className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center py-5"
      >
        <div className="mb-5 text-center">
          <p className="text-xs font-semibold uppercase text-stone-400 dark:text-stone-500">
            {t("workbench.newConversation", "New conversation")}
          </p>
          <h1 className="mt-2 text-2xl font-semibold leading-8 text-stone-950 dark:text-stone-50 sm:text-3xl">
            {greeting}
          </h1>
          <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-stone-500 dark:text-stone-400">
            {subtitle}
          </p>
        </div>

        <div className="welcome-input">
          <ChatInput
            {...chatInputProps}
            onMentionQueryChange={handleMentionQueryChange}
            pendingInput={pendingInput}
            onPendingInputConsumed={() => setPendingInput(null)}
            className="mx-auto w-full px-0"
          />
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
