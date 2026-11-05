export interface ModelSelectionOption {
  id: string;
  value: string;
}

export interface ModelSelection {
  modelId: string;
  modelValue: string;
}

interface ResolveDefaultModelSelectionArgs {
  availableModels?: ModelSelectionOption[] | null;
  storedDefaultId?: string;
  storedDefaultValue?: string;
  fallbackDefaultValue?: string;
}

interface ReconcileCurrentModelSelectionArgs
  extends ResolveDefaultModelSelectionArgs {
  currentModelId?: string;
  currentModelValue?: string;
}

function emptySelection(fallbackDefaultValue?: string): ModelSelection {
  return {
    modelId: "",
    modelValue: fallbackDefaultValue || "",
  };
}

export function resolveDefaultModelSelection({
  availableModels,
  storedDefaultId,
  storedDefaultValue,
  fallbackDefaultValue,
}: ResolveDefaultModelSelectionArgs): ModelSelection {
  if (!availableModels || availableModels.length === 0) {
    return {
      modelId: storedDefaultId || "",
      modelValue: storedDefaultValue || fallbackDefaultValue || "",
    };
  }

  if (storedDefaultId) {
    const byId = availableModels.find((model) => model.id === storedDefaultId);
    if (byId) {
      return { modelId: byId.id, modelValue: byId.value };
    }
  }

  if (storedDefaultValue) {
    const matches = availableModels.filter((model) => model.value === storedDefaultValue);
    const byValue = matches.length === 1 ? matches[0] : undefined;
    if (byValue) {
      return { modelId: byValue.id, modelValue: byValue.value };
    }
  }

  return emptySelection();
}

export function reconcileCurrentModelSelection({
  availableModels,
  currentModelId,
  currentModelValue,
  storedDefaultId,
  storedDefaultValue,
  fallbackDefaultValue,
}: ReconcileCurrentModelSelectionArgs): ModelSelection {
  if (!availableModels || availableModels.length === 0) {
    return {
      modelId: currentModelId || "",
      modelValue:
        currentModelValue || storedDefaultValue || fallbackDefaultValue || "",
    };
  }

  if (currentModelId) {
    const currentById = availableModels.find(
      (model) => model.id === currentModelId,
    );
    if (currentById) {
      return {
        modelId: currentById.id,
        modelValue: currentById.value,
      };
    }
  }

  if (currentModelValue) {
    const matches = availableModels.filter((model) => model.value === currentModelValue);
    const currentByValue = matches.length === 1 ? matches[0] : undefined;
    if (currentByValue) {
      return {
        modelId: currentByValue.id,
        modelValue: currentByValue.value,
      };
    }
  }

  return resolveDefaultModelSelection({
    availableModels,
    storedDefaultId,
    storedDefaultValue,
    fallbackDefaultValue,
  });
}
