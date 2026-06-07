import type { AvailableModel } from "../../../contexts/SettingsContext";

interface ResolveTokenUsageModelNameArgs {
  modelId?: string;
  model?: string;
  availableModels?: AvailableModel[] | null;
}

export interface TokenUsageModelDetails {
  name: string;
  value: string;
  provider?: string;
}

export function resolveTokenUsageModelName({
  modelId,
  model,
  availableModels,
}: ResolveTokenUsageModelNameArgs): string {
  if (modelId && availableModels?.length) {
    const matchedModel = availableModels.find(
      (availableModel) => availableModel.id === modelId,
    );
    if (matchedModel?.label) {
      return matchedModel.label;
    }
  }

  return model || modelId || "";
}

export function resolveTokenUsageModelDetails({
  modelId,
  model,
  availableModels,
}: ResolveTokenUsageModelNameArgs): TokenUsageModelDetails | null {
  if (modelId && availableModels?.length) {
    const matchedModel = availableModels.find(
      (availableModel) => availableModel.id === modelId,
    );
    if (matchedModel) {
      return {
        name: matchedModel.label || matchedModel.value || matchedModel.id,
        value: matchedModel.value,
        provider: matchedModel.provider,
      };
    }
  }

  const fallback = model || modelId || "";
  if (!fallback) return null;

  return {
    name: fallback,
    value: model || fallback,
  };
}
