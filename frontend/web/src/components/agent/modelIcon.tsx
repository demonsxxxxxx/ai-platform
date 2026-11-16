import React, { useMemo } from "react";
import { getModelIconUrl } from "./modelIcon";

export const ModelIconImg = React.memo(function ModelIconImg({
  model,
  provider,
  size = 22,
}: {
  model: string;
  provider?: string;
  size?: number;
}) {
  const url = useMemo(
    () => getModelIconUrl(model, provider),
    [model, provider],
  );
  if (!url) {
    return (
      <div
        className="flex items-center justify-center rounded-full bg-neutral-200 dark:bg-neutral-600"
        style={{ width: size, height: size }}
      >
        <span className="text-xs font-bold text-neutral-600 dark:text-neutral-200">
          {model.charAt(0).toUpperCase()}
        </span>
      </div>
    );
  }
  return (
    <div
      className="flex items-center justify-center rounded-full bg-neutral-50 dark:bg-neutral-300/70 dark:ring-1 dark:ring-[color-mix(in_srgb,var(--theme-bg-card)_10%,transparent)]"
      style={{ width: size, height: size }}
    >
      <img src={url} alt={model} width={size * 0.7} height={size * 0.7} />
    </div>
  );
});
