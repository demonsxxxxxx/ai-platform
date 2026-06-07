import { memo, useMemo, useState, useCallback, useRef } from "react";
import { RefreshCw, Sparkles, UserRound, ChevronRight } from "lucide-react";
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
      className="welcome-root relative flex h-full flex-col items-center justify-center px-4"
    >
      {/* Greeting section */}
      <div className="welcome-hero relative flex flex-col items-center mb-3 sm:mb-4 md:mb-5 xl:mb-6 2xl:mb-7 w-full max-w-[90vw]">
        {/* App icon (mobile only) */}
        <div className="sm:hidden relative mb-3">
          <img
            src="/icons/icon.svg"
            alt="LambChat"
            className="welcome-icon relative size-10 rounded-full shadow-md ring-1 ring-stone-200/60 dark:ring-stone-700/40"
          />
        </div>

        {/* Greeting */}
        <h1
          className="welcome-greeting max-w-[90vw] text-[1.65rem] sm:text-[2rem] md:text-[2.25rem] lg:text-[2.35rem] xl:text-[2.4rem] 2xl:text-[2.5rem] font-semibold tracking-[-0.02em] leading-[1.2] text-center font-serif"
          style={{ color: "var(--theme-text)" }}
        >
          <img
            src="/icons/icon.svg"
            alt=""
            className="welcome-icon hidden sm:inline-block size-10 2xl:size-12 mr-4 align-text-bottom rounded-full"
          />
          {greeting}
        </h1>
        {/* Subtle subtitle prompt */}
        <p
          className="welcome-subtitle mt-2 sm:mt-3 md:mt-3.5 xl:mt-4 2xl:mt-4 text-sm sm:text-base md:text-[17px] xl:text-lg 2xl:text-lg text-center font-serif"
          style={{ color: "var(--theme-text-secondary)" }}
        >
          {subtitle}
        </p>
      </div>

      {/* ChatInput centered — the focal point */}
      <div className="welcome-input w-full sm:max-w-[44rem] md:max-w-[46rem] lg:max-w-[48rem] xl:max-w-[50rem] 2xl:max-w-[52rem]">
        <ChatInput
          {...chatInputProps}
          onMentionQueryChange={handleMentionQueryChange}
          pendingInput={pendingInput}
          onPendingInputConsumed={() => setPendingInput(null)}
          className="mx-auto w-full px-2"
        />
      </div>

      {(showPersonaCards || showStarterPrompts) && (
        <div
          className={getWelcomeSuggestionsContainerClass(
            showPersonaCards ? "personas" : "prompts",
          )}
        >
          <div className="welcome-suggestions-header flex items-center justify-between mb-2 sm:mb-3 md:mb-3 xl:mb-4 2xl:mb-4 px-2 sm:px-0">
            <div
              className="flex items-center gap-1 text-xs sm:text-sm md:text-sm font-medium font-serif"
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
                  className="flex items-center gap-0.5 px-2 py-1 rounded-lg text-[11px] sm:text-[12px] md:text-[12px] font-medium transition-all duration-300 cursor-pointer font-serif"
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
                  className="welcome-refresh-btn flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] sm:text-[12px] md:text-[12px] font-medium transition-all duration-300 cursor-pointer font-serif"
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
                      {suggestion.icon || "✨"}
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
