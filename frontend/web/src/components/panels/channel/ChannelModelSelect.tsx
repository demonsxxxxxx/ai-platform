/**
 * Model selector for channel configuration.
 * Fetches user's available models and renders a select dropdown.
 * Uses model.id (DB ID) so the backend resolves provider/key/config.
 */
import { useState, useEffect } from "react";
import { Cpu } from "lucide-react";
import { useTranslation } from "react-i18next";
import { modelApi } from "../../../services/api/model";
import type { ModelOption } from "../../../services/api/model";
import { GlassSelect } from "../../common/GlassSelect";

interface ChannelModelSelectProps {
  value: string | null | undefined;
  onChange: (modelId: string | null) => void;
}

export function ChannelModelSelect({
  value,
  onChange,
}: ChannelModelSelectProps) {
  const { t } = useTranslation();
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    modelApi
      .listAvailable()
      .then((res) => {
        setModels(res.models || []);
      })
      .catch(() => {
        setModels([]);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
        <div className="flex items-center gap-1.5">
          <Cpu size={14} />
          {t("channel.model", "Model")}
        </div>
      </label>
      <GlassSelect
        value={value || ""}
        onChange={(v) => onChange(v || null)}
        disabled={loading}
        placeholder={
          loading
            ? t("common.loading", "Loading...")
            : t("channel.defaultModel", "Default Model")
        }
        options={models.map((model) => ({
          value: model.id,
          label: `${model.label} (${model.value})`,
        }))}
      />
      <p className="text-xs text-stone-500 dark:text-stone-500">
        {t(
          "channel.modelHint",
          "Select which model this channel uses. Uses the model's configured provider and API key.",
        )}
      </p>
    </div>
  );
}
