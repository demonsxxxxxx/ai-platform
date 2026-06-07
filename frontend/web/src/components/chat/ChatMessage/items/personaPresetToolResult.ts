import {
  dispatchPersonaPresetsChanged,
  type PersonaPresetEventTarget,
  type PersonaPresetsChangedDetail,
} from "../../../../hooks/personaPresetEvents";

interface PersonaPresetToolMutationResult {
  action?: unknown;
  entity_type?: unknown;
  preset?: {
    id?: unknown;
    name?: unknown;
  } | null;
}

function isRecord(
  value: string | Record<string, unknown> | undefined,
): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function getPersonaPresetMutationDetail(
  result: string | Record<string, unknown> | undefined,
): PersonaPresetsChangedDetail | null {
  if (!isRecord(result)) return null;

  const payload = result as PersonaPresetToolMutationResult;
  if (payload.entity_type !== "persona_preset") return null;
  if (payload.action !== "created" && payload.action !== "updated") return null;

  const detail: PersonaPresetsChangedDetail = {
    action: payload.action,
  };

  if (payload.preset?.id && typeof payload.preset.id === "string") {
    detail.presetId = payload.preset.id;
  }
  if (payload.preset?.name && typeof payload.preset.name === "string") {
    detail.presetName = payload.preset.name;
  }

  return detail;
}

export function dispatchPersonaPresetRefreshFromToolResult(
  result: string | Record<string, unknown> | undefined,
  target?: PersonaPresetEventTarget | null,
): boolean {
  const detail = getPersonaPresetMutationDetail(result);
  if (!detail) return false;
  return dispatchPersonaPresetsChanged(detail, target ?? undefined);
}
