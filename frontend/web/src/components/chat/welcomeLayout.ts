import type { LocalizedText, PersonaStarterPrompt } from "../../types";

interface WelcomePersonaLike {
  id: string;
  name: string;
  is_favorite?: boolean;
  is_pinned?: boolean;
  last_used_at?: string | null;
  usage_count?: number;
  updated_at?: string;
  created_at?: string;
  starter_prompts?: PersonaStarterPrompt[];
}

export interface WelcomeStarterPrompt {
  icon: string | null;
  text: string;
}

export function getWelcomePersonaCardClass(_index: number): string {
  return [
    "welcome-card",
    "welcome-persona-card",
    "group",
    "relative",
    "flex",
    "min-w-[15.75rem]",
    "snap-start",
    "flex-col",
    "gap-1.5",
    "rounded-2xl",
    "border",
    "p-3",
    "text-left",
    "cursor-pointer",
    "transition-all",
    "duration-300",
    "overflow-hidden",
    "sm:min-w-0",
  ]
    .filter(Boolean)
    .join(" ");
}

export function getWelcomePersonaSkeletonCount(
  loading: boolean,
  visibleCardCount: number,
  fallbackCount = 12,
): number {
  if (!loading || visibleCardCount > 0) return 0;
  return fallbackCount;
}

export function getWelcomeSuggestionButtonClass(_index: number): string {
  return "welcome-card welcome-suggestion-pill group relative flex items-center gap-2 sm:gap-3 md:gap-3 xl:gap-3.5 2xl:gap-3.5 rounded-xl border px-3 py-2 sm:px-4 sm:py-3 text-left cursor-pointer transition-all duration-300 overflow-hidden";
}

export function getWelcomeSuggestionsContainerClass(
  variant: "personas" | "prompts",
): string {
  const base =
    "welcome-suggestions relative px-0 sm:px-4 sm:mt-2 md:mt-3 xl:mt-4 2xl:mt-4";
  if (variant === "personas") {
    return `${base} w-full sm:max-w-[42rem] md:max-w-[48rem] lg:max-w-[52rem] xl:max-w-[56rem] 2xl:max-w-[58rem]`;
  }
  return `${base} w-[85%] sm:max-w-[38rem] md:max-w-[40rem] lg:max-w-[42rem] xl:max-w-[44rem] 2xl:max-w-[46rem]`;
}

export function resolveLocalizedText(
  value: LocalizedText,
  language = "en",
): string {
  if (typeof value === "string") return value;

  const normalizedLanguage = language.toLowerCase();
  const baseLanguage = normalizedLanguage.split("-")[0];
  return (
    value[language] ||
    value[normalizedLanguage] ||
    value[baseLanguage] ||
    value.en ||
    value.zh ||
    Object.values(value)[0] ||
    ""
  );
}

export function getWelcomePersonaCards<T extends WelcomePersonaLike>(
  personas: T[],
  selectedPersonaId: string | null | undefined,
  limit?: number,
): T[] {
  if (selectedPersonaId) return [];
  const cards = [...personas].sort(compareWelcomePersonas);
  return typeof limit === "number" ? cards.slice(0, limit) : cards;
}

function timeValue(value: string | null | undefined): number {
  if (!value) return 0;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : 0;
}

export function compareWelcomePersonas<T extends WelcomePersonaLike>(
  a: T,
  b: T,
): number {
  return (
    Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned)) ||
    Number(Boolean(b.is_favorite)) - Number(Boolean(a.is_favorite)) ||
    timeValue(b.updated_at) - timeValue(a.updated_at) ||
    timeValue(b.created_at) - timeValue(a.created_at) ||
    timeValue(b.last_used_at) - timeValue(a.last_used_at) ||
    Number(b.usage_count || 0) - Number(a.usage_count || 0)
  );
}

export function getSelectedPersonaStarterPrompts(
  personas: WelcomePersonaLike[],
  selectedPersonaId: string | null | undefined,
  language = "en",
  fallbackPrompts: WelcomeStarterPrompt[] = [],
): WelcomeStarterPrompt[] {
  const selected = personas.find((persona) => persona.id === selectedPersonaId);
  if (!selected) return fallbackPrompts;

  const prompts = (selected.starter_prompts ?? [])
    .map((prompt) => ({
      icon: prompt.icon?.trim() || null,
      text: resolveLocalizedText(prompt.text, language).trim(),
    }))
    .filter((prompt) => prompt.text.length > 0);

  return prompts.length > 0 ? prompts : fallbackPrompts;
}
